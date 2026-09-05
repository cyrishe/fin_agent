from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol, Sequence

from src.backtest.contracts import (
    BacktestConfig,
    BacktestError,
    BacktestResult,
    PortfolioSnapshot,
    TargetPortfolio,
    as_date,
    as_decimal,
    as_positive_int,
)
from src.backtest.data import MarketData, MarketView
from src.backtest.engine import BacktestEngine
from src.backtest.execution import ExecutionModel
from src.services.strategy_run_service import (
    ResolvedStrategyRunPlan,
    StrategyInvocationAdapter,
    StrategyInvokerPort,
    StrategyReference,
    StrategyRunError,
    StrategyRunExecutor,
    StrategyRunResolver,
)


STRATEGY_BACKTEST_PROTOCOL = "strategy_backtest_result.v1"
SELECTION_SNAPSHOT_PROTOCOL = "selection_snapshot.v1"


def _trim(value: Any) -> str:
    return str(value or "").strip()


def _fingerprint(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _emit(
    handler: Callable[[Mapping[str, Any]], None] | None,
    event: Mapping[str, Any],
) -> None:
    if handler is None:
        return
    try:
        handler(dict(event))
    except Exception:
        return


@dataclass(frozen=True)
class SelectionOutputProfile:
    """System-owned mapping from one Tool result to ranked security rows.

    The path syntax is intentionally small: dot-separated object keys and ``*``
    for expanding one object or array level.  It is executable metadata saved
    with a strategy revision, not a heuristic search over arbitrary output.
    """

    candidate_path: str
    symbol_field: str
    output_date_path: str = ""

    def __post_init__(self) -> None:
        candidate_path = _trim(self.candidate_path)
        symbol_field = _trim(self.symbol_field)
        output_date_path = _trim(self.output_date_path)
        if not candidate_path or any(not part for part in candidate_path.split(".")):
            raise BacktestError(
                "invalid_selection_profile",
                "candidate_path must be a non-empty dot path",
            )
        if "[" in candidate_path or "]" in candidate_path:
            raise BacktestError(
                "invalid_selection_profile",
                "candidate_path uses dot-separated object fields only; "
                "array indexes are not supported",
            )
        if not symbol_field or "." in symbol_field or symbol_field == "*":
            raise BacktestError(
                "invalid_selection_profile",
                "symbol_field must be one result-row field",
            )
        if output_date_path and any(not part for part in output_date_path.split(".")):
            raise BacktestError(
                "invalid_selection_profile",
                "output_date_path must be an empty value or a dot path",
            )
        if "[" in output_date_path or "]" in output_date_path:
            raise BacktestError(
                "invalid_selection_profile",
                "output_date_path must resolve to one scalar date without "
                "array indexes",
            )
        object.__setattr__(self, "candidate_path", candidate_path)
        object.__setattr__(self, "symbol_field", symbol_field)
        object.__setattr__(self, "output_date_path", output_date_path)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_path": self.candidate_path,
            "symbol_field": self.symbol_field,
            "output_date_path": self.output_date_path,
        }


