from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

import pytest

from src.backtest import Bar, BacktestError, InMemoryMarketData, Instrument
from src.services.code_work_item_runner import CodeWorkItemRunner
from src.services.file_artifact_service import FileArtifactService
from src.services.python_execution_runtime import PythonExecutionRuntime
from src.services.quant_data_provider_service import QuantDataProviderService
from src.services.quant_factor_screening_service import QuantFactorScreeningService
from src.services.strategy_backtest_service import (
    EqualWeightSelectionPolicy,
    MarketDataTradingCalendar,
    SelectionOutputProfile,
    SelectionOutputTargetAdapter,
    StrategyToolBacktestService,
)
from src.services.strategy_run_service import (
    StrategyReference,
    StrategyRunResolver,
    StrategyRuntimeProfile,
)


TOOL_DEFINITION = (
    Path(__file__).parents[1]
    / "src"
    / "tools"
    / "definitions"
    / "quant_factor_screening.tool.json"
)


DATES = tuple(dt.date(2026, 7, day) for day in (21, 22, 23, 24, 27, 28, 29))
SYMBOLS = ("600001.SH", "000002.SZ", "000003.SZ")
CLOSES = {
    "600001.SH": (10, 12, 14, 16, 12, 10, 9),
    "000002.SZ": (10, 10, 10, 11, 15, 16, 17),
    "000003.SZ": (10, 9, 8, 7, 9, 20, 21),
}


def _market_data() -> InMemoryMarketData:
    bars: list[Bar] = []
    for symbol in SYMBOLS:
        for date, raw_close in zip(DATES, CLOSES[symbol]):
            close = Decimal(str(raw_close))
            bars.append(
                Bar(
                    date=date,
                    symbol=symbol,
                    open=close,
                    high=close * Decimal("1.02"),
                    low=close * Decimal("0.98"),
                    close=close,
                    volume=Decimal("1000000"),
                )
            )
    return InMemoryMarketData(
        bars,
        instruments=[Instrument(symbol=symbol, lot_size=1) for symbol in SYMBOLS],
        calendar=DATES,
        source_name="quant_factor_screening_fixture",
    )


def _daily_rows() -> list[dict[str, Any]]:
    return [
        {
            "stk_code": symbol,
            "trade_date": date.isoformat(),
            "close": raw_close,
            "amount": 1_000_000,
        }
        for symbol in SYMBOLS
        for date, raw_close in zip(DATES, CLOSES[symbol])
    ]


def _plan(data: InMemoryMarketData):
    resolver = StrategyRunResolver(calendar=MarketDataTradingCalendar(data))
    return resolver.resolve(
        {
            "strategy_ref": {
                "kind": "tool",
                "name": "quant_factor_screening",
                "version": "v1",
            },
            "parameters": {
                "user_text": "区间涨幅靠前的股票",
                "required_data": ["daily_price"],
                "top_n": 1,
            },
            "scope": {"targets": list(SYMBOLS)},
            "temporal": {
                "as_of": DATES[-1].isoformat(),
                "run_sessions": 4,
            },
        },
        runtime_profile=StrategyRuntimeProfile(
            entity_argument="universe",
            required_history_sessions=3,
            default_run_sessions=4,
        ),
    )


def _input_schema() -> Mapping[str, Any]:
    definition = json.loads(TOOL_DEFINITION.read_text(encoding="utf-8"))
    return definition["schemas"]["input"]


