from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from src.backtest import (
    BacktestConfig,
    BacktestEngine,
    BacktestError,
    Bar,
    BpsExecutionModel,
    ExecutionQuote,
    FixedWeightStrategy,
    InMemoryMarketData,
    Instrument,
    MarketView,
    ScheduledTargetStrategy,
    TargetPortfolio,
    TopNMomentumStrategy,
)


D1 = dt.date(2026, 1, 5)
D2 = dt.date(2026, 1, 6)
D3 = dt.date(2026, 1, 7)
D4 = dt.date(2026, 1, 8)


def _bar(
    date,
    symbol,
    open_price,
    close_price=None,
    *,
    tradable=True,
    can_buy=True,
    can_sell=True,
    status_reason="",
):
    open_value = Decimal(str(open_price))
    close_value = Decimal(str(open_price if close_price is None else close_price))
    high = max(open_value, close_value) * Decimal("1.05")
    low = min(open_value, close_value) * Decimal("0.95")
    return Bar(
        date=date,
        symbol=symbol,
        open=open_value,
        high=high,
        low=low,
        close=close_value,
        tradable=tradable,
        can_buy=can_buy,
        can_sell=can_sell,
        status_reason=status_reason,
    )


def _data(bars, *, symbols=("AAA", "BBB"), lot_size=1, calendar=None):
    return InMemoryMarketData(
        bars,
        instruments=[
            Instrument(symbol=symbol, lot_size=lot_size)
            for symbol in symbols
        ],
        calendar=calendar,
    )


def test_fixed_portfolio_uses_next_open_and_reconciles_equity():
    data = _data(
        [
            _bar(D1, "AAA", 10, 10),
            _bar(D1, "BBB", 20, 20),
            _bar(D2, "AAA", 10, 12),
            _bar(D2, "BBB", 20, 19),
            _bar(D3, "AAA", 12, 13),
            _bar(D3, "BBB", 19, 21),
        ]
    )

    result = BacktestEngine().run(
        data=data,
        strategy=FixedWeightStrategy({"AAA": "0.5", "BBB": "0.5"}),
        config=BacktestConfig(universe=["BBB", "AAA"], initial_cash="10000"),
    )

    assert [(item.symbol, item.quantity) for item in result.orders] == [
        ("AAA", 500),
        ("BBB", 250),
    ]
    assert all(item.signal_date == D1 and item.execution_date == D2 for item in result.orders)
    assert all(item.date == D2 for item in result.trades)
    assert result.daily_snapshots[-1].cash == Decimal("0")
    assert result.daily_snapshots[-1].equity == Decimal("11750")
    assert result.metrics.total_return == pytest.approx(0.175)
    assert result.metrics.trade_count == 2


def test_order_quantity_is_frozen_from_signal_close_not_future_open():
    data = _data(
        [
            _bar(D1, "AAA", 10, 10),
            _bar(D2, "AAA", 8, 8.5),
        ],
        symbols=("AAA",),
    )

    result = BacktestEngine().run(
        data=data,
        strategy=FixedWeightStrategy({"AAA": 1}),
        config=BacktestConfig(universe=["AAA"], initial_cash=1000),
    )

    assert result.orders[0].reference_price == Decimal("10")
    assert result.orders[0].quantity == 100
    assert result.trades[0].market_price == Decimal("8")
    assert result.trades[0].filled_quantity == 100
    assert result.daily_snapshots[-1].cash == Decimal("200")


