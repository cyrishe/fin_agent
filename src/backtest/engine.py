from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, Sequence

from .contracts import (
    ENGINE_VERSION,
    ONE,
    ZERO,
    BacktestConfig,
    BacktestError,
    BacktestResult,
    Bar,
    DecisionRecord,
    ExecutionIssue,
    Order,
    PortfolioSnapshot,
    PositionSnapshot,
    TargetPortfolio,
    Trade,
    as_decimal,
    json_value,
)
from .data import MarketData, MarketView
from .execution import BpsExecutionModel, ExecutionModel, ExecutionQuote
from .metrics import calculate_metrics
from .strategy import Strategy


@dataclass
class _Position:
    quantity: int
    average_cost: Decimal


class _Ledger:
    def __init__(self, initial_cash: Decimal) -> None:
        self.cash = initial_cash
        self.positions: dict[str, _Position] = {}
        self.realized_pnl = ZERO
        self.last_marks: dict[str, Decimal] = {}

    def apply_trade(self, trade: Trade) -> None:
        position = self.positions.get(trade.symbol, _Position(0, ZERO))
        if trade.side == "BUY":
            new_quantity = position.quantity + trade.filled_quantity
            total_cost_basis = (
                position.average_cost * Decimal(position.quantity)
                + trade.gross_value
                + trade.commission
                + trade.tax
            )
            self.positions[trade.symbol] = _Position(
                quantity=new_quantity,
                average_cost=total_cost_basis / Decimal(new_quantity),
            )
        elif trade.side == "SELL":
            if trade.filled_quantity > position.quantity:
                raise BacktestError(
                    "ledger_invariant_failed",
                    "sell fill exceeds held quantity",
                    details={
                        "symbol": trade.symbol,
                        "held_quantity": position.quantity,
                        "filled_quantity": trade.filled_quantity,
                    },
                )
            self.realized_pnl += (
                trade.cash_delta
                - position.average_cost * Decimal(trade.filled_quantity)
            )
            remaining = position.quantity - trade.filled_quantity
            if remaining:
                self.positions[trade.symbol] = _Position(
                    quantity=remaining,
                    average_cost=position.average_cost,
                )
            else:
                self.positions.pop(trade.symbol, None)
        else:
            raise BacktestError("ledger_invariant_failed", f"unknown trade side: {trade.side}")

        self.cash += trade.cash_delta
        if self.cash < ZERO:
            raise BacktestError(
                "ledger_invariant_failed",
                "cash became negative after a fill",
                details={"cash": str(self.cash), "trade_id": trade.trade_id},
            )

    def snapshot(
        self,
        date: dt.date,
        data: MarketData,
        issues: list[ExecutionIssue],
    ) -> PortfolioSnapshot:
        marked: list[tuple[str, _Position, Decimal, bool]] = []
        for symbol, position in sorted(self.positions.items()):
            bar = data.bar(symbol, date)
            if bar is not None:
                mark = bar.close
                mark_is_stale = False
                self.last_marks[symbol] = mark
            else:
                stale_mark = self.last_marks.get(symbol)
                if stale_mark is None:
                    raise BacktestError(
                        "missing_position_mark",
                        "held position has no current or prior valid close",
                        details={"date": date.isoformat(), "symbol": symbol},
                    )
                mark = stale_mark
                mark_is_stale = True
                issues.append(
                    ExecutionIssue(
                        date=date,
                        code="stale_mark",
                        symbol=symbol,
                        message="position valued with its last known close because the current bar is missing",
                        details={"mark_price": str(mark)},
                    )
                )
            marked.append((symbol, position, mark, mark_is_stale))

        market_value = sum(
            (mark * Decimal(position.quantity) for _, position, mark, _ in marked),
            ZERO,
        )
        equity = self.cash + market_value
        if equity <= ZERO:
            raise BacktestError(
                "ledger_invariant_failed",
                "portfolio equity must remain positive",
                details={"date": date.isoformat(), "equity": str(equity)},
            )
        positions = tuple(
            PositionSnapshot(
                symbol=symbol,
                quantity=position.quantity,
                average_cost=position.average_cost,
                mark_price=mark,
                market_value=mark * Decimal(position.quantity),
                actual_weight=mark * Decimal(position.quantity) / equity,
                mark_is_stale=mark_is_stale,
            )
            for symbol, position, mark, mark_is_stale in marked
        )
        return PortfolioSnapshot(
            date=date,
            cash=self.cash,
            equity=equity,
            positions=positions,
            realized_pnl=self.realized_pnl,
        )