class _HistoricalQuantFactorHost:
    def __init__(self, service: QuantFactorScreeningService) -> None:
        self.service = service
        self.preflight_calls: list[StrategyReference] = []
        self.calls: list[dict[str, Any]] = []

    def preflight_historical_replay(
        self,
        *,
        strategy_ref: StrategyReference,
    ) -> Mapping[str, Any]:
        self.preflight_calls.append(strategy_ref)
        return {
            "authorized": True,
            "revision_pinned": True,
            "point_in_time_enforced": True,
            "asset_fingerprint": "sha256:quant-factor-screening-v1-fixture",
            "asset": f"{strategy_ref.name}@{strategy_ref.version}",
            "cutoff_policy": "provider date_range.end <= effective_as_of",
        }

    def invoke(
        self,
        *,
        strategy_ref: StrategyReference,
        arguments: Mapping[str, Any],
        runtime_context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        assert strategy_ref.name == "quant_factor_screening"
        strategy_run = dict(runtime_context["strategy_run"])
        effective_as_of = str(strategy_run["effective_as_of"])
        payload = dict(arguments)
        # This is the host guarantee under test: business arguments stay clean,
        # while every provider request is bounded by the child decision plan.
        payload["date_range"] = {
            "start": str(strategy_run["data_start"]),
            "end": effective_as_of,
        }
        self.calls.append(
            {
                "arguments": dict(arguments),
                "payload": payload,
                "effective_as_of": effective_as_of,
                "decision_date": runtime_context["strategy_backtest"]["decision_date"],
            }
        )
        return {
            "tool": strategy_ref.name,
            "ok": True,
            "data": self.service.run(payload),
            "error": "",
        }


def _quant_service(tmp_path: Path, provider_requests: list[dict[str, Any]]):
    artifact_service = FileArtifactService(data_root=tmp_path / "data")
    all_rows = _daily_rows()

    def execute(source, request):
        del source
        provider_requests.append(dict(request))
        return list(all_rows)

    provider = QuantDataProviderService(
        data_root=tmp_path / "data",
        file_artifact_service=artifact_service,
        query_executor=execute,
        source_status_overrides={"daily_price": "verified"},
    )
    runner = CodeWorkItemRunner(
        python_runtime=PythonExecutionRuntime(allow_unsafe_backends=True),
        runtime_root=str(tmp_path / "runs"),
        file_artifact_service=artifact_service,
    )
    return QuantFactorScreeningService(
        data_root=tmp_path / "data",
        provider_service=provider,
        code_runner=runner,
        file_artifact_service=artifact_service,
    )


def test_active_quant_selection_tool_runs_through_wrapper_and_backtest(tmp_path):
    data = _market_data()
    plan = _plan(data)
    provider_requests: list[dict[str, Any]] = []
    host = _HistoricalQuantFactorHost(_quant_service(tmp_path, provider_requests))
    events: list[dict[str, Any]] = []

    result = StrategyToolBacktestService().run(
        plan=plan,
        data=data,
        input_schema=_input_schema(),
        output_profile=SelectionOutputProfile(
            candidate_path="data.selected_stocks",
            symbol_field="stk_code",
            output_date_path="data.factor_plan.date_range.end",
        ),
        selection_policy=EqualWeightSelectionPolicy(top_n=1),
        invoker=host,
        initial_cash="100000",
        event_handler=events.append,
    )

    assert len(host.preflight_calls) == 1
    assert [item["effective_as_of"] for item in host.calls] == [
        DATES[3].isoformat(),
        DATES[4].isoformat(),
        DATES[5].isoformat(),
    ]
    assert [item["decision_date"] for item in host.calls] == [
        DATES[3].isoformat(),
        DATES[4].isoformat(),
        DATES[5].isoformat(),
    ]
    assert all(item["arguments"]["universe"] == list(SYMBOLS) for item in host.calls)
    assert all("date_range" not in item["arguments"] for item in host.calls)
    assert [request["date_range"]["end"] for request in provider_requests] == [
        DATES[3].isoformat(),
        DATES[4].isoformat(),
        DATES[5].isoformat(),
    ]

    assert [dict(item.target_weights) for item in result.backtest.decisions] == [
        {"600001.SH": Decimal("1")},
        {"000002.SZ": Decimal("1")},
        {"000003.SZ": Decimal("1")},
    ]
    assert [item.date for item in result.backtest.decisions] == list(DATES[3:6])
    assert {item.date for item in result.backtest.trades} == set(DATES[4:7])
    assert all(item.execute_on > item.date for item in result.backtest.decisions)
    assert all(
        item.evidence["selection_snapshot"]["as_of"] == item.date.isoformat()
        for item in result.backtest.decisions
    )
    assert all(
        item.evidence["parent_plan_hash"] == plan.plan_hash
        and item.evidence["decision_plan_hash"] != plan.plan_hash
        for item in result.backtest.decisions
    )
    assert result.to_dict()["protocol"] == "strategy_backtest_result.v1"
    assert result.execution_fingerprint.startswith("sha256:")
    assert [event["type"] for event in events[:3]] == [
        "strategy_backtest_preflight_started",
        "strategy_backtest_preflight_completed",
        "strategy_backtest_started",
    ]
    assert events[-1]["type"] == "strategy_backtest_completed"
    assert events[-1]["execution_fingerprint"] == result.execution_fingerprint


def test_historical_replay_fails_before_tool_call_without_host_guarantees():
    data = _market_data()
    plan = _plan(data)

    class UnverifiedInvoker:
        calls = 0

        def invoke(self, **kwargs):
            del kwargs
            self.calls += 1
            return {"ok": True, "data": {"selected_stocks": []}}

    invoker = UnverifiedInvoker()
    with pytest.raises(BacktestError) as caught:
        StrategyToolBacktestService().run(
            plan=plan,
            data=data,
            input_schema=_input_schema(),
            output_profile=SelectionOutputProfile(
                candidate_path="data.selected_stocks",
                symbol_field="stk_code",
            ),
            selection_policy=EqualWeightSelectionPolicy(top_n=1),
            invoker=invoker,
            initial_cash=1000,
        )

    assert caught.value.code == "historical_strategy_host_required"
    assert invoker.calls == 0


def test_historical_replay_rejects_dynamic_universe_without_timeline():
    data = _market_data()

    class UniverseResolver:
        def resolve(self, reference, *, as_of):
            from src.services.strategy_run_service import UniverseMembers

            return UniverseMembers(
                members=SYMBOLS,
                source="fixture.dynamic_universe",
                evidence={"as_of": as_of.isoformat(), "reference": dict(reference)},
            )

    plan = StrategyRunResolver(
        calendar=MarketDataTradingCalendar(data),
        universe_resolver=UniverseResolver(),
    ).resolve(
        {
            "strategy_ref": {
                "kind": "tool",
                "name": "quant_factor_screening",
                "version": "v1",
            },
            "parameters": {
                "user_text": "区间涨幅靠前的股票",
                "required_data": ["daily_price"],
                "top_n": 1,
            },
            "scope": {"universe_ref": {"type": "all_a_share"}},
            "temporal": {
                "as_of": DATES[-1].isoformat(),
                "run_sessions": 4,
            },
        },
        runtime_profile=StrategyRuntimeProfile(
            entity_argument="universe",
            required_history_sessions=3,
            default_run_sessions=4,
        ),
    )
    host = _FixedSelectionHost("600001.SH")

    with pytest.raises(BacktestError) as caught:
        StrategyToolBacktestService().run(
            plan=plan,
            data=data,
            input_schema=_input_schema(),
            output_profile=SelectionOutputProfile(
                candidate_path="data.selected_stocks",
                symbol_field="stk_code",
            ),
            selection_policy=EqualWeightSelectionPolicy(top_n=1),
            invoker=host,
            initial_cash=10000,
        )

    assert caught.value.code == "historical_universe_timeline_required"
    assert host.invoke_calls == 0


def test_selection_adapter_rejects_stale_future_or_out_of_universe_results():
    adapter = SelectionOutputTargetAdapter(
        SelectionOutputProfile(
            candidate_path="data.matches",
            symbol_field="code",
            output_date_path="data.as_of_date",
        )
    )

    def result(code: str, as_of_date: str):
        return {
            "ok": True,
            "summary": {"failed": 0},
            "items": [
                {
                    "status": "completed",
                    "result": {
                        "ok": True,
                        "data": {
                            "matches": [{"code": code}],
                            "as_of_date": as_of_date,
                        },
                    },
                }
            ],
        }

    with pytest.raises(BacktestError) as future:
        adapter.adapt(
            result("600001.SH", "2026-07-25"),
            as_of=dt.date(2026, 7, 24),
            universe=["600001.SH"],
        )
    with pytest.raises(BacktestError) as stale:
        adapter.adapt(
            result("600001.SH", "2026-07-23"),
            as_of=dt.date(2026, 7, 24),
            universe=["600001.SH"],
        )
    with pytest.raises(BacktestError) as outside:
        adapter.adapt(
            result("000999.SZ", "2026-07-24"),
            as_of=dt.date(2026, 7, 24),
            universe=["600001.SH"],
        )

    assert future.value.code == "selection_future_output"
    assert stale.value.code == "selection_stale_output"
    assert outside.value.code == "selection_outside_universe"


def test_successful_empty_selection_is_cash_but_tool_failure_is_not():
    adapter = SelectionOutputTargetAdapter(
        SelectionOutputProfile(
            candidate_path="data.selected_stocks",
            symbol_field="stk_code",
        )
    )
    snapshot = adapter.adapt(
        {
            "ok": True,
            "summary": {"failed": 0},
            "items": [
                {
                    "status": "completed",
                    "result": {"ok": True, "data": {"selected_stocks": []}},
                }
            ],
        },
        as_of=dt.date(2026, 7, 24),
        universe=["600001.SH"],
    )
    target = EqualWeightSelectionPolicy(top_n=1).to_target(snapshot)

    assert target.weights == {}
    with pytest.raises(BacktestError) as failed:
        adapter.adapt(
            {
                "ok": False,
                "summary": {"failed": 1},
                "items": [],
            },
            as_of=dt.date(2026, 7, 24),
            universe=["600001.SH"],
        )
    assert failed.value.code == "strategy_tool_failed"


def test_selection_adapter_rejects_implicit_cross_invocation_ranking():
    adapter = SelectionOutputTargetAdapter(
        SelectionOutputProfile(
            candidate_path="data.selected_stocks",
            symbol_field="stk_code",
        )
    )
    execution_result = {
        "ok": True,
        "items": [
            {
                "status": "completed",
                "result": {
                    "ok": True,
                    "data": {
                        "selected_stocks": [{"stk_code": "600001.SH", "score": 0.1}]
                    },
                },
            },
            {
                "status": "completed",
                "result": {
                    "ok": True,
                    "data": {
                        "selected_stocks": [{"stk_code": "000002.SZ", "score": 0.9}]
                    },
                },
            },
        ],
    }

    with pytest.raises(BacktestError) as caught:
        adapter.adapt(
            execution_result,
            as_of=dt.date(2026, 7, 24),
            universe=["600001.SH", "000002.SZ"],
        )

    assert caught.value.code == "unsupported_selection_aggregation"


def test_selection_adapter_rejects_multiple_ranked_blocks():
    adapter = SelectionOutputTargetAdapter(
        SelectionOutputProfile(
            candidate_path="data.*.items",
            symbol_field="stock_code",
        )
    )
    execution_result = {
        "ok": True,
        "items": [
            {
                "status": "completed",
                "result": {
                    "ok": True,
                    "data": [
                        {
                            "window": 4,
                            "items": [{"stock_code": "600001.SH", "rank": 1}],
                        },
                        {
                            "window": 5,
                            "items": [{"stock_code": "000002.SZ", "rank": 1}],
                        },
                    ],
                },
            }
        ],
    }

    with pytest.raises(BacktestError) as caught:
        adapter.adapt(
            execution_result,
            as_of=dt.date(2026, 7, 24),
            universe=["600001.SH", "000002.SZ"],
        )

    assert caught.value.code == "ambiguous_selection_output"


def test_equal_weight_policy_preserves_exact_total_for_repeating_decimals():
    symbols = tuple(f"{index:06d}.SZ" for index in range(1, 19))
    snapshot = SelectionOutputTargetAdapter(
        SelectionOutputProfile(
            candidate_path="data.selected_stocks",
            symbol_field="stk_code",
        )
    ).adapt(
        {
            "ok": True,
            "items": [
                {
                    "status": "completed",
                    "result": {
                        "ok": True,
                        "data": {
                            "selected_stocks": [
                                {"stk_code": symbol} for symbol in symbols
                            ]
                        },
                    },
                }
            ],
        },
        as_of=dt.date(2026, 7, 24),
        universe=symbols,
    )

    target = EqualWeightSelectionPolicy(top_n=18).to_target(snapshot)

    assert sum(target.weights.values(), Decimal("0")) == Decimal("1")
    assert len(target.weights) == 18


class _FixedSelectionHost:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.invoke_calls = 0

    def preflight_historical_replay(self, *, strategy_ref):
        return {
            "authorized": True,
            "revision_pinned": True,
            "point_in_time_enforced": True,
            "asset_fingerprint": "sha256:fixed-selection-host-v1",
            "asset": f"{strategy_ref.name}@{strategy_ref.version}",
        }

    def invoke(self, *, strategy_ref, arguments, runtime_context):
        del strategy_ref, arguments
        self.invoke_calls += 1
        as_of = runtime_context["strategy_run"]["effective_as_of"]
        return {
            "ok": True,
            "data": {
                "selected_stocks": [{"stk_code": self.symbol}],
                "as_of_date": as_of,
            },
        }


def test_bridge_fingerprint_includes_external_tool_decisions():
    data = _market_data()
    plan = _plan(data)
    kwargs = {
        "plan": plan,
        "data": data,
        "input_schema": _input_schema(),
        "output_profile": SelectionOutputProfile(
            candidate_path="data.selected_stocks",
            symbol_field="stk_code",
            output_date_path="data.as_of_date",
        ),
        "selection_policy": EqualWeightSelectionPolicy(top_n=1),
        "initial_cash": 10000,
    }

    first = StrategyToolBacktestService().run(
        **kwargs,
        invoker=_FixedSelectionHost("600001.SH"),
    )
    second = StrategyToolBacktestService().run(
        **kwargs,
        invoker=_FixedSelectionHost("000002.SZ"),
    )

    assert first.backtest.run_fingerprint == second.backtest.run_fingerprint
    assert first.execution_fingerprint != second.execution_fingerprint


def test_market_calendar_must_match_frozen_plan_inside_replay_window():
    plan_data = _market_data()
    plan = _plan(plan_data)
    injected_date = dt.date(2026, 7, 25)
    replay_data = InMemoryMarketData(
        [
            bar
            for symbol in plan_data.symbols
            for date in plan_data.calendar
            if (bar := plan_data.bar(symbol, date)) is not None
        ],
        instruments=[Instrument(symbol=symbol, lot_size=1) for symbol in SYMBOLS],
        calendar=(*DATES[:4], injected_date, *DATES[4:]),
        source_name="calendar_with_injected_session",
    )
    host = _FixedSelectionHost("600001.SH")

    with pytest.raises(BacktestError) as caught:
        StrategyToolBacktestService().run(
            plan=plan,
            data=replay_data,
            input_schema=_input_schema(),
            output_profile=SelectionOutputProfile(
                candidate_path="data.selected_stocks",
                symbol_field="stk_code",
            ),
            selection_policy=EqualWeightSelectionPolicy(top_n=1),
            invoker=host,
            initial_cash=10000,
        )

    assert caught.value.code == "market_data_window_mismatch"
    assert caught.value.details["unexpected_dates"] == [injected_date.isoformat()]
    assert host.invoke_calls == 0


def test_scalar_tool_is_rejected_before_parallel_invocations():
    data = _market_data()
    plan = StrategyRunResolver(calendar=MarketDataTradingCalendar(data)).resolve(
        {
            "strategy_ref": {
                "kind": "tool",
                "name": "scalar_factor",
                "version": "v1",
            },
            "parameters": {},
            "scope": {"targets": list(SYMBOLS)},
            "temporal": {"as_of": DATES[-1].isoformat(), "run_sessions": 4},
        },
        runtime_profile=StrategyRuntimeProfile(
            entity_argument="stock_code",
            required_history_sessions=3,
            default_run_sessions=4,
        ),
    )
    host = _FixedSelectionHost("600001.SH")

    with pytest.raises(BacktestError) as caught:
        StrategyToolBacktestService().run(
            plan=plan,
            data=data,
            input_schema={
                "type": "object",
                "properties": {"stock_code": {"type": "string"}},
            },
            output_profile=SelectionOutputProfile(
                candidate_path="data.selected_stocks",
                symbol_field="stk_code",
            ),
            selection_policy=EqualWeightSelectionPolicy(top_n=1),
            invoker=host,
            initial_cash=10000,
        )

    assert caught.value.code == "unsupported_selection_aggregation"
    assert host.invoke_calls == 0