def test_future_close_change_does_not_change_prior_dynamic_decision_or_order():
    common = [
        _bar(D1, "AAA", 10, 10),
        _bar(D1, "BBB", 10, 10),
        _bar(D2, "AAA", 11, 11),
        _bar(D2, "BBB", 9, 9),
    ]
    run_a = BacktestEngine().run(
        data=_data(common + [_bar(D3, "AAA", 11, 12), _bar(D3, "BBB", 9, 8)]),
        strategy=TopNMomentumStrategy(lookback_days=1, top_n=1),
        config=BacktestConfig(universe=["AAA", "BBB"], initial_cash=1000),
    )
    run_b = BacktestEngine().run(
        data=_data(common + [_bar(D3, "AAA", 11, 200), _bar(D3, "BBB", 9, 300)]),
        strategy=TopNMomentumStrategy(lookback_days=1, top_n=1),
        config=BacktestConfig(universe=["AAA", "BBB"], initial_cash=1000),
    )

    assert run_a.decisions == run_b.decisions
    assert run_a.orders == run_b.orders
    assert run_a.decisions[0].date == D2
    assert run_a.decisions[0].target_weights == {"AAA": Decimal("1")}
    assert run_a.run_fingerprint != run_b.run_fingerprint


def test_market_view_rejects_future_access():
    data = _data(
        [_bar(D1, "AAA", 10), _bar(D2, "AAA", 11)],
        symbols=("AAA",),
    )
    view = MarketView(data, current_date=D1, universe=["AAA"])

    with pytest.raises(BacktestError) as caught:
        view.bar("AAA", D2)

    assert caught.value.code == "future_data_access"
    assert view.history("AAA") == ((D1, Decimal("10")),)


def test_scheduled_target_rotates_by_selling_before_buying():
    data = _data(
        [
            _bar(D1, "AAA", 10),
            _bar(D1, "BBB", 10),
            _bar(D2, "AAA", 10),
            _bar(D2, "BBB", 10),
            _bar(D3, "AAA", 10),
            _bar(D3, "BBB", 10),
        ]
    )
    strategy = ScheduledTargetStrategy(
        {
            D1: {"AAA": 1},
            D2: {"BBB": 1},
        }
    )

    result = BacktestEngine().run(
        data=data,
        strategy=strategy,
        config=BacktestConfig(universe=["AAA", "BBB"], initial_cash=1000),
    )

    second_decision_orders = [
        item for item in result.orders if item.signal_date == D2
    ]
    assert [(item.side, item.symbol, item.quantity) for item in second_decision_orders] == [
        ("SELL", "AAA", 100),
        ("BUY", "BBB", 100),
    ]
    d3_trades = [item for item in result.trades if item.date == D3]
    assert [(item.side, item.symbol) for item in d3_trades] == [
        ("SELL", "AAA"),
        ("BUY", "BBB"),
    ]
    assert [(item.symbol, item.quantity) for item in result.daily_snapshots[-1].positions] == [
        ("BBB", 100)
    ]


def test_fee_tax_slippage_and_liquidation_reconcile_exactly():
    data = _data(
        [
            _bar(D1, "AAA", 10, 10),
            _bar(D2, "AAA", 10, 10.1),
            _bar(D3, "AAA", 10, 10),
        ],
        symbols=("AAA",),
    )
    execution = BpsExecutionModel(
        commission_rate="0.001",
        minimum_commission="5",
        sell_tax_rate="0.001",
        slippage_rate="0.01",
    )

    result = BacktestEngine().run(
        data=data,
        strategy=ScheduledTargetStrategy({D1: {"AAA": "0.5"}, D2: {}}),
        config=BacktestConfig(universe=["AAA"], initial_cash=10000),
        execution_model=execution,
    )

    buy, sell = result.trades
    assert buy.fill_price == Decimal("10.10")
    assert buy.commission == Decimal("5.05000")
    assert sell.fill_price == Decimal("9.90")
    assert sell.commission == Decimal("5")
    assert sell.tax == Decimal("4.95000")
    assert result.daily_snapshots[-1].cash == Decimal("9885.00000")
    assert result.daily_snapshots[-1].positions == ()
    assert result.metrics.total_commission == Decimal("10.05000")
    assert result.metrics.total_tax == Decimal("4.95000")
    assert result.metrics.total_slippage_cost == Decimal("100.00")