class BacktestEngine:
    """Sequential daily-bar portfolio simulator with point-in-time strategy views."""

    ASSUMPTIONS = (
        "daily bars; decisions are made after close and execute at the next trading-session open",
        "order quantities are frozen using signal-day close prices and equity",
        "long-only and no leverage; target weight below 1 leaves the balance in cash",
        "orders are DAY orders and expire after one execution attempt without implicit retry",
        "shared-cash buy orders are proportionally scaled and rounded down to instrument lot size",
        "missing close for a held position uses the last known mark and is disclosed",
        "corporate actions, dividends, shorts, margin and intraday orders are not modeled",
    )

    def run(
        self,
        *,
        data: MarketData,
        strategy: Strategy,
        config: BacktestConfig,
        execution_model: ExecutionModel | None = None,
    ) -> BacktestResult:
        execution = execution_model or BpsExecutionModel()
        calendar = tuple(data.calendar)
        if (
            not calendar
            or any(
                not isinstance(date, dt.date) or isinstance(date, dt.datetime)
                for date in calendar
            )
            or any(current <= previous for previous, current in zip(calendar, calendar[1:]))
        ):
            raise BacktestError(
                "invalid_market_calendar",
                "market calendar must contain unique dates in strictly increasing order",
            )
        sessions = tuple(
            date
            for date in calendar
            if (config.start_date is None or date >= config.start_date)
            and (config.end_date is None or date <= config.end_date)
        )
        if len(sessions) < 2:
            raise BacktestError(
                "insufficient_sessions",
                "backtest requires at least two trading sessions",
                details={"session_count": len(sessions)},
            )
        unknown_symbols = sorted(set(config.universe).difference(data.symbols))
        if unknown_symbols:
            raise BacktestError(
                "unknown_symbol",
                "backtest universe contains symbols absent from market data",
                details={"symbols": unknown_symbols},
            )

        strategy.reset()
        ledger = _Ledger(config.initial_cash)
        decisions: list[DecisionRecord] = []
        orders: list[Order] = []
        trades: list[Trade] = []
        issues: list[ExecutionIssue] = []
        snapshots: list[PortfolioSnapshot] = []
        pending: dict[dt.date, list[Order]] = {}
        next_order_number = 1
        next_trade_number = 1

        for session_index, session in enumerate(sessions):
            session_orders = pending.pop(session, [])
            if session_orders:
                session_trades, next_trade_number = self._execute_orders(
                    date=session,
                    orders=session_orders,
                    data=data,
                    ledger=ledger,
                    execution=execution,
                    issues=issues,
                    next_trade_number=next_trade_number,
                )
                trades.extend(session_trades)

            snapshot = ledger.snapshot(session, data, issues)
            snapshots.append(snapshot)
            if session_index == len(sessions) - 1:
                continue

            view = MarketView(data, current_date=session, universe=config.universe)
            target = strategy.on_close(view, snapshot)
            if target is None:
                continue
            if not isinstance(target, TargetPortfolio):
                raise BacktestError(
                    "invalid_strategy_output",
                    "strategy must return TargetPortfolio or None",
                )
            outside_universe = sorted(set(target.weights).difference(config.universe))
            if outside_universe:
                raise BacktestError(
                    "target_outside_universe",
                    "strategy target contains symbols outside the configured universe",
                    details={"symbols": outside_universe},
                )

            execution_date = sessions[session_index + 1]
            decision_id = f"dec_{len(decisions) + 1:06d}"
            decision = DecisionRecord(
                decision_id=decision_id,
                date=session,
                execute_on=execution_date,
                data_cutoff=f"{session.isoformat()}:session_close",
                target_weights=dict(target.weights),
                reason=target.reason,
                evidence=dict(target.evidence),
            )
            decisions.append(decision)
            planned, next_order_number = self._plan_orders(
                decision=decision,
                target=target,
                snapshot=snapshot,
                data=data,
                issues=issues,
                next_order_number=next_order_number,
            )
            if planned:
                orders.extend(planned)
                pending.setdefault(execution_date, []).extend(planned)

        metrics = calculate_metrics(
            snapshots,
            trades,
            annualization_days=config.annualization_days,
            risk_free_rate=config.risk_free_rate,
        )
        strategy_description = dict(strategy.describe())
        execution_description = dict(execution.describe())
        data_fingerprint = data.fingerprint()
        run_fingerprint = self._run_fingerprint(
            config=config,
            strategy=strategy_description,
            execution_model=execution_description,
            data_fingerprint=data_fingerprint,
        )
        return BacktestResult(
            engine_version=ENGINE_VERSION,
            run_fingerprint=run_fingerprint,
            data_fingerprint=data_fingerprint,
            strategy=strategy_description,
            execution_model=execution_description,
            config=config,
            assumptions=self.ASSUMPTIONS,
            decisions=tuple(decisions),
            orders=tuple(orders),
            trades=tuple(trades),
            issues=tuple(issues),
            daily_snapshots=tuple(snapshots),
            metrics=metrics,
        )

    def _plan_orders(
        self,
        *,
        decision: DecisionRecord,
        target: TargetPortfolio,
        snapshot: PortfolioSnapshot,
        data: MarketData,
        issues: list[ExecutionIssue],
        next_order_number: int,
    ) -> tuple[list[Order], int]:
        current_positions = {position.symbol: position for position in snapshot.positions}
        missing_reference_symbols = sorted(
            symbol
            for symbol, weight in target.weights.items()
            if weight > ZERO and data.bar(symbol, decision.date) is None
        )
        if missing_reference_symbols:
            for symbol in missing_reference_symbols:
                issues.append(
                    ExecutionIssue(
                        date=decision.date,
                        code="missing_reference_bar",
                        symbol=symbol,
                        message=(
                            "the complete target was not planned because a positive-weight "
                            "symbol lacks a signal-day bar"
                        ),
                        details={"decision_id": decision.decision_id},
                    )
                )
            return [], next_order_number

        planned: list[Order] = []
        symbols = sorted(set(current_positions).union(target.weights))
        for symbol in symbols:
            current_position = current_positions.get(symbol)
            current_quantity = 0 if current_position is None else current_position.quantity
            target_weight = target.weights.get(symbol, ZERO)
            bar = data.bar(symbol, decision.date)
            if target_weight > ZERO:
                assert bar is not None
                reference_price = bar.close
                lot_size = data.instrument(symbol).lot_size
                raw_quantity = target_weight * snapshot.equity / reference_price
                desired_quantity = int(raw_quantity // Decimal(lot_size)) * lot_size
                if desired_quantity == 0:
                    issues.append(
                        ExecutionIssue(
                            date=decision.date,
                            code="target_below_lot",
                            symbol=symbol,
                            message="positive target weight is below one executable lot",
                            details={
                                "decision_id": decision.decision_id,
                                "lot_size": lot_size,
                                "target_weight": str(target_weight),
                            },
                        )
                    )
            else:
                desired_quantity = 0
                reference_price = (
                    bar.close
                    if bar is not None
                    else current_position.mark_price
                    if current_position is not None
                    else ZERO
                )
                lot_size = data.instrument(symbol).lot_size

            delta = desired_quantity - current_quantity
            if delta == 0:
                continue
            side = "BUY" if delta > 0 else "SELL"
            quantity = abs(delta)
            if quantity % lot_size:
                if side == "SELL" and quantity == current_quantity:
                    pass
                else:
                    quantity = quantity // lot_size * lot_size
            if quantity <= 0:
                issues.append(
                    ExecutionIssue(
                        date=decision.date,
                        code="lot_rounding_no_order",
                        symbol=symbol,
                        message="target difference was smaller than the instrument lot size",
                        details={
                            "decision_id": decision.decision_id,
                            "lot_size": lot_size,
                            "raw_delta": abs(delta),
                        },
                    )
                )
                continue
            order = Order(
                order_id=f"ord_{next_order_number:06d}",
                decision_id=decision.decision_id,
                signal_date=decision.date,
                execution_date=decision.execute_on,
                symbol=symbol,
                side=side,
                quantity=quantity,
                reference_price=reference_price,
                target_weight=target_weight,
            )
            next_order_number += 1
            planned.append(order)
        return sorted(planned, key=lambda item: (item.side != "SELL", item.symbol)), next_order_number

    def _execute_orders(
        self,
        *,
        date: dt.date,
        orders: Sequence[Order],
        data: MarketData,
        ledger: _Ledger,
        execution: ExecutionModel,
        issues: list[ExecutionIssue],
        next_trade_number: int,
    ) -> tuple[list[Trade], int]:
        trades: list[Trade] = []
        sell_orders = sorted((item for item in orders if item.side == "SELL"), key=lambda item: item.symbol)
        buy_orders = sorted((item for item in orders if item.side == "BUY"), key=lambda item: item.symbol)

        for order in sell_orders:
            bar = self._executable_bar(order, data, date, issues)
            if bar is None:
                continue
            held = ledger.positions.get(order.symbol, _Position(0, ZERO)).quantity
            if order.quantity > held:
                raise BacktestError(
                    "ledger_invariant_failed",
                    "planned sell quantity exceeds position at execution",
                    details={
                        "order_id": order.order_id,
                        "held_quantity": held,
                        "order_quantity": order.quantity,
                    },
                )
            quote = self._validated_quote(
                execution,
                order,
                bar,
                quantity=order.quantity,
            )
            trade = self._trade_from_quote(
                order=order,
                date=date,
                quantity=order.quantity,
                quote=quote,
                trade_number=next_trade_number,
            )
            next_trade_number += 1
            ledger.apply_trade(trade)
            trades.append(trade)

        executable_buys: list[tuple[Order, Bar]] = []
        for order in buy_orders:
            bar = self._executable_bar(order, data, date, issues)
            if bar is not None:
                executable_buys.append((order, bar))
        fill_quantities = self._allocate_buying_cash(
            executable_buys,
            available_cash=ledger.cash,
            data=data,
            execution=execution,
        )
        for order, bar in executable_buys:
            quantity = fill_quantities.get(order.order_id, 0)
            if quantity < order.quantity:
                issues.append(
                    ExecutionIssue(
                        date=date,
                        code="cash_constrained",
                        symbol=order.symbol,
                        order_id=order.order_id,
                        message="buy order was proportionally reduced to keep shared cash non-negative",
                        details={
                            "requested_quantity": order.quantity,
                            "filled_quantity": quantity,
                        },
                    )
                )
            if quantity <= 0:
                continue
            quote = self._validated_quote(
                execution,
                order,
                bar,
                quantity=quantity,
            )
            trade = self._trade_from_quote(
                order=order,
                date=date,
                quantity=quantity,
                quote=quote,
                trade_number=next_trade_number,
            )
            next_trade_number += 1
            ledger.apply_trade(trade)
            trades.append(trade)
        return trades, next_trade_number

    def _allocate_buying_cash(
        self,
        executable_orders: Sequence[tuple[Order, Bar]],
        *,
        available_cash: Decimal,
        data: MarketData,
        execution: ExecutionModel,
    ) -> dict[str, int]:
        if not executable_orders or available_cash <= ZERO:
            return {order.order_id: 0 for order, _ in executable_orders}

        def quantities_at(scale: Decimal) -> dict[str, int]:
            quantities: dict[str, int] = {}
            for order, _ in executable_orders:
                lot_size = data.instrument(order.symbol).lot_size
                scaled = Decimal(order.quantity) * scale
                quantities[order.order_id] = int(scaled // Decimal(lot_size)) * lot_size
            return quantities

        def total_cost(quantities: Mapping[str, int]) -> Decimal:
            return sum(
                (
                    -self._validated_quote(
                        execution,
                        order,
                        bar,
                        quantity=quantity,
                    ).cash_delta
                    for order, bar in executable_orders
                    if (quantity := quantities.get(order.order_id, 0)) > 0
                ),
                ZERO,
            )

        full_quantities = {order.order_id: order.quantity for order, _ in executable_orders}
        if total_cost(full_quantities) <= available_cash:
            return full_quantities

        low = ZERO
        high = ONE
        best = {order.order_id: 0 for order, _ in executable_orders}
        for _ in range(80):
            midpoint = (low + high) / Decimal("2")
            candidate = quantities_at(midpoint)
            if total_cost(candidate) <= available_cash:
                low = midpoint
                best = candidate
            else:
                high = midpoint

        # Largest-remainder allocation keeps the proportional base while using
        # indivisible lots when cash can fund them. Each order receives at most
        # one residual lot, so input order cannot become a hidden cash priority.
        remainder_candidates: list[tuple[Decimal, str, Order]] = []
        for order, _ in executable_orders:
            lot_size = data.instrument(order.symbol).lot_size
            scaled_lots = Decimal(order.quantity) * low / Decimal(lot_size)
            remainder = scaled_lots - scaled_lots.to_integral_value(rounding="ROUND_FLOOR")
            if best[order.order_id] + lot_size <= order.quantity:
                remainder_candidates.append((remainder, order.symbol, order))
        for _, _, order in sorted(
            remainder_candidates,
            key=lambda item: (-item[0], item[1]),
        ):
            lot_size = data.instrument(order.symbol).lot_size
            candidate = dict(best)
            candidate[order.order_id] += lot_size
            if total_cost(candidate) <= available_cash:
                best = candidate
        return best

    def _executable_bar(
        self,
        order: Order,
        data: MarketData,
        date: dt.date,
        issues: list[ExecutionIssue],
    ) -> Bar | None:
        bar = data.bar(order.symbol, date)
        if bar is None:
            issues.append(
                ExecutionIssue(
                    date=date,
                    code="missing_bar",
                    symbol=order.symbol,
                    order_id=order.order_id,
                    message="DAY order expired because the execution-session bar is missing",
                )
            )
            return None
        if not bar.tradable:
            issues.append(
                ExecutionIssue(
                    date=date,
                    code="not_tradable",
                    symbol=order.symbol,
                    order_id=order.order_id,
                    message="DAY order expired because the instrument was not tradable",
                    details={"status_reason": bar.status_reason},
                )
            )
            return None
        if order.side == "BUY" and not bar.can_buy:
            code = "buy_blocked"
        elif order.side == "SELL" and not bar.can_sell:
            code = "sell_blocked"
        else:
            return bar
        issues.append(
            ExecutionIssue(
                date=date,
                code=code,
                symbol=order.symbol,
                order_id=order.order_id,
                message=f"DAY order expired because {order.side.lower()} execution was blocked",
                details={"status_reason": bar.status_reason},
            )
        )
        return None

    def _trade_from_quote(
        self,
        *,
        order: Order,
        date: dt.date,
        quantity: int,
        quote: ExecutionQuote,
        trade_number: int,
    ) -> Trade:
        return Trade(
            trade_id=f"trd_{trade_number:06d}",
            order_id=order.order_id,
            date=date,
            symbol=order.symbol,
            side=order.side,
            requested_quantity=order.quantity,
            filled_quantity=quantity,
            market_price=quote.market_price,
            fill_price=quote.fill_price,
            gross_value=quote.gross_value,
            commission=quote.commission,
            tax=quote.tax,
            slippage_cost=quote.slippage_cost,
            cash_delta=quote.cash_delta,
        )

    def _validated_quote(
        self,
        execution: ExecutionModel,
        order: Order,
        bar: Bar,
        *,
        quantity: int,
    ) -> ExecutionQuote:
        raw = execution.quote(order, bar, quantity=quantity)
        if not isinstance(raw, ExecutionQuote):
            raise BacktestError(
                "invalid_execution_quote",
                "execution model must return ExecutionQuote",
            )
        values = {
            field_name: as_decimal(getattr(raw, field_name), field_name=f"quote.{field_name}")
            for field_name in (
                "market_price",
                "fill_price",
                "gross_value",
                "commission",
                "tax",
                "slippage_cost",
                "cash_delta",
            )
        }
        if values["market_price"] != bar.open:
            raise BacktestError(
                "invalid_execution_quote",
                "next-open execution quote must identify the session open as market_price",
            )
        if values["fill_price"] <= ZERO or values["gross_value"] <= ZERO:
            raise BacktestError(
                "invalid_execution_quote",
                "execution prices and gross value must be positive",
            )
        for field_name in ("commission", "tax", "slippage_cost"):
            if values[field_name] < ZERO:
                raise BacktestError(
                    "invalid_execution_quote",
                    f"quote.{field_name} must not be negative",
                )
        expected_gross = values["fill_price"] * Decimal(quantity)
        expected_slippage = (
            abs(values["fill_price"] - values["market_price"]) * Decimal(quantity)
        )
        expected_cash_delta = (
            -(expected_gross + values["commission"] + values["tax"])
            if order.side == "BUY"
            else expected_gross - values["commission"] - values["tax"]
        )
        if values["gross_value"] != expected_gross:
            raise BacktestError(
                "invalid_execution_quote",
                "gross_value must equal fill_price multiplied by quantity",
            )
        if values["slippage_cost"] != expected_slippage:
            raise BacktestError(
                "invalid_execution_quote",
                "slippage_cost must reconcile to market and fill prices",
            )
        if values["cash_delta"] != expected_cash_delta:
            raise BacktestError(
                "invalid_execution_quote",
                "cash_delta does not satisfy the trade accounting identity",
            )
        return ExecutionQuote(**values)

    def _run_fingerprint(
        self,
        *,
        config: BacktestConfig,
        strategy: Mapping[str, Any],
        execution_model: Mapping[str, Any],
        data_fingerprint: str,
    ) -> str:
        payload = {
            "engine_version": ENGINE_VERSION,
            "config": {
                "universe": list(config.universe),
                "initial_cash": str(config.initial_cash),
                "start_date": None if config.start_date is None else config.start_date.isoformat(),
                "end_date": None if config.end_date is None else config.end_date.isoformat(),
                "annualization_days": config.annualization_days,
                "risk_free_rate": str(config.risk_free_rate),
            },
            "strategy": strategy,
            "execution_model": execution_model,
            "data_fingerprint": data_fingerprint,
        }
        raw = json.dumps(
            json_value(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
