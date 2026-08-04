from __future__ import annotations

import datetime as dt
import json
import threading
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import pytest

from src.services.strategy_run_service import (
    AiiaTradingCalendarPort,
    KingdomaiAshareUniverseResolver,
    StrategyAssetInvoker,
    StrategyInvocationAdapter,
    StrategyReference,
    StrategyRunError,
    StrategyRunExecutor,
    StrategyRunResolver,
    StrategyRuntimeProfile,
    UniverseMembers,
)


def _business_days(start: dt.date, count: int) -> tuple[dt.date, ...]:
    values: list[dt.date] = []
    current = start
    while len(values) < count:
        if current.weekday() < 5:
            values.append(current)
        current += dt.timedelta(days=1)
    return tuple(values)


class _FakeCalendar:
    def __init__(self, sessions, *, latest=None):
        self.sessions = tuple(sessions)
        self.latest = latest or self.sessions[-1]
        self.requests = []

    def latest_completed_session(self, *, market_code, now):
        self.requests.append(("latest", market_code, now))
        return self.latest

    def recent_sessions(self, *, market_code, end_on_or_before, count):
        self.requests.append(("recent", market_code, end_on_or_before, count))
        eligible = [item for item in self.sessions if item <= end_on_or_before]
        return eligible[-count:]


class _FakeUniverseResolver:
    def __init__(self, members, *, evidence=None):
        self.members = members
        self.evidence = evidence or {"revision": "u7"}
        self.calls = []

    def resolve(self, reference, *, as_of):
        self.calls.append((dict(reference), as_of))
        return UniverseMembers(
            members=self.members,
            source="fake.historical_membership",
            evidence=self.evidence,
        )


def _request(*, scope: Mapping[str, Any], temporal=None):
    return {
        "strategy_ref": {
            "kind": "tool",
            "name": "ct_demo_strategy",
            "version": "v1",
            "revision": 7,
        },
        "parameters": {"threshold": 3.5},
        "scope": dict(scope),
        "temporal": dict(temporal or {}),
    }


def _resolve(
    sessions,
    *,
    scope=None,
    temporal=None,
    profile=None,
    latest=None,
    universe_resolver=None,
):
    calendar = _FakeCalendar(sessions, latest=latest)
    resolver = StrategyRunResolver(
        calendar=calendar,
        universe_resolver=universe_resolver,
        clock=lambda: dt.datetime(2026, 7, 31, 10, tzinfo=dt.timezone.utc),
    )
    plan = resolver.resolve(
        _request(
            scope=scope if scope is not None else {"targets": ["600519.SH"]},
            temporal=temporal,
        ),
        runtime_profile=profile or StrategyRuntimeProfile(default_run_sessions=3),
    )
    return plan, calendar


def test_default_as_of_uses_calendar_latest_completed_session() -> None:
    sessions = _business_days(dt.date(2026, 7, 20), 10)
    latest_completed = sessions[-2]

    plan, calendar = _resolve(sessions, latest=latest_completed)

    assert plan.window.requested_as_of is None
    assert plan.window.effective_as_of == latest_completed
    assert plan.window.run_end == latest_completed
    assert calendar.requests[0][0] == "latest"
    assert plan.window.warnings == ()


def test_explicit_non_trade_as_of_falls_back_with_warning() -> None:
    sessions = _business_days(dt.date(2026, 7, 20), 10)

    plan, _ = _resolve(
        sessions,
        temporal={"as_of": "2026-07-26"},
        latest=sessions[-1],
    )

    assert plan.window.requested_as_of == dt.date(2026, 7, 26)
    assert plan.window.effective_as_of == dt.date(2026, 7, 24)
    assert plan.window.warnings == (
        "请求日期 2026-07-26 不是交易日，已使用前一个交易日 2026-07-24。",
    )


def test_calendar_cannot_return_a_session_after_requested_cutoff() -> None:
    class FutureLeakingCalendar(_FakeCalendar):
        def recent_sessions(self, *, market_code, end_on_or_before, count):
            return (dt.date(2026, 7, 29), dt.date(2026, 7, 31))

    calendar = FutureLeakingCalendar(
        (dt.date(2026, 7, 29), dt.date(2026, 7, 30)),
        latest=dt.date(2026, 7, 30),
    )
    resolver = StrategyRunResolver(calendar=calendar)

    with pytest.raises(StrategyRunError) as caught:
        resolver.resolve(
            _request(
                scope={"targets": ["600519.SH"]},
                temporal={"as_of": "2026-07-30"},
            ),
            runtime_profile=StrategyRuntimeProfile(default_run_sessions=2),
        )

    assert caught.value.code == "invalid_calendar_resolution"