def test_shared_cash_reduces_all_buys_proportionally_and_is_input_order_independent():
    bars = [
        _bar(D1, "AAA", 10, 10),
        _bar(D1, "BBB", 20, 20),
        _bar(D2, "AAA", 20, 20),
        _bar(D2, "BBB", 40, 40),
    ]

    def run(target):
        return BacktestEngine().run(
            data=_data(list(reversed(bars))),
            strategy=FixedWeightStrategy(target),
            config=BacktestConfig(universe=list(reversed(target)), initial_cash=1000),
        )

    first = run({"AAA": "0.5", "BBB": "0.5"})
    second = run({"BBB": "0.5", "AAA": "0.5"})

    assert [(item.symbol, item.filled_quantity) for item in first.trades] == [
        ("AAA", 26),
        ("BBB", 12),
    ]
    assert first.trades == second.trades
    assert first.daily_snapshots == second.daily_snapshots
    assert [item.code for item in first.issues].count("cash_constrained") == 2
    assert first.daily_snapshots[-1].cash == Decimal("0")


def test_nontradable_day_order_expires_and_is_not_implicitly_retried():
    data = _data(
        [
            _bar(D1, "AAA", 10),
            _bar(D2, "AAA", 10, tradable=False, status_reason="suspended"),
            _bar(D3, "AAA", 10),
        ],
        symbols=("AAA",),
    )

    result = BacktestEngine().run(
        data=data,
        strategy=FixedWeightStrategy({"AAA": 1}),
        config=BacktestConfig(universe=["AAA"], initial_cash=1000),
    )

    assert len(result.orders) == 1
    assert result.trades == ()
    assert [item.code for item in result.issues] == ["not_tradable"]
    assert all(item.cash == Decimal("1000") for item in result.daily_snapshots)


def test_missing_close_for_held_position_uses_disclosed_stale_mark():
    data = _data(
        [
            _bar(D1, "AAA", 10),
            _bar(D2, "AAA", 10, 11),
        ],
        symbols=("AAA",),
        calendar=[D1, D2, D3],
    )

    result = BacktestEngine().run(
        data=data,
        strategy=FixedWeightStrategy({"AAA": 1}),
        config=BacktestConfig(universe=["AAA"], initial_cash=1000),
    )

    assert result.daily_snapshots[-1].positions[0].mark_price == Decimal("11")
    assert result.daily_snapshots[-1].positions[0].mark_is_stale is True
    assert any(item.code == "stale_mark" and item.date == D3 for item in result.issues)


def test_target_validation_does_not_normalize_or_invent_weights():
    target = TargetPortfolio({"AAA": "0.6"})
    assert target.weights == {"AAA": Decimal("0.6")}

    with pytest.raises(BacktestError) as exceeds:
        TargetPortfolio({"AAA": "0.7", "BBB": "0.4"})
    with pytest.raises(BacktestError) as negative:
        TargetPortfolio({"AAA": "-0.1"})

    assert exceeds.value.code == "target_weight_exceeds_one"
    assert negative.value.code == "negative_target_weight"


def test_strategy_target_outside_universe_is_rejected():
    data = _data(
        [_bar(D1, "AAA", 10), _bar(D2, "AAA", 10)],
        symbols=("AAA",),
    )

    with pytest.raises(BacktestError) as caught:
        BacktestEngine().run(
            data=data,
            strategy=FixedWeightStrategy({"BBB": 1}),
            config=BacktestConfig(universe=["AAA"], initial_cash=1000),
        )

    assert caught.value.code == "target_outside_universe"


def test_same_inputs_produce_identical_result_and_fingerprint():
    data = _data(
        [
            _bar(D1, "AAA", 10),
            _bar(D2, "AAA", 10, 11),
            _bar(D3, "AAA", 11, 12),
        ],
        symbols=("AAA",),
    )
    strategy = FixedWeightStrategy({"AAA": "0.8"})
    config = BacktestConfig(universe=["AAA"], initial_cash=1000)

    first = BacktestEngine().run(data=data, strategy=strategy, config=config)
    second = BacktestEngine().run(data=data, strategy=strategy, config=config)

    assert first.run_fingerprint == second.run_fingerprint
    assert first.to_dict() == second.to_dict()