@dataclass(frozen=True)
class SelectionSnapshot:
    as_of: dt.date
    ranked_symbols: tuple[str, ...]
    result_fingerprint: str
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        parsed_as_of = as_date(self.as_of, field_name="selection.as_of")
        assert parsed_as_of is not None
        normalized: list[str] = []
        seen: set[str] = set()
        for raw_symbol in self.ranked_symbols:
            symbol = _trim(raw_symbol).upper()
            if not symbol:
                raise BacktestError(
                    "invalid_selection_symbol",
                    "selection result contains an empty symbol",
                )
            if symbol not in seen:
                seen.add(symbol)
                normalized.append(symbol)
        fingerprint = _trim(self.result_fingerprint)
        if not fingerprint:
            raise BacktestError(
                "invalid_selection_fingerprint",
                "selection result fingerprint is required",
            )
        object.__setattr__(self, "as_of", parsed_as_of)
        object.__setattr__(self, "ranked_symbols", tuple(normalized))
        object.__setattr__(self, "result_fingerprint", fingerprint)
        object.__setattr__(
            self,
            "evidence",
            MappingProxyType(dict(self.evidence or {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": SELECTION_SNAPSHOT_PROTOCOL,
            "as_of": self.as_of.isoformat(),
            "ranked_symbols": list(self.ranked_symbols),
            "result_fingerprint": self.result_fingerprint,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class EqualWeightSelectionPolicy:
    """Portfolio policy kept outside the user-authored selection Tool."""

    top_n: int
    rebalance_every: int = 1
    target_exposure: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "top_n",
            as_positive_int(self.top_n, field_name="selection_policy.top_n"),
        )
        object.__setattr__(
            self,
            "rebalance_every",
            as_positive_int(
                self.rebalance_every,
                field_name="selection_policy.rebalance_every",
            ),
        )
        exposure = as_decimal(
            self.target_exposure,
            field_name="selection_policy.target_exposure",
        )
        if exposure <= Decimal("0") or exposure > Decimal("1"):
            raise BacktestError(
                "invalid_target_exposure",
                "selection target exposure must be greater than 0 and at most 1",
            )
        object.__setattr__(self, "target_exposure", exposure)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "equal_weight_selection",
            "top_n": self.top_n,
            "rebalance_every": self.rebalance_every,
            "target_exposure": str(self.target_exposure),
            "empty_selection": "complete_empty_target",
        }

    def to_target(
        self,
        snapshot: SelectionSnapshot,
        *,
        evidence: Mapping[str, Any] | None = None,
    ) -> TargetPortfolio:
        selected = snapshot.ranked_symbols[: self.top_n]
        weights: dict[str, Decimal] = {}
        if selected:
            weight = self.target_exposure / Decimal(len(selected))
            weights = {symbol: weight for symbol in selected[:-1]}
            allocated = sum(weights.values(), Decimal("0"))
            weights[selected[-1]] = self.target_exposure - allocated
        return TargetPortfolio(
            weights=weights,
            reason=(
                f"top {len(selected)} equal-weight selections"
                if selected
                else "successful empty selection; target cash"
            ),
            evidence={
                "selection_snapshot": snapshot.to_dict(),
                "selected_symbols": list(selected),
                "selection_policy": self.to_dict(),
                **dict(evidence or {}),
            },
        )


class SelectionOutputTargetAdapter:
    """Strictly adapt successful Tool output; never guess rows or symbols."""

    def __init__(self, profile: SelectionOutputProfile) -> None:
        self.profile = profile

    def adapt(
        self,
        execution_result: Mapping[str, Any],
        *,
        as_of: dt.date,
        universe: Sequence[str],
    ) -> SelectionSnapshot:
        if not isinstance(execution_result, Mapping):
            raise BacktestError(
                "invalid_strategy_run_result",
                "strategy wrapper result must be an object",
            )
        if execution_result.get("ok") is not True:
            raw_summary = execution_result.get("summary")
            raise BacktestError(
                "strategy_tool_failed",
                "selection Tool did not complete every invocation",
                details={
                    "summary": dict(raw_summary)
                    if isinstance(raw_summary, Mapping)
                    else {}
                },
            )
        raw_items = execution_result.get("items")
        if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
            raise BacktestError(
                "invalid_strategy_run_result",
                "strategy wrapper result items must be an array",
            )
        if len(raw_items) != 1:
            raise BacktestError(
                "unsupported_selection_aggregation",
                "selection backtest requires one native ranked-result invocation",
                details={"invocation_count": len(raw_items)},
            )

        allowed = {_trim(symbol).upper() for symbol in universe if _trim(symbol)}
        ranked: list[str] = []
        seen: set[str] = set()
        raw_results: list[Any] = []
        output_dates: list[str] = []
        for item in raw_items:
            if not isinstance(item, Mapping) or item.get("status") != "completed":
                raise BacktestError(
                    "strategy_tool_failed",
                    "selection Tool invocation failed",
                    details={"item": dict(item) if isinstance(item, Mapping) else {}},
                )
            raw_result = item.get("result")
            if not isinstance(raw_result, Mapping):
                raise BacktestError(
                    "invalid_selection_output",
                    "selection Tool result must be an object",
                )
            if raw_result.get("ok") is False:
                raise BacktestError(
                    "strategy_tool_failed",
                    _trim(raw_result.get("error"))
                    or "selection Tool returned ok=false",
                )
            raw_results.append(raw_result)
            if self.profile.output_date_path:
                output_date = self._output_date(raw_result)
                if output_date > as_of:
                    raise BacktestError(
                        "selection_future_output",
                        "selection Tool returned data after the decision cutoff",
                        details={
                            "decision_date": as_of.isoformat(),
                            "output_date": output_date.isoformat(),
                        },
                    )
                if output_date < as_of:
                    raise BacktestError(
                        "selection_stale_output",
                        "selection Tool returned data before the decision cutoff",
                        details={
                            "decision_date": as_of.isoformat(),
                            "output_date": output_date.isoformat(),
                        },
                    )
                output_dates.append(output_date.isoformat())

            for row in self._candidate_rows(raw_result):
                raw_symbol = row.get(self.profile.symbol_field)
                symbol = _trim(raw_symbol).upper()
                if not symbol:
                    raise BacktestError(
                        "selection_symbol_missing",
                        "selection row does not contain a non-empty symbol",
                        details={"symbol_field": self.profile.symbol_field},
                    )
                if symbol not in allowed:
                    raise BacktestError(
                        "selection_outside_universe",
                        "selection Tool returned a symbol outside the frozen universe",
                        details={"symbol": symbol},
                    )
                if symbol not in seen:
                    seen.add(symbol)
                    ranked.append(symbol)

        fingerprint_payload = {
            "as_of": as_of.isoformat(),
            "profile": self.profile.to_dict(),
            "results": raw_results,
            "ranked_symbols": ranked,
        }
        return SelectionSnapshot(
            as_of=as_of,
            ranked_symbols=tuple(ranked),
            result_fingerprint=_fingerprint(fingerprint_payload),
            evidence={
                "invocation_count": len(raw_items),
                "output_dates": output_dates,
                "output_profile": self.profile.to_dict(),
            },
        )

    def _candidate_rows(self, raw_result: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        values = self._resolve_path(raw_result, self.profile.candidate_path)
        if len(values) != 1:
            raise BacktestError(
                "ambiguous_selection_output",
                "candidate_path must resolve to exactly one ranked collection",
                details={
                    "candidate_path": self.profile.candidate_path,
                    "collection_count": len(values),
                },
            )
        rows: list[Mapping[str, Any]] = []
        for value in values:
            if isinstance(value, Mapping):
                rows.append(value)
            elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                for row in value:
                    if not isinstance(row, Mapping):
                        raise BacktestError(
                            "invalid_selection_output",
                            "candidate collection must contain objects",
                        )
                    rows.append(row)
            else:
                raise BacktestError(
                    "invalid_selection_output",
                    "candidate_path must resolve to objects or arrays of objects",
                )
        return rows

    def _output_date(self, raw_result: Mapping[str, Any]) -> dt.date:
        values = self._resolve_path(raw_result, self.profile.output_date_path)
        if len(values) != 1:
            raise BacktestError(
                "invalid_selection_output_date",
                "output_date_path must resolve to exactly one date",
            )
        parsed = as_date(values[0], field_name="selection.output_date")
        if parsed is None:
            raise BacktestError(
                "invalid_selection_output_date",
                "selection output date is required",
            )
        return parsed

    @staticmethod
    def _resolve_path(value: Any, path: str) -> list[Any]:
        values = [value]
        for segment in path.split("."):
            resolved: list[Any] = []
            for current in values:
                if segment == "*":
                    if isinstance(current, Mapping):
                        resolved.extend(current.values())
                    elif isinstance(current, Sequence) and not isinstance(
                        current, (str, bytes)
                    ):
                        resolved.extend(current)
                    else:
                        raise BacktestError(
                            "invalid_selection_output",
                            "selection wildcard must address an object or array",
                            details={"path": path},
                        )
                elif isinstance(current, Mapping) and segment in current:
                    resolved.append(current[segment])
                else:
                    raise BacktestError(
                        "selection_output_path_missing",
                        "configured selection output path is missing",
                        details={"path": path, "segment": segment},
                    )
            values = resolved
        return values


class HistoricalStrategyInvoker(StrategyInvokerPort, Protocol):
    def preflight_historical_replay(
        self,
        *,
        strategy_ref: StrategyReference,
    ) -> Mapping[str, Any]: ...


class MarketDataTradingCalendar:
    """Trading-calendar port constrained to the already loaded replay data."""

    def __init__(self, data: MarketData) -> None:
        self.data = data

    def latest_completed_session(
        self,
        *,
        market_code: str,
        now: dt.datetime,
    ) -> dt.date:
        del market_code, now
        return self.data.calendar[-1]

    def recent_sessions(
        self,
        *,
        market_code: str,
        end_on_or_before: dt.date,
        count: int,
    ) -> tuple[dt.date, ...]:
        del market_code
        requested = as_positive_int(count, field_name="calendar.count")
        eligible = tuple(
            date for date in self.data.calendar if date <= end_on_or_before
        )
        return eligible[-requested:]


class ToolSelectionBacktestStrategy:
    """Run one selection Tool at each sequential backtest decision cutoff."""

    def __init__(
        self,
        *,
        parent_plan: ResolvedStrategyRunPlan,
        data: MarketData,
        input_schema: Mapping[str, Any],
        output_profile: SelectionOutputProfile,
        policy: EqualWeightSelectionPolicy,
        invoker: HistoricalStrategyInvoker,
        max_concurrency: int = 4,
        event_handler: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> None:
        self.parent_plan = parent_plan
        self.input_schema = dict(input_schema or {})
        self.output_profile = output_profile
        self.policy = policy
        self.invoker = invoker
        self.max_concurrency = as_positive_int(
            max_concurrency,
            field_name="max_concurrency",
        )
        self.event_handler = event_handler
        self._calendar = MarketDataTradingCalendar(data)
        self._resolver = StrategyRunResolver(calendar=self._calendar)
        self._adapter = StrategyInvocationAdapter()
        self._output_adapter = SelectionOutputTargetAdapter(output_profile)
        self._decision_count = 0

    def reset(self) -> None:
        self._decision_count = 0

    def on_close(
        self,
        market: MarketView,
        portfolio: PortfolioSnapshot,
    ) -> TargetPortfolio | None:
        del portfolio
        should_rebalance = self._decision_count % self.policy.rebalance_every == 0
        self._decision_count += 1
        if not should_rebalance:
            return None

        try:
            child_plan = self._resolver.resolve(
                {
                    "strategy_ref": self.parent_plan.strategy_ref.to_dict(),
                    "parameters": self.parent_plan.to_dict()["parameters"],
                    "scope": {"targets": list(self.parent_plan.universe.members)},
                    "temporal": {
                        "as_of": market.current_date.isoformat(),
                        "run_sessions": 1,
                    },
                },
                runtime_profile=self.parent_plan.runtime_profile,
            )
            prepared = self._adapter.prepare(
                child_plan,
                input_schema=self.input_schema,
            )
            if len(prepared.invocations) != 1:
                raise BacktestError(
                    "unsupported_selection_aggregation",
                    "selection backtest requires one native ranked-result invocation",
                    details={"invocation_count": len(prepared.invocations)},
                )
            execution_result = StrategyRunExecutor(
                invoker=self.invoker,
                max_concurrency=self.max_concurrency,
            ).execute(
                prepared,
                runtime_context={
                    "strategy_backtest": {
                        "parent_plan_hash": self.parent_plan.plan_hash,
                        "decision_date": market.current_date.isoformat(),
                    }
                },
                event_handler=self._decision_event_handler(market.current_date),
            )
        except StrategyRunError as exc:
            raise BacktestError(
                "strategy_wrapper_failed",
                exc.message,
                details={"strategy_error_code": exc.code, **exc.details},
            ) from exc

        snapshot = self._output_adapter.adapt(
            execution_result,
            as_of=market.current_date,
            universe=self.parent_plan.universe.members,
        )
        target = self.policy.to_target(
            snapshot,
            evidence={
                "parent_plan_hash": self.parent_plan.plan_hash,
                "decision_plan_hash": child_plan.plan_hash,
                "decision_date": market.current_date.isoformat(),
            },
        )
        _emit(
            self.event_handler,
            {
                "type": "strategy_backtest_decision_completed",
                "decision_date": market.current_date.isoformat(),
                "candidate_count": len(snapshot.ranked_symbols),
                "selected_count": len(target.weights),
                "decision_plan_hash": child_plan.plan_hash,
            },
        )
        return target

    def describe(self) -> Mapping[str, Any]:
        return {
            "type": "tool_selection_backtest",
            "strategy_ref": self.parent_plan.strategy_ref.to_dict(),
            "parent_plan_hash": self.parent_plan.plan_hash,
            "output_profile": self.output_profile.to_dict(),
            "selection_policy": self.policy.to_dict(),
        }

    def _decision_event_handler(
        self,
        decision_date: dt.date,
    ) -> Callable[[Mapping[str, Any]], None] | None:
        if self.event_handler is None:
            return None

        def forward(event: Mapping[str, Any]) -> None:
            _emit(
                self.event_handler,
                {
                    **dict(event),
                    "backtest_decision_date": decision_date.isoformat(),
                },
            )

        return forward


@dataclass(frozen=True)
class StrategyToolBacktestResult:
    plan: ResolvedStrategyRunPlan
    output_profile: SelectionOutputProfile
    selection_policy: EqualWeightSelectionPolicy
    host_preflight: Mapping[str, Any]
    backtest: BacktestResult
    execution_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        host_preflight = dict(self.host_preflight or {})
        object.__setattr__(
            self,
            "host_preflight",
            MappingProxyType(host_preflight),
        )
        selection_fingerprints: list[str] = []
        for decision in self.backtest.decisions:
            raw_snapshot = decision.evidence.get("selection_snapshot")
            snapshot = raw_snapshot if isinstance(raw_snapshot, Mapping) else {}
            selection_fingerprints.append(_trim(snapshot.get("result_fingerprint")))
        object.__setattr__(
            self,
            "execution_fingerprint",
            _fingerprint(
                {
                    "protocol": STRATEGY_BACKTEST_PROTOCOL,
                    "plan_hash": self.plan.plan_hash,
                    "engine_run_fingerprint": self.backtest.run_fingerprint,
                    "asset_fingerprint": host_preflight.get("asset_fingerprint"),
                    "selection_fingerprints": selection_fingerprints,
                }
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": STRATEGY_BACKTEST_PROTOCOL,
            "plan": self.plan.to_dict(),
            "output_profile": self.output_profile.to_dict(),
            "selection_policy": self.selection_policy.to_dict(),
            "host_preflight": dict(self.host_preflight),
            "execution_fingerprint": self.execution_fingerprint,
            "backtest": self.backtest.to_dict(),
        }


class StrategyToolBacktestService:
    """Isolated Tool -> selection snapshot -> portfolio backtest bridge."""

    _REQUIRED_HOST_GUARANTEES = (
        "authorized",
        "revision_pinned",
        "point_in_time_enforced",
    )

    def __init__(self, *, engine: BacktestEngine | None = None) -> None:
        self.engine = engine or BacktestEngine()

    def run(
        self,
        *,
        plan: ResolvedStrategyRunPlan,
        data: MarketData,
        input_schema: Mapping[str, Any],
        output_profile: SelectionOutputProfile,
        selection_policy: EqualWeightSelectionPolicy,
        invoker: HistoricalStrategyInvoker,
        initial_cash: Decimal | int | float | str,
        execution_model: ExecutionModel | None = None,
        max_concurrency: int = 4,
        event_handler: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> StrategyToolBacktestResult:
        self._validate_replay_scope(plan=plan)
        _emit(
            event_handler,
            {
                "type": "strategy_backtest_preflight_started",
                "plan_hash": plan.plan_hash,
            },
        )
        preflight = self._preflight(plan=plan, invoker=invoker)
        _emit(
            event_handler,
            {
                "type": "strategy_backtest_preflight_completed",
                "plan_hash": plan.plan_hash,
            },
        )
        self._validate_market_data(plan=plan, data=data)
        _emit(
            event_handler,
            {
                "type": "strategy_backtest_started",
                "plan_hash": plan.plan_hash,
                "run_start": plan.window.run_start.isoformat(),
                "run_end": plan.window.run_end.isoformat(),
                "universe_count": len(plan.universe.members),
            },
        )
        strategy = ToolSelectionBacktestStrategy(
            parent_plan=plan,
            data=data,
            input_schema=input_schema,
            output_profile=output_profile,
            policy=selection_policy,
            invoker=invoker,
            max_concurrency=max_concurrency,
            event_handler=event_handler,
        )
        result = self.engine.run(
            data=data,
            strategy=strategy,
            config=BacktestConfig(
                universe=plan.universe.members,
                initial_cash=as_decimal(initial_cash, field_name="initial_cash"),
                start_date=plan.window.run_start,
                end_date=plan.window.run_end,
            ),
            execution_model=execution_model,
        )
        final_result = StrategyToolBacktestResult(
            plan=plan,
            output_profile=output_profile,
            selection_policy=selection_policy,
            host_preflight=preflight,
            backtest=result,
        )
        _emit(
            event_handler,
            {
                "type": "strategy_backtest_completed",
                "plan_hash": plan.plan_hash,
                "decision_count": len(result.decisions),
                "trade_count": len(result.trades),
                "engine_run_fingerprint": result.run_fingerprint,
                "execution_fingerprint": final_result.execution_fingerprint,
            },
        )
        return final_result

    @staticmethod
    def _validate_replay_scope(*, plan: ResolvedStrategyRunPlan) -> None:
        reference_type = _trim(plan.universe.reference.get("type"))
        if reference_type == "explicit_targets":
            return
        raise BacktestError(
            "historical_universe_timeline_required",
            "historical strategy replay currently requires explicit targets",
            details={
                "universe_reference": plan.universe.to_dict()["reference"],
                "reason": (
                    "dynamic universes must be resolved for every decision date "
                    "before portfolio simulation"
                ),
            },
        )

    def _preflight(
        self,
        *,
        plan: ResolvedStrategyRunPlan,
        invoker: HistoricalStrategyInvoker,
    ) -> dict[str, Any]:
        preflight = getattr(invoker, "preflight_historical_replay", None)
        if not callable(preflight):
            raise BacktestError(
                "historical_strategy_host_required",
                "historical replay requires an authorized, revision-pinned, point-in-time host",
                details={"missing_guarantees": list(self._REQUIRED_HOST_GUARANTEES)},
            )
        try:
            evidence = preflight(strategy_ref=plan.strategy_ref)
        except BacktestError:
            raise
        except Exception as exc:
            raise BacktestError(
                "historical_host_preflight_failed",
                "historical strategy host preflight failed",
                details={"error": str(exc)},
            ) from exc
        if not isinstance(evidence, Mapping):
            raise BacktestError(
                "invalid_historical_host_evidence",
                "historical strategy host preflight must return an object",
            )
        missing = [
            field
            for field in self._REQUIRED_HOST_GUARANTEES
            if evidence.get(field) is not True
        ]
        if missing:
            raise BacktestError(
                "historical_strategy_host_required",
                "historical strategy host did not prove required guarantees",
                details={"missing_guarantees": missing},
            )
        asset_fingerprint = _trim(evidence.get("asset_fingerprint"))
        if not asset_fingerprint:
            raise BacktestError(
                "historical_strategy_host_required",
                "historical strategy host did not identify the pinned asset",
                details={"missing_guarantees": ["asset_fingerprint"]},
            )
        return dict(evidence)

    @staticmethod
    def _validate_market_data(
        *,
        plan: ResolvedStrategyRunPlan,
        data: MarketData,
    ) -> None:
        expected_dates = (
            plan.window.warmup_session_dates + plan.window.run_session_dates
        )
        actual_dates = tuple(
            date
            for date in data.calendar
            if plan.window.data_start <= date <= plan.window.run_end
        )
        missing_symbols = sorted(set(plan.universe.members).difference(data.symbols))
        if actual_dates != expected_dates or missing_symbols:
            expected_set = set(expected_dates)
            actual_set = set(actual_dates)
            raise BacktestError(
                "market_data_window_mismatch",
                "market data does not match the frozen strategy run plan",
                details={
                    "missing_dates": [
                        date.isoformat()
                        for date in sorted(expected_set.difference(actual_set))
                    ],
                    "unexpected_dates": [
                        date.isoformat()
                        for date in sorted(actual_set.difference(expected_set))
                    ],
                    "missing_symbols": missing_symbols,
                },
            )


__all__ = [
    "EqualWeightSelectionPolicy",
    "HistoricalStrategyInvoker",
    "MarketDataTradingCalendar",
    "SelectionOutputProfile",
    "SelectionOutputTargetAdapter",
    "SelectionSnapshot",
    "StrategyToolBacktestResult",
    "StrategyToolBacktestService",
    "ToolSelectionBacktestStrategy",
]