def test_run_window_and_warmup_are_separate() -> None:
    sessions = _business_days(dt.date(2025, 12, 1), 130)
    profile = StrategyRuntimeProfile(
        default_run_sessions=100,
        required_history_sessions=20,
    )

    plan, _ = _resolve(sessions, profile=profile)

    assert plan.window.run_session_dates == sessions[-100:]
    assert plan.window.warmup_session_dates == sessions[-120:-100]
    assert plan.window.run_start == sessions[-100]
    assert plan.window.data_start == sessions[-120]
    assert plan.window.run_end == sessions[-1]
    assert plan.window.to_dict()["run_sessions"] == 100
    assert plan.window.to_dict()["warmup_sessions"] == 20
    assert plan.backtest_context() == {
        "universe": ["600519.SH"],
        "data_start": sessions[-120].isoformat(),
        "start_date": sessions[-100].isoformat(),
        "end_date": sessions[-1].isoformat(),
        "as_of": sessions[-1].isoformat(),
        "strategy_ref": {
            "kind": "tool",
            "name": "ct_demo_strategy",
            "version": "v1",
            "revision": 7,
        },
        "plan_hash": plan.plan_hash,
    }


def test_single_and_list_are_frozen_universe_snapshots() -> None:
    sessions = _business_days(dt.date(2026, 7, 20), 10)
    single, _ = _resolve(sessions, scope={"targets": "600519.sh"})
    mutable_targets = ["600519.sh", "000858.sz", "600519.SH"]
    multiple, _ = _resolve(sessions, scope={"targets": mutable_targets})
    mutable_targets.append("000001.SZ")

    assert single.universe.members == ("600519.SH",)
    assert multiple.universe.members == ("600519.SH", "000858.SZ")
    assert multiple.universe.to_dict()["member_count"] == 2
    assert "000001.SZ" not in multiple.universe.members


def test_market_universe_is_resolved_and_frozen_at_effective_as_of() -> None:
    sessions = _business_days(dt.date(2026, 7, 20), 10)
    source_members = ["600519.SH", "000858.SZ"]
    universe_resolver = _FakeUniverseResolver(source_members)
    profile = StrategyRuntimeProfile(
        default_run_sessions=3,
        default_universe_ref={"type": "all_a_share"},
    )

    plan, _ = _resolve(
        sessions,
        scope={},
        profile=profile,
        universe_resolver=universe_resolver,
    )
    source_members.append("000001.SZ")

    assert universe_resolver.calls == [({"type": "all_a_share"}, sessions[-1])]
    assert plan.universe.members == ("600519.SH", "000858.SZ")
    assert plan.universe.source == "fake.historical_membership"
    assert plan.universe.evidence["revision"] == "u7"


def test_resolved_plan_deeply_detaches_and_freezes_nested_inputs() -> None:
    sessions = _business_days(dt.date(2026, 7, 20), 10)
    universe_ref = {"type": "all_a_share", "filters": {"boards": ["main"]}}
    evidence = {"source_revision": {"parts": ["u7"]}}
    request = _request(scope={"universe_ref": universe_ref})
    request["parameters"] = {"rules": {"weights": [1, 2]}}
    universe_resolver = _FakeUniverseResolver(["600519.SH"], evidence=evidence)
    resolver = StrategyRunResolver(
        calendar=_FakeCalendar(sessions),
        universe_resolver=universe_resolver,
    )

    plan = resolver.resolve(
        request,
        runtime_profile=StrategyRuntimeProfile(default_run_sessions=3),
    )
    before = plan.to_dict()
    plan_hash = plan.plan_hash
    snapshot_hash = plan.universe.snapshot_hash

    request["parameters"]["rules"]["weights"].append(3)
    universe_ref["filters"]["boards"].append("growth")
    evidence["source_revision"]["parts"].append("u8")

    assert plan.to_dict() == before
    assert plan.plan_hash == plan_hash
    assert plan.universe.snapshot_hash == snapshot_hash
    assert plan.parameters["rules"]["weights"] == (1, 2)
    with pytest.raises(TypeError):
        plan.parameters["rules"]["weights"][0] = 9
    with pytest.raises(TypeError):
        plan.universe.reference["filters"]["new"] = True