def test_next_open_fill_does_not_depend_on_later_session_high_or_low():
    d1 = _bar(D1, "AAA", 10, 10)

    def execution_bar(high):
        return Bar(
            date=D2,
            symbol="AAA",
            open=Decimal("10"),
            high=Decimal(str(high)),
            low=Decimal("9"),
            close=Decimal("10"),
        )

    def run(high):
        return BacktestEngine().run(
            data=_data([d1, execution_bar(high)], symbols=("AAA",)),
            strategy=FixedWeightStrategy({"AAA": 1}),
            config=BacktestConfig(universe=["AAA"], initial_cash=1000),
            execution_model=BpsExecutionModel(slippage_rate="0.2"),
        )

    narrow_range = run(10)
    wide_range = run(20)

    assert narrow_range.trades[0].fill_price == Decimal("12.0")
    assert wide_range.trades[0].fill_price == Decimal("12.0")
    assert narrow_range.trades[0].filled_quantity == wide_range.trades[0].filled_quantity


def test_missing_positive_target_bar_aborts_complete_rebalance():
    data = _data(
        [
            _bar(D1, "AAA", 10),
            _bar(D1, "BBB", 10),
            _bar(D2, "AAA", 10),
            _bar(D2, "BBB", 10),
            _bar(D3, "BBB", 10),
            _bar(D4, "AAA", 10),
            _bar(D4, "BBB", 10),
        ],
        calendar=[D1, D2, D3, D4],
    )
    strategy = ScheduledTargetStrategy({D1: {"BBB": 1}, D3: {"AAA": 1}})

    result = BacktestEngine().run(
        data=data,
        strategy=strategy,
        config=BacktestConfig(universe=["AAA", "BBB"], initial_cash=1000),
    )

    assert len(result.decisions) == 2
    assert [(item.side, item.symbol) for item in result.orders] == [("BUY", "BBB")]
    assert [(item.symbol, item.quantity) for item in result.daily_snapshots[-1].positions] == [
        ("BBB", 100)
    ]
    assert any(
        item.code == "missing_reference_bar" and item.symbol == "AAA"
        for item in result.issues
    )


def test_largest_remainder_uses_one_affordable_lot_instead_of_zeroing_all_buys():
    data = _data(
        [
            _bar(D1, "AAA", 50),
            _bar(D1, "BBB", 50),
            _bar(D2, "AAA", 60),
            _bar(D2, "BBB", 60),
        ],
        lot_size=100,
    )

    result = BacktestEngine().run(
        data=data,
        strategy=FixedWeightStrategy({"AAA": "0.5", "BBB": "0.5"}),
        config=BacktestConfig(universe=["BBB", "AAA"], initial_cash=10000),
    )

    assert len(result.trades) == 1
    assert result.trades[0].symbol == "AAA"
    assert result.trades[0].filled_quantity == 100
    assert result.daily_snapshots[-1].cash == Decimal("4000")


def test_invalid_execution_model_cannot_create_cash_or_assets():
    class BrokenExecutionModel:
        def quote(self, order, bar, *, quantity):
            gross = bar.open * Decimal(quantity)
            return ExecutionQuote(
                market_price=bar.open,
                fill_price=bar.open,
                gross_value=gross,
                commission=Decimal("0"),
                tax=Decimal("0"),
                slippage_cost=Decimal("0"),
                cash_delta=gross,
            )

        def describe(self):
            return {"type": "broken"}

    data = _data(
        [_bar(D1, "AAA", 1), _bar(D2, "AAA", 1)],
        symbols=("AAA",),
    )

    with pytest.raises(BacktestError) as caught:
        BacktestEngine().run(
            data=data,
            strategy=FixedWeightStrategy({"AAA": 1}),
            config=BacktestConfig(universe=["AAA"], initial_cash=100),
            execution_model=BrokenExecutionModel(),
        )

    assert caught.value.code == "invalid_execution_quote"


