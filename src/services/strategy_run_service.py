from __future__ import annotations

import datetime as dt
import hashlib
import json
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol, Sequence
from zoneinfo import ZoneInfo


STRATEGY_RUN_PROTOCOL = "strategy_run.v1"
STRATEGY_RUNTIME_PROFILE_PROTOCOL = "strategy_runtime_profile.v1"
DEFAULT_MARKET_CODE = "CN_A"


class StrategyRunError(ValueError):
    """Stable failure raised before user strategy code is executed."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.details = dict(details or {})


def _trim(value: Any) -> str:
    return str(value or "").strip()


def _parse_date(value: Any, *, field_name: str) -> dt.date | None:
    if value in (None, ""):
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(_trim(value))
    except (TypeError, ValueError) as exc:
        raise StrategyRunError(
            "invalid_date",
            f"{field_name} must be an ISO date",
            details={"field": field_name, "value": _trim(value)},
        ) from exc


def _positive_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise StrategyRunError(
            "invalid_integer", f"{field_name} must be a positive integer"
        )
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise StrategyRunError(
            "invalid_integer", f"{field_name} must be a positive integer"
        ) from exc
    if not number.is_finite() or number != number.to_integral_value() or number <= 0:
        raise StrategyRunError(
            "invalid_integer", f"{field_name} must be a positive integer"
        )
    return int(number)


def _non_negative_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise StrategyRunError(
            "invalid_integer", f"{field_name} must be a non-negative integer"
        )
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise StrategyRunError(
            "invalid_integer", f"{field_name} must be a non-negative integer"
        ) from exc
    if not number.is_finite() or number != number.to_integral_value() or number < 0:
        raise StrategyRunError(
            "invalid_integer", f"{field_name} must be a non-negative integer"
        )
    return int(number)


def _deep_freeze(value: Any) -> Any:
    """Detach JSON-like values from callers and make nested containers read-only."""

    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _deep_freeze(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(
            sorted(
                (_deep_freeze(item) for item in value),
                key=lambda item: json.dumps(
                    item, ensure_ascii=False, sort_keys=True, default=str
                ),
            )
        )
    return deepcopy(value)


def _deep_thaw(value: Any) -> Any:
    """Return detached JSON-compatible containers from frozen plan state."""

    if isinstance(value, Mapping):
        return {str(key): _deep_thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_deep_thaw(item) for item in value]
    return deepcopy(value)


def _frozen_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    frozen = _deep_freeze(value or {})
    if not isinstance(frozen, Mapping):  # Defensive; the public type is a Mapping.
        raise StrategyRunError("invalid_mapping", "value must be an object")
    return frozen


def _json_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    thawed = _deep_thaw(value or {})
    if not isinstance(thawed, dict):  # Defensive; the public type is a Mapping.
        raise StrategyRunError("invalid_mapping", "value must be an object")
    return thawed


def _fingerprint(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_symbols(values: Sequence[Any]) -> tuple[str, ...]:
    symbols: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = _trim(raw).upper()
        if not value or value in seen:
            continue
        seen.add(value)
        symbols.append(value)
    if not symbols:
        raise StrategyRunError(
            "empty_universe", "strategy run universe must contain at least one symbol"
        )
    return tuple(symbols)


@dataclass(frozen=True)
class StrategyReference:
    kind: str
    name: str
    version: str = "v1"
    revision: int | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "StrategyReference":
        if not isinstance(value, Mapping):
            raise StrategyRunError(
                "invalid_strategy_ref", "strategy_ref must be an object"
            )
        kind = _trim(value.get("kind")) or "tool"
        name = _trim(value.get("name"))
        if not name:
            raise StrategyRunError("missing_strategy", "strategy_ref.name is required")
        raw_revision = value.get("revision")
        revision = None
        if raw_revision not in (None, ""):
            revision = _non_negative_int(
                raw_revision, field_name="strategy_ref.revision"
            )
        return cls(
            kind=kind,
            name=name,
            version=_trim(value.get("version")) or "v1",
            revision=revision,
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "name": self.name,
            "version": self.version,
        }
        if self.revision is not None:
            payload["revision"] = self.revision
        return payload


@dataclass(frozen=True)
class StrategyRuntimeProfile:
    """System-owned execution metadata compiled once per strategy revision.

    ``entity_argument`` is empty for a universe-native strategy.  When it is
    present, the JSON Schema type decides whether the wrapper maps scalar calls
    or sends one native array call.  There is intentionally no public
    single/multi/market mode.
    """

    entity_argument: str = ""
    required_history_sessions: int = 0
    default_run_sessions: int = 100
    default_universe_ref: Mapping[str, Any] = field(default_factory=dict)
    market_code: str = DEFAULT_MARKET_CODE

    def __post_init__(self) -> None:
        object.__setattr__(self, "entity_argument", _trim(self.entity_argument))
        object.__setattr__(
            self,
            "required_history_sessions",
            _non_negative_int(
                self.required_history_sessions,
                field_name="required_history_sessions",
            ),
        )
        object.__setattr__(
            self,
            "default_run_sessions",
            _positive_int(self.default_run_sessions, field_name="default_run_sessions"),
        )
        object.__setattr__(
            self,
            "default_universe_ref",
            _frozen_mapping(self.default_universe_ref),
        )
        object.__setattr__(
            self, "market_code", _trim(self.market_code) or DEFAULT_MARKET_CODE
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "StrategyRuntimeProfile":
        payload = dict(value or {})
        raw_binding = payload.get("binding")
        binding: Mapping[str, Any] = (
            raw_binding if isinstance(raw_binding, Mapping) else {}
        )
        candidates = [
            _trim(payload.get("entity_argument")),
            _trim(binding.get("field")),
            _trim(binding.get("scalar_field")),
            _trim(binding.get("collection_field")),
        ]
        fields = list(dict.fromkeys(item for item in candidates if item))
        if len(fields) > 1:
            raise StrategyRunError(
                "ambiguous_entity_binding",
                "runtime profile must identify at most one entity argument",
                details={"fields": fields},
            )
        default_universe = payload.get("default_universe_ref")
        if default_universe is None:
            default_universe = payload.get("universe_ref")
        if default_universe is not None and not isinstance(default_universe, Mapping):
            raise StrategyRunError(
                "invalid_universe_ref",
                "default_universe_ref must be an object",
            )
        return cls(
            entity_argument=fields[0] if fields else "",
            required_history_sessions=payload.get("required_history_sessions", 0),
            default_run_sessions=payload.get("default_run_sessions", 100),
            default_universe_ref=default_universe or {},
            market_code=_trim(payload.get("market_code")) or DEFAULT_MARKET_CODE,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": STRATEGY_RUNTIME_PROFILE_PROTOCOL,
            "binding": (
                {"field": self.entity_argument} if self.entity_argument else {}
            ),
            "required_history_sessions": self.required_history_sessions,
            "default_run_sessions": self.default_run_sessions,
            "default_universe_ref": _json_mapping(self.default_universe_ref),
            "market_code": self.market_code,
        }


class TradingCalendarPort(Protocol):
    def latest_completed_session(
        self,
        *,
        market_code: str,
        now: dt.datetime,
    ) -> dt.date: ...

    def recent_sessions(
        self,
        *,
        market_code: str,
        end_on_or_before: dt.date,
        count: int,
    ) -> Sequence[dt.date]: ...


@dataclass(frozen=True)
class UniverseMembers:
    members: Sequence[str]
    source: str
    evidence: Mapping[str, Any] = field(default_factory=dict)


class UniverseResolverPort(Protocol):
    def resolve(
        self,
        reference: Mapping[str, Any],
        *,
        as_of: dt.date,
    ) -> UniverseMembers: ...


class AiiaTradingCalendarPort:
    """CN-A implementation backed by ``aiia_trade_calendar``."""

    def __init__(
        self,
        *,
        database: str = "kingdomai",
        db_factory: Callable[..., Any] | None = None,
        timezone: str = "Asia/Shanghai",
        completed_after: dt.time = dt.time(15, 30),
    ) -> None:
        if db_factory is None:
            from src.utils.mysql_utils import StockInfoDbUtils

            db_factory = StockInfoDbUtils
        self.database = database
        self.db_factory = db_factory
        self.timezone = ZoneInfo(timezone)
        self.completed_after = completed_after

    def latest_completed_session(
        self,
        *,
        market_code: str,
        now: dt.datetime,
    ) -> dt.date:
        localized = now
        if localized.tzinfo is None:
            localized = localized.replace(tzinfo=self.timezone)
        else:
            localized = localized.astimezone(self.timezone)
        candidate = localized.date()
        if localized.timetz().replace(tzinfo=None) < self.completed_after:
            candidate -= dt.timedelta(days=1)
        sessions = self.recent_sessions(
            market_code=market_code,
            end_on_or_before=candidate,
            count=1,
        )
        if not sessions:
            raise StrategyRunError(
                "trade_calendar_unavailable",
                f"no completed trading session for market {market_code}",
            )
        return sessions[-1]

    def recent_sessions(
        self,
        *,
        market_code: str,
        end_on_or_before: dt.date,
        count: int,
    ) -> tuple[dt.date, ...]:
        requested_count = _positive_int(count, field_name="calendar.count")
        db = self.db_factory(database=self.database)
        try:
            with db.conn.cursor() as cursor:
                cursor.execute(
                    "SELECT calendar_date FROM aiia_trade_calendar "
                    "WHERE market_code = %s AND is_trade_day = 1 AND calendar_date <= %s "
                    "ORDER BY calendar_date DESC LIMIT %s",
                    (market_code, end_on_or_before, requested_count),
                )
                rows = cursor.fetchall()
        finally:
            close_db = getattr(db, "close_db", None)
            if callable(close_db):
                close_db()
        dates: list[dt.date] = []
        for row in rows:
            raw = row.get("calendar_date") if isinstance(row, Mapping) else row[0]
            parsed = _parse_date(raw, field_name="calendar_date")
            if parsed is not None:
                dates.append(parsed)
        return tuple(sorted(set(dates)))


class KingdomaiAshareUniverseResolver:
    """Resolve historical all-A-share membership without the API row limit."""

    SUPPORTED_TYPE = "all_a_share"

    def __init__(
        self,
        *,
        database: str = "kingdomai",
        db_factory: Callable[..., Any] | None = None,
    ) -> None:
        if db_factory is None:
            from src.utils.mysql_utils import StockInfoDbUtils

            db_factory = StockInfoDbUtils
        self.database = database
        self.db_factory = db_factory

    def resolve(
        self,
        reference: Mapping[str, Any],
        *,
        as_of: dt.date,
    ) -> UniverseMembers:
        universe_type = _trim(reference.get("type"))
        if universe_type != self.SUPPORTED_TYPE:
            raise StrategyRunError(
                "unsupported_universe",
                f"unsupported universe type: {universe_type or '-'}",
            )
        db = self.db_factory(database=self.database)
        try:
            with db.conn.cursor() as cursor:
                cursor.execute(
                    "SELECT stk_code FROM kcrp_stock_baseinfo "
                    "WHERE market IN ('SH', 'SZ', 'BJ') "
                    "AND list_date IS NOT NULL AND list_date <= %s "
                    "AND (delist_date IS NULL OR delist_date >= %s) "
                    "ORDER BY stk_code ASC",
                    (as_of, as_of),
                )
                rows = cursor.fetchall()
        finally:
            close_db = getattr(db, "close_db", None)
            if callable(close_db):
                close_db()
        members = [
            row.get("stk_code") if isinstance(row, Mapping) else row[0] for row in rows
        ]
        return UniverseMembers(
            members=_normalize_symbols(members),
            source="kingdomai.kcrp_stock_baseinfo",
            evidence={
                "as_of": as_of.isoformat(),
                "membership_rule": "list_date <= as_of <= delist_date",
            },
        )


@dataclass(frozen=True)
class ResolvedRunWindow:
    requested_as_of: dt.date | None
    effective_as_of: dt.date
    run_session_dates: tuple[dt.date, ...]
    warmup_session_dates: tuple[dt.date, ...]
    calendar_fingerprint: str
    warnings: tuple[str, ...] = ()

    @property
    def run_start(self) -> dt.date:
        return self.run_session_dates[0]

    @property
    def run_end(self) -> dt.date:
        return self.run_session_dates[-1]

    @property
    def data_start(self) -> dt.date:
        return (
            self.warmup_session_dates[0]
            if self.warmup_session_dates
            else self.run_start
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_as_of": self.requested_as_of.isoformat()
            if self.requested_as_of
            else "",
            "effective_as_of": self.effective_as_of.isoformat(),
            "run_start": self.run_start.isoformat(),
            "run_end": self.run_end.isoformat(),
            "data_start": self.data_start.isoformat(),
            "run_sessions": len(self.run_session_dates),
            "warmup_sessions": len(self.warmup_session_dates),
            "calendar_fingerprint": self.calendar_fingerprint,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class ResolvedUniverseSnapshot:
    reference: Mapping[str, Any]
    members: tuple[str, ...]
    as_of: dt.date
    source: str
    snapshot_hash: str
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "reference", _frozen_mapping(self.reference))
        object.__setattr__(self, "evidence", _frozen_mapping(self.evidence))

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference": _json_mapping(self.reference),
            "members": list(self.members),
            "member_count": len(self.members),
            "as_of": self.as_of.isoformat(),
            "source": self.source,
            "snapshot_hash": self.snapshot_hash,
            "evidence": _json_mapping(self.evidence),
        }


@dataclass(frozen=True)
class ResolvedStrategyRunPlan:
    strategy_ref: StrategyReference
    parameters: Mapping[str, Any]
    runtime_profile: StrategyRuntimeProfile
    window: ResolvedRunWindow
    universe: ResolvedUniverseSnapshot
    plan_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", _frozen_mapping(self.parameters))

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": STRATEGY_RUN_PROTOCOL,
            "strategy_ref": self.strategy_ref.to_dict(),
            "parameters": _json_mapping(self.parameters),
            "runtime_profile": self.runtime_profile.to_dict(),
            "window": self.window.to_dict(),
            "universe": self.universe.to_dict(),
            "plan_hash": self.plan_hash,
        }

    def runtime_context(self) -> dict[str, Any]:
        return {
            "protocol": STRATEGY_RUN_PROTOCOL,
            "plan_hash": self.plan_hash,
            "strategy_ref": self.strategy_ref.to_dict(),
            "effective_as_of": self.window.effective_as_of.isoformat(),
            "run_start": self.window.run_start.isoformat(),
            "run_end": self.window.run_end.isoformat(),
            "data_start": self.window.data_start.isoformat(),
            "universe": {
                "snapshot_hash": self.universe.snapshot_hash,
                "member_count": len(self.universe.members),
                "reference": _json_mapping(self.universe.reference),
                "source": self.universe.source,
                "as_of": self.universe.as_of.isoformat(),
            },
        }

    def backtest_context(self) -> dict[str, Any]:
        """Inputs for a data loader and the existing BacktestConfig boundary."""

        return {
            "universe": list(self.universe.members),
            "data_start": self.window.data_start.isoformat(),
            "start_date": self.window.run_start.isoformat(),
            "end_date": self.window.run_end.isoformat(),
            "as_of": self.window.effective_as_of.isoformat(),
            "strategy_ref": self.strategy_ref.to_dict(),
            "plan_hash": self.plan_hash,
        }


class StrategyRunResolver:
    """Turn a soft request into an immutable, replayable strategy run plan."""

    def __init__(
        self,
        *,
        calendar: TradingCalendarPort,
        universe_resolver: UniverseResolverPort | None = None,
        clock: Callable[[], dt.datetime] | None = None,
    ) -> None:
        self.calendar = calendar
        self.universe_resolver = universe_resolver
        self.clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))

    def resolve(
        self,
        request: Mapping[str, Any],
        *,
        runtime_profile: StrategyRuntimeProfile | Mapping[str, Any] | None = None,
    ) -> ResolvedStrategyRunPlan:
        if not isinstance(request, Mapping):
            raise StrategyRunError(
                "invalid_run_request", "strategy run request must be an object"
            )
        profile = (
            runtime_profile
            if isinstance(runtime_profile, StrategyRuntimeProfile)
            else StrategyRuntimeProfile.from_mapping(runtime_profile)
        )
        raw_ref = request.get("strategy_ref")
        if not isinstance(raw_ref, Mapping):
            raw_ref = request.get("target_ref")
        strategy_ref = StrategyReference.from_mapping(raw_ref or {})
        raw_parameters = request.get("parameters")
        if raw_parameters is None:
            raw_parameters = request.get("arguments")
        if raw_parameters is None:
            raw_parameters = {}
        if not isinstance(raw_parameters, Mapping):
            raise StrategyRunError(
                "invalid_parameters", "strategy parameters must be an object"
            )
        parameters = _json_mapping(raw_parameters)
        if "_runtime" in parameters:
            raise StrategyRunError(
                "reserved_runtime_field",
                "strategy parameters must not contain _runtime",
            )

        window = self._resolve_window(request=request, profile=profile)
        universe = self._resolve_universe(
            request=request,
            profile=profile,
            as_of=window.effective_as_of,
        )
        hash_payload = {
            "protocol": STRATEGY_RUN_PROTOCOL,
            "strategy_ref": strategy_ref.to_dict(),
            "parameters": parameters,
            "runtime_profile": profile.to_dict(),
            "window": window.to_dict(),
            "universe": universe.to_dict(),
        }
        return ResolvedStrategyRunPlan(
            strategy_ref=strategy_ref,
            parameters=parameters,
            runtime_profile=profile,
            window=window,
            universe=universe,
            plan_hash=_fingerprint(hash_payload),
        )

    def _resolve_window(
        self,
        *,
        request: Mapping[str, Any],
        profile: StrategyRuntimeProfile,
    ) -> ResolvedRunWindow:
        raw_temporal = request.get("temporal")
        temporal: Mapping[str, Any] = (
            raw_temporal if isinstance(raw_temporal, Mapping) else {}
        )
        raw_as_of = temporal.get("as_of")
        if raw_as_of in (None, ""):
            raw_as_of = temporal.get("as_of_date")
        if raw_as_of in (None, ""):
            raw_as_of = request.get("as_of") or request.get("as_of_date")
        requested_as_of = _parse_date(raw_as_of, field_name="as_of")
        run_sessions = _positive_int(
            temporal.get(
                "run_sessions",
                request.get("run_sessions", profile.default_run_sessions),
            ),
            field_name="run_sessions",
        )
        warmup_sessions = profile.required_history_sessions
        latest_completed = self.calendar.latest_completed_session(
            market_code=profile.market_code,
            now=self.clock(),
        )
        requested_end = requested_as_of or latest_completed
        warnings: list[str] = []
        if requested_end > latest_completed:
            requested_end = latest_completed
            warnings.append(
                "请求截止日尚未形成完整交易数据，已使用最近一个已完成交易日 "
                f"{latest_completed.isoformat()}。"
            )
        total_sessions = run_sessions + warmup_sessions
        raw_sessions = self.calendar.recent_sessions(
            market_code=profile.market_code,
            end_on_or_before=requested_end,
            count=total_sessions,
        )
        parsed_sessions: list[dt.date] = []
        for raw_session in raw_sessions:
            try:
                session = _parse_date(raw_session, field_name="calendar_session")
            except StrategyRunError as exc:
                raise StrategyRunError(
                    "invalid_calendar_resolution",
                    "trade calendar returned an invalid session",
                    details={"value": _trim(raw_session)},
                ) from exc
            if session is None or session > requested_end:
                raise StrategyRunError(
                    "invalid_calendar_resolution",
                    "trade calendar returned a session after the requested cutoff",
                    details={
                        "value": session.isoformat() if session else "",
                        "end_on_or_before": requested_end.isoformat(),
                    },
                )
            parsed_sessions.append(session)
        sessions = tuple(sorted(set(parsed_sessions)))
        if len(sessions) < total_sessions:
            raise StrategyRunError(
                "insufficient_calendar_history",
                "trade calendar does not contain enough sessions for run window and warm-up",
                details={
                    "required": total_sessions,
                    "available": len(sessions),
                    "end_on_or_before": requested_end.isoformat(),
                },
            )
        sessions = sessions[-total_sessions:]
        effective_as_of = sessions[-1]
        if (
            requested_as_of is not None
            and requested_as_of <= latest_completed
            and requested_as_of != effective_as_of
        ):
            warnings.append(
                f"请求日期 {requested_as_of.isoformat()} 不是交易日，已使用前一个交易日 "
                f"{effective_as_of.isoformat()}。"
            )
        run_dates = sessions[-run_sessions:]
        warmup_dates = sessions[:-run_sessions]
        calendar_fingerprint = _fingerprint(
            {
                "market_code": profile.market_code,
                "sessions": [item.isoformat() for item in sessions],
            }
        )
        return ResolvedRunWindow(
            requested_as_of=requested_as_of,
            effective_as_of=effective_as_of,
            run_session_dates=run_dates,
            warmup_session_dates=warmup_dates,
            calendar_fingerprint=calendar_fingerprint,
            warnings=tuple(warnings),
        )

    def _resolve_universe(
        self,
        *,
        request: Mapping[str, Any],
        profile: StrategyRuntimeProfile,
        as_of: dt.date,
    ) -> ResolvedUniverseSnapshot:
        raw_scope = request.get("scope")
        scope: Mapping[str, Any] = raw_scope if isinstance(raw_scope, Mapping) else {}
        raw_targets = scope.get("targets", request.get("targets"))
        raw_reference = scope.get("universe_ref", request.get("universe_ref"))
        has_targets = raw_targets not in (None, "", [])
        has_reference = isinstance(raw_reference, Mapping) and bool(raw_reference)
        if has_targets and has_reference:
            raise StrategyRunError(
                "ambiguous_universe",
                "scope must use either targets or universe_ref, not both",
            )
        if not has_targets and not has_reference and profile.default_universe_ref:
            raw_reference = _json_mapping(profile.default_universe_ref)
            has_reference = True

        if has_targets:
            values = [raw_targets] if isinstance(raw_targets, str) else raw_targets
            if not isinstance(values, Sequence):
                raise StrategyRunError(
                    "invalid_targets", "scope.targets must be a string or list"
                )
            members = _normalize_symbols(values)
            reference = {"type": "explicit_targets"}
            source = "request.scope.targets"
            evidence: Mapping[str, Any] = {}
        elif has_reference:
            if self.universe_resolver is None:
                raise StrategyRunError(
                    "universe_resolver_unavailable",
                    "a universe resolver is required for universe_ref",
                )
            reference = _json_mapping(raw_reference)
            resolution = self.universe_resolver.resolve(reference, as_of=as_of)
            if not isinstance(resolution, UniverseMembers):
                raise StrategyRunError(
                    "invalid_universe_resolution",
                    "universe resolver must return UniverseMembers",
                )
            members = _normalize_symbols(resolution.members)
            source = _trim(resolution.source) or "universe_resolver"
            evidence = resolution.evidence
        else:
            raise StrategyRunError(
                "missing_universe",
                "strategy run requires targets or universe_ref",
            )

        snapshot_payload = {
            "reference": reference,
            "members": list(members),
            "as_of": as_of.isoformat(),
            "source": source,
            "evidence": dict(evidence or {}),
        }
        return ResolvedUniverseSnapshot(
            reference=reference,
            members=members,
            as_of=as_of,
            source=source,
            snapshot_hash=_fingerprint(snapshot_payload),
            evidence=evidence,
        )


@dataclass(frozen=True)
class StrategyInvocation:
    index: int
    subjects: tuple[str, ...]
    arguments: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "arguments", _frozen_mapping(self.arguments))

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "subjects": list(self.subjects),
            "arguments": _json_mapping(self.arguments),
        }


@dataclass(frozen=True)
class PreparedStrategyRun:
    plan: ResolvedStrategyRunPlan
    invocations: tuple[StrategyInvocation, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan.to_dict(),
            "invocations": [item.to_dict() for item in self.invocations],
        }


class StrategyInvocationAdapter:
    """Compile a resolved universe into schema-valid calls without changing business logic.

    Native array bindings receive the complete resolved universe in one call.
    Scalar legacy bindings remain ordered one-target calls for compatibility.
    """

    def prepare(
        self,
        plan: ResolvedStrategyRunPlan,
        *,
        input_schema: Mapping[str, Any] | None = None,
        execution_shape: str = "",
    ) -> PreparedStrategyRun:
        schema = self._input_schema(input_schema or {})
        arguments = _json_mapping(plan.parameters)
        field_name = plan.runtime_profile.entity_argument
        if not field_name:
            return PreparedStrategyRun(
                plan=plan,
                invocations=(
                    StrategyInvocation(
                        index=0, subjects=plan.universe.members, arguments=arguments
                    ),
                ),
            )

        raw_properties = schema.get("properties")
        properties: Mapping[str, Any] = (
            raw_properties if isinstance(raw_properties, Mapping) else {}
        )
        field_schema = (
            properties.get(field_name)
            if isinstance(properties.get(field_name), Mapping)
            else None
        )
        if field_schema is None:
            raise StrategyRunError(
                "entity_binding_missing_from_schema",
                f"runtime profile entity argument is absent from input schema: {field_name}",
            )
        field_type = field_schema.get("type")
        allowed_types = (
            set(field_type) if isinstance(field_type, list) else {field_type}
        )
        if "array" in allowed_types:
            item_schema = field_schema.get("items")
            item_type = (
                item_schema.get("type") if isinstance(item_schema, Mapping) else None
            )
            allowed_item_types = (
                set(item_type) if isinstance(item_type, list) else {item_type}
            )
            if "string" not in allowed_item_types:
                raise StrategyRunError(
                    "unsupported_entity_binding",
                    f"entity array argument {field_name} must contain strings",
                    details={"type": field_type, "items_type": item_type},
                )
            self._assert_binding_compatible(
                existing=arguments.get(field_name),
                expected=list(plan.universe.members),
                field_name=field_name,
            )
            arguments[field_name] = list(plan.universe.members)
            invocations = (
                StrategyInvocation(
                    index=0, subjects=plan.universe.members, arguments=arguments
                ),
            )
        elif "string" in allowed_types:
            existing = arguments.get(field_name)
            if existing not in (None, ""):
                if (
                    len(plan.universe.members) != 1
                    or _trim(existing).upper() != plan.universe.members[0]
                ):
                    raise StrategyRunError(
                        "scope_binding_conflict",
                        f"strategy parameter {field_name} conflicts with resolved universe",
                    )
            invocations = tuple(
                StrategyInvocation(
                    index=index,
                    subjects=(symbol,),
                    arguments={**arguments, field_name: symbol},
                )
                for index, symbol in enumerate(plan.universe.members)
            )
        else:
            raise StrategyRunError(
                "unsupported_entity_binding",
                f"entity argument {field_name} must be a string or array",
                details={"type": field_type},
            )
        return PreparedStrategyRun(plan=plan, invocations=invocations)

    @staticmethod
    def _input_schema(value: Mapping[str, Any]) -> Mapping[str, Any]:
        nested = (
            value.get("input_schema")
            if isinstance(value.get("input_schema"), Mapping)
            else None
        )
        return nested or value

    @staticmethod
    def _assert_binding_compatible(
        *, existing: Any, expected: list[str], field_name: str
    ) -> None:
        if existing in (None, "", []):
            return
        if not isinstance(existing, Sequence) or isinstance(existing, str):
            raise StrategyRunError(
                "scope_binding_conflict",
                f"strategy parameter {field_name} conflicts with resolved universe",
            )
        normalized = [_trim(item).upper() for item in existing if _trim(item)]
        if normalized != expected:
            raise StrategyRunError(
                "scope_binding_conflict",
                f"strategy parameter {field_name} conflicts with resolved universe",
            )


class StrategyInvokerPort(Protocol):
    def invoke(
        self,
        *,
        strategy_ref: StrategyReference,
        arguments: Mapping[str, Any],
        runtime_context: Mapping[str, Any],
    ) -> Any: ...


class StrategyAssetInvoker:
    """Dispatch through host-provided, authorization and revision-aware runners.

    The wrapper deliberately does not instantiate ``SkillRunner`` or call the
    generic tool registry itself.  Those low-level paths cannot guarantee asset
    visibility, owner authorization, or pinned-revision execution.
    """

    def __init__(
        self,
        *,
        tool_runner: Callable[..., Any] | None = None,
        skill_runner: Any = None,
    ) -> None:
        self.tool_runner = tool_runner
        self.skill_runner = skill_runner

    def invoke(
        self,
        *,
        strategy_ref: StrategyReference,
        arguments: Mapping[str, Any],
        runtime_context: Mapping[str, Any],
    ) -> Any:
        runner = self.tool_runner if strategy_ref.kind == "tool" else self.skill_runner
        if strategy_ref.kind not in {"tool", "skill"}:
            raise StrategyRunError(
                "unsupported_strategy_kind",
                f"unsupported strategy asset kind: {strategy_ref.kind}",
            )
        if runner is None:
            raise StrategyRunError(
                "strategy_host_unavailable",
                f"an authorized {strategy_ref.kind} host runner is required",
            )
        if callable(runner):
            return runner(
                strategy_ref=strategy_ref,
                arguments=_json_mapping(arguments),
                runtime_context=_json_mapping(runtime_context),
            )
        invoke = getattr(runner, "invoke", None)
        if callable(invoke):
            return invoke(
                strategy_ref=strategy_ref,
                arguments=_json_mapping(arguments),
                runtime_context=_json_mapping(runtime_context),
            )
        raise StrategyRunError(
            "invalid_strategy_host",
            f"configured {strategy_ref.kind} host runner is not callable",
        )


class StrategyRunExecutor:
    """Dispatch prepared calls; bounded parallelism only serves legacy multi-call plans."""

    def __init__(
        self,
        *,
        invoker: StrategyInvokerPort,
        max_concurrency: int = 4,
    ) -> None:
        self.invoker = invoker
        self.max_concurrency = _positive_int(
            max_concurrency, field_name="max_concurrency"
        )

    def execute(
        self,
        prepared: PreparedStrategyRun,
        *,
        runtime_context: Mapping[str, Any] | None = None,
        event_handler: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        invocations = prepared.invocations
        if not invocations:
            raise StrategyRunError(
                "empty_invocations", "prepared strategy run has no invocations"
            )
        base_context = _json_mapping(runtime_context)
        base_context["source_type"] = "strategy_run"
        base_context["task_type"] = "strategy_run"
        base_context["strategy_run"] = prepared.plan.runtime_context()
        self._emit(
            event_handler,
            {
                "type": "strategy_run_started",
                "plan_hash": prepared.plan.plan_hash,
                "invocation_count": len(invocations),
                "universe_count": len(prepared.plan.universe.members),
            },
        )

        ordered: list[dict[str, Any] | None] = [None] * len(invocations)
        if len(invocations) == 1:
            ordered[0] = self._invoke_one(invocations[0], prepared.plan, base_context)
            self._emit(event_handler, self._item_event(ordered[0]))
        else:
            self._execute_bounded(
                invocations=invocations,
                plan=prepared.plan,
                base_context=base_context,
                ordered=ordered,
                event_handler=event_handler,
            )

        items = [item for item in ordered if item is not None]
        completed = sum(1 for item in items if item["status"] == "completed")
        failed = len(items) - completed
        result = {
            "protocol": "strategy_run_result.v1",
            "ok": failed == 0,
            "plan": prepared.plan.to_dict(),
            "summary": {
                "invocation_count": len(items),
                "completed": completed,
                "failed": failed,
                "universe_count": len(prepared.plan.universe.members),
            },
            "items": items,
        }
        self._emit(
            event_handler,
            {
                "type": "strategy_run_completed",
                "plan_hash": prepared.plan.plan_hash,
                "completed": completed,
                "failed": failed,
            },
        )
        return result

    def _execute_bounded(
        self,
        *,
        invocations: tuple[StrategyInvocation, ...],
        plan: ResolvedStrategyRunPlan,
        base_context: Mapping[str, Any],
        ordered: list[dict[str, Any] | None],
        event_handler: Callable[[Mapping[str, Any]], None] | None,
    ) -> None:
        iterator = iter(invocations)
        workers = min(self.max_concurrency, len(invocations))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            pending: dict[Future[dict[str, Any]], StrategyInvocation] = {}

            def submit_next() -> bool:
                try:
                    invocation = next(iterator)
                except StopIteration:
                    return False
                future = executor.submit(
                    self._invoke_one, invocation, plan, base_context
                )
                pending[future] = invocation
                return True

            for _ in range(workers):
                submit_next()
            while pending:
                completed_futures, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
                for future in completed_futures:
                    invocation = pending.pop(future)
                    item = future.result()
                    ordered[invocation.index] = item
                    self._emit(event_handler, self._item_event(item))
                    submit_next()

    def _invoke_one(
        self,
        invocation: StrategyInvocation,
        plan: ResolvedStrategyRunPlan,
        base_context: Mapping[str, Any],
    ) -> dict[str, Any]:
        context = _json_mapping(base_context)
        context["strategy_invocation"] = {
            "index": invocation.index,
            "subjects": list(invocation.subjects),
        }
        try:
            raw_result = self.invoker.invoke(
                strategy_ref=plan.strategy_ref,
                arguments=_json_mapping(invocation.arguments),
                runtime_context=context,
            )
            failed = isinstance(raw_result, Mapping) and raw_result.get("ok") is False
            return {
                "index": invocation.index,
                "subjects": list(invocation.subjects),
                "status": "failed" if failed else "completed",
                "result": raw_result,
                **(
                    {
                        "error": _trim(raw_result.get("error"))
                        or "strategy returned ok=false"
                    }
                    if failed
                    else {}
                ),
            }
        except Exception as exc:  # noqa: BLE001 - isolate one item in a batch.
            return {
                "index": invocation.index,
                "subjects": list(invocation.subjects),
                "status": "failed",
                "result": {},
                "error": str(exc),
            }

    @staticmethod
    def _item_event(item: Mapping[str, Any] | None) -> dict[str, Any]:
        payload = dict(item or {})
        return {
            "type": "strategy_invocation_completed",
            "index": payload.get("index"),
            "subjects": payload.get("subjects") or [],
            "status": payload.get("status") or "failed",
        }

    @staticmethod
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


__all__ = [
    "AiiaTradingCalendarPort",
    "KingdomaiAshareUniverseResolver",
    "PreparedStrategyRun",
    "ResolvedRunWindow",
    "ResolvedStrategyRunPlan",
    "ResolvedUniverseSnapshot",
    "StrategyAssetInvoker",
    "StrategyInvocation",
    "StrategyInvocationAdapter",
    "StrategyInvokerPort",
    "StrategyReference",
    "StrategyRunError",
    "StrategyRunExecutor",
    "StrategyRunResolver",
    "StrategyRuntimeProfile",
    "TradingCalendarPort",
    "UniverseMembers",
    "UniverseResolverPort",
]