@pytest.mark.parametrize(
    ("profile", "schema", "expected_call_count", "expected_values"),
    [
        (
            StrategyRuntimeProfile(
                entity_argument="stock_code", default_run_sessions=3
            ),
            {"type": "object", "properties": {"stock_code": {"type": "string"}}},
            2,
            ["600519.SH", "000858.SZ"],
        ),
        (
            StrategyRuntimeProfile(
                entity_argument="stock_codes", default_run_sessions=3
            ),
            {
                "type": "object",
                "properties": {
                    "stock_codes": {"type": "array", "items": {"type": "string"}}
                },
            },
            1,
            [("600519.SH", "000858.SZ")],
        ),
        (
            StrategyRuntimeProfile(default_run_sessions=3),
            {"type": "object", "properties": {"threshold": {"type": "number"}}},
            1,
            [None],
        ),
    ],
)
def test_invocation_shape_is_derived_from_profile_and_schema(
    profile,
    schema,
    expected_call_count,
    expected_values,
) -> None:
    sessions = _business_days(dt.date(2026, 7, 20), 10)
    plan, _ = _resolve(
        sessions,
        scope={"targets": ["600519.SH", "000858.SZ"]},
        profile=profile,
    )

    prepared = StrategyInvocationAdapter().prepare(plan, input_schema=schema)

    assert len(prepared.invocations) == expected_call_count
    field_name = profile.entity_argument
    actual = [
        item.arguments.get(field_name) if field_name else None
        for item in prepared.invocations
    ]
    assert actual == expected_values
    for invocation in prepared.invocations:
        assert "as_of" not in invocation.arguments
        assert "run_sessions" not in invocation.arguments
        assert "warmup_sessions" not in invocation.arguments
        assert "run_mode" not in invocation.arguments


def test_array_entity_binding_requires_string_items() -> None:
    sessions = _business_days(dt.date(2026, 7, 20), 10)
    profile = StrategyRuntimeProfile(
        entity_argument="stock_codes", default_run_sessions=3
    )
    plan, _ = _resolve(sessions, profile=profile)

    with pytest.raises(StrategyRunError) as caught:
        StrategyInvocationAdapter().prepare(
            plan,
            input_schema={
                "type": "object",
                "properties": {
                    "stock_codes": {"type": "array", "items": {"type": "number"}}
                },
            },
        )

    assert caught.value.code == "unsupported_entity_binding"


class _RecordingInvoker:
    def __init__(self, *, failures=(), require_parallel=False):
        self.failures = set(failures)
        self.require_parallel = require_parallel
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.calls = []
        self.second_entered = threading.Event()

    def invoke(self, *, strategy_ref, arguments, runtime_context):
        symbol = arguments["stock_code"]
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.calls.append((symbol, dict(arguments), dict(runtime_context)))
            call_number = len(self.calls)
        try:
            if (
                self.require_parallel
                and call_number == 1
                and not self.second_entered.wait(timeout=2)
            ):
                raise RuntimeError("second concurrent invocation did not start")
            if self.require_parallel and call_number == 2:
                self.second_entered.set()
        finally:
            with self.lock:
                self.active -= 1
        if symbol in self.failures:
            return {"ok": False, "error": "fixture failure", "data": {}}
        return {"ok": True, "data": {"symbol": symbol}}


def test_scalar_map_is_bounded_parallel_and_results_keep_universe_order() -> None:
    sessions = _business_days(dt.date(2026, 7, 20), 10)
    symbols = ["100001.SH", "200002.SH", "300003.SH", "400004.SH", "500005.SH"]
    profile = StrategyRuntimeProfile(
        entity_argument="stock_code", default_run_sessions=3
    )
    plan, _ = _resolve(sessions, scope={"targets": symbols}, profile=profile)
    prepared = StrategyInvocationAdapter().prepare(
        plan,
        input_schema={
            "type": "object",
            "properties": {"stock_code": {"type": "string"}},
        },
    )
    invoker = _RecordingInvoker(failures={"300003.SH"}, require_parallel=True)
    events = []

    result = StrategyRunExecutor(invoker=invoker, max_concurrency=2).execute(
        prepared,
        runtime_context={"owner_id": "user-1"},
        event_handler=events.append,
    )

    assert invoker.max_active == 2
    assert [item["subjects"][0] for item in result["items"]] == symbols
    assert result["ok"] is False
    assert result["summary"] == {
        "invocation_count": 5,
        "completed": 4,
        "failed": 1,
        "universe_count": 5,
    }
    assert events[0]["type"] == "strategy_run_started"
    assert events[-1]["type"] == "strategy_run_completed"


def test_executor_keeps_runtime_context_out_of_business_arguments() -> None:
    sessions = _business_days(dt.date(2026, 7, 20), 10)
    profile = StrategyRuntimeProfile(
        entity_argument="stock_code", default_run_sessions=3
    )
    plan, _ = _resolve(sessions, profile=profile)
    prepared = StrategyInvocationAdapter().prepare(
        plan,
        input_schema={
            "type": "object",
            "properties": {"stock_code": {"type": "string"}},
        },
    )
    invoker = _RecordingInvoker()

    result = StrategyRunExecutor(invoker=invoker, max_concurrency=4).execute(prepared)

    assert result["ok"] is True
    _, business_arguments, runtime_context = invoker.calls[0]
    assert business_arguments == {"threshold": 3.5, "stock_code": "600519.SH"}
    assert "_runtime" not in business_arguments
    assert (
        runtime_context["strategy_run"]["effective_as_of"] == sessions[-1].isoformat()
    )
    assert runtime_context["strategy_run"]["strategy_ref"]["revision"] == 7
    assert runtime_context["strategy_run"]["universe"] == {
        "snapshot_hash": plan.universe.snapshot_hash,
        "member_count": 1,
        "reference": {"type": "explicit_targets"},
        "source": "request.scope.targets",
        "as_of": sessions[-1].isoformat(),
    }