def test_positive_target_below_one_lot_is_disclosed():
    data = _data(
        [_bar(D1, "AAA", 10), _bar(D2, "AAA", 10)],
        symbols=("AAA",),
        lot_size=100,
    )

    result = BacktestEngine().run(
        data=data,
        strategy=FixedWeightStrategy({"AAA": 1}),
        config=BacktestConfig(universe=["AAA"], initial_cash=500),
    )

    assert result.orders == ()
    assert [item.code for item in result.issues] == ["target_below_lot"]


def test_momentum_requires_complete_current_session_history():
    data = _data(
        [
            _bar(D1, "AAA", 10),
            _bar(D1, "BBB", 10),
            _bar(D2, "AAA", 10),
            _bar(D2, "BBB", 100),
            _bar(D3, "AAA", 11),
            _bar(D4, "AAA", 11),
            _bar(D4, "BBB", 100),
        ],
        calendar=[D1, D2, D3, D4],
    )

    result = BacktestEngine().run(
        data=data,
        strategy=TopNMomentumStrategy(
            lookback_days=1,
            top_n=1,
            rebalance_every=2,
        ),
        config=BacktestConfig(universe=["AAA", "BBB"], initial_cash=1000),
    )

    assert result.decisions[0].date == D3
    assert result.decisions[0].target_weights == {"AAA": Decimal("1")}


def test_integer_contracts_reject_fractional_values_and_target_is_read_only():
    with pytest.raises(BacktestError) as lot_error:
        Instrument("AAA", lot_size=100.9)
    with pytest.raises(BacktestError) as interval_error:
        FixedWeightStrategy({"AAA": 1}, rebalance_every=1.5)

    target = TargetPortfolio({"AAA": "0.5"})
    with pytest.raises(TypeError):
        target.weights["AAA"] = Decimal("2")

    assert lot_error.value.code == "invalid_integer"
    assert interval_error.value.code == "invalid_integer"


@pytest.mark.parametrize(
    "calendar",
    [
        (D2, D1, D3),
        (D1, D2, D2, D3),
    ],
)
def test_engine_rejects_non_monotonic_market_calendar(calendar):
    base = _data(
        [_bar(D1, "AAA", 10), _bar(D2, "AAA", 10), _bar(D3, "AAA", 10)],
        symbols=("AAA",),
    )

    class CalendarOverride:
        symbols = base.symbols

        @property
        def calendar(self):
            return calendar

        def instrument(self, symbol):
            return base.instrument(symbol)

        def bar(self, symbol, date):
            return base.bar(symbol, date)

        def fingerprint(self):
            return base.fingerprint()

    with pytest.raises(BacktestError) as caught:
        BacktestEngine().run(
            data=CalendarOverride(),
            strategy=FixedWeightStrategy({"AAA": 1}),
            config=BacktestConfig(universe=["AAA"], initial_cash=1000),
        )

    assert caught.value.code == "invalid_market_calendar"


def test_execution_status_fields_require_real_booleans():
    with pytest.raises(BacktestError) as caught:
        Bar(
            date=D1,
            symbol="AAA",
            open=10,
            high=10,
            low=10,
            close=10,
            tradable="false",
        )

    assert caught.value.code == "invalid_boolean"


def test_duplicate_instrument_is_rejected_instead_of_order_dependent_override():
    with pytest.raises(BacktestError) as caught:
        InMemoryMarketData(
            [_bar(D1, "AAA", 10)],
            instruments=[
                Instrument("AAA", lot_size=1),
                Instrument("AAA", lot_size=100),
            ],
        )

    assert caught.value.code == "duplicate_instrument"


def test_result_evidence_is_deterministically_json_serializable():
    data = _data(
        [_bar(D1, "AAA", 10), _bar(D2, "AAA", 10)],
        symbols=("AAA",),
    )
    target = TargetPortfolio(
        {"AAA": "0.5"},
        evidence={"source_ids": {"source_b", "source_a"}},
    )

    result = BacktestEngine().run(
        data=data,
        strategy=ScheduledTargetStrategy({D1: target}),
        config=BacktestConfig(universe=["AAA"], initial_cash=1000),
    )

    payload = result.to_dict()
    assert payload["decisions"][0]["evidence"]["source_ids"] == [
        "source_a",
        "source_b",
    ]