def test_runtime_context_stays_compact_for_a_large_scalar_universe() -> None:
    sessions = _business_days(dt.date(2026, 7, 20), 10)
    symbols = [f"{index:06d}.SZ" for index in range(1, 1001)]
    profile = StrategyRuntimeProfile(
        entity_argument="stock_code", default_run_sessions=3
    )
    plan, _ = _resolve(sessions, scope={"targets": symbols}, profile=profile)

    context = plan.runtime_context()

    assert "universe_members" not in context
    assert context["universe"]["member_count"] == 1000
    assert len(json.dumps(context, ensure_ascii=False)) < 2_000


def test_strategy_asset_invoker_forwards_business_and_hidden_context_separately() -> (
    None
):
    captured = {}

    def tool_runner(*, strategy_ref, arguments, runtime_context):
        captured.update(
            strategy_ref=strategy_ref.to_dict(),
            arguments=arguments,
            runtime_context=runtime_context,
        )
        return {"ok": True}

    result = StrategyAssetInvoker(tool_runner=tool_runner).invoke(
        strategy_ref=StrategyReference(
            kind="tool", name="ct_demo_strategy", revision=7
        ),
        arguments={"threshold": 3.5, "nested": {"enabled": True}},
        runtime_context={"strategy_run": {"effective_as_of": "2026-07-31"}},
    )

    assert result == {"ok": True}
    assert captured == {
        "strategy_ref": {
            "kind": "tool",
            "name": "ct_demo_strategy",
            "version": "v1",
            "revision": 7,
        },
        "arguments": {"threshold": 3.5, "nested": {"enabled": True}},
        "runtime_context": {"strategy_run": {"effective_as_of": "2026-07-31"}},
    }


def test_strategy_asset_invoker_fails_closed_without_a_host_runner() -> None:
    with pytest.raises(StrategyRunError) as caught:
        StrategyAssetInvoker().invoke(
            strategy_ref=StrategyReference(
                kind="skill", name="private_strategy", revision=4
            ),
            arguments={},
            runtime_context={"owner_id": "user-1"},
        )

    assert caught.value.code == "strategy_host_unavailable"


class _Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.sql = ""
        self.params = ()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        self.sql = sql
        self.params = params

    def fetchall(self):
        return self.rows


class _Db:
    def __init__(self, rows):
        self.conn = self
        self.cursor_obj = _Cursor(rows)
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def close_db(self):
        self.closed = True


def test_aiia_calendar_excludes_an_unfinished_current_session() -> None:
    rows = [(dt.date(2026, 7, 30),)]
    db = _Db(rows)
    calendar = AiiaTradingCalendarPort(db_factory=lambda **_kwargs: db)

    resolved = calendar.latest_completed_session(
        market_code="CN_A",
        now=dt.datetime(2026, 7, 31, 14, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert resolved == dt.date(2026, 7, 30)
    assert db.cursor_obj.params == ("CN_A", dt.date(2026, 7, 30), 1)
    assert db.closed is True


def test_kingdomai_all_a_share_resolver_uses_historical_membership_cutoff() -> None:
    db = _Db([("000001.SZ",), ("600519.SH",)])
    resolver = KingdomaiAshareUniverseResolver(db_factory=lambda **_kwargs: db)
    as_of = dt.date(2026, 7, 31)

    result = resolver.resolve({"type": "all_a_share"}, as_of=as_of)

    assert tuple(result.members) == ("000001.SZ", "600519.SH")
    assert db.cursor_obj.params == (as_of, as_of)
    assert "list_date <= %s" in db.cursor_obj.sql
    assert "delist_date >= %s" in db.cursor_obj.sql


def test_invalid_runtime_metadata_is_rejected_before_strategy_execution() -> None:
    with pytest.raises(StrategyRunError) as caught:
        StrategyRuntimeProfile.from_mapping(
            {
                "binding": {
                    "scalar_field": "stock_code",
                    "collection_field": "stock_codes",
                }
            }
        )

    assert caught.value.code == "ambiguous_entity_binding"

    for invalid in (0, -1, 1.5, True):
        with pytest.raises(StrategyRunError) as integer_error:
            StrategyRuntimeProfile(default_run_sessions=invalid)
        assert integer_error.value.code == "invalid_integer"
