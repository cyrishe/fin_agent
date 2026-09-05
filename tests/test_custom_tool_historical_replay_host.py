from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from src.backtest import Bar, BacktestError, InMemoryMarketData, Instrument
from src.experiments.staged_data_protocol.phase2.models import ResultHandle
from src.experiments.staged_data_protocol.phase2.dynamic_cal_provider import (
    HISTORICAL_RAW_ONLY_ARG,
    _dynamic_fields,
)
from src.experiments.staged_data_protocol.phase2.quote_provider import QUOTE_SOURCES
from src.services import finance_data_tool_runtime_service as finance_runtime_module
from src.services.custom_tool_historical_replay_host import (
    CustomToolHistoricalReplayHost,
)
from src.services.custom_tool_service import (
    CustomToolRuntimeService,
    CustomToolStoreService,
)
from src.services.finance_data_tool_runtime_service import (
    FinanceDataToolRuntimeService,
)
from src.services.python_execution_runtime import PythonExecutionRuntime
from src.services.strategy_backtest_service import (
    EqualWeightSelectionPolicy,
    MarketDataTradingCalendar,
    SelectionOutputProfile,
    StrategyToolBacktestService,
)
from src.services.strategy_run_service import (
    StrategyReference,
    StrategyRunResolver,
    StrategyRuntimeProfile,
)


CUTOFF = dt.date(2026, 7, 24)


def _strategy_bundle(*, code: str, tool_name: str = "ct_rank_selector") -> dict:
    return {
        "manifest": {
            "tool_name": tool_name,
            "display_name": "历史排名策略",
            "description": "在固定股票范围内生成有序候选。",
            "visibility": "personal",
            "runtime": {
                "kind": "python_sandbox",
                "backend": "local_dev",
                "timeout_ms": 3000,
            },
        },
        "input_schema": {
            "type": "object",
            "properties": {
                "universe": {
                    "type": "array",
                    "items": {"type": "string"},
                }
            },
            "required": ["universe"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "selected_stocks": {"type": "array"},
                "as_of_date": {"type": "string"},
                "label": {"type": "string"},
                "key_process_info": {"type": "object"},
            },
            "required": ["selected_stocks", "as_of_date"],
        },
        "code": code,
        "finance_tool_profile": {
            "protocol": "finance_tool_profile.v1",
            "family": "strategy",
            "execution_shape": "cross_sectional",
            "output_semantic": "ranked_selection",
            "summary": "固定范围内的历史价格排名。",
        },
        "strategy_runtime_profile": {
            "protocol": "strategy_runtime_profile.v1",
            "binding": {"field": "universe"},
            "required_history_sessions": 2,
            "default_run_sessions": 3,
            "default_universe_ref": {},
            "market_code": "CN_A",
        },
        "selection_output_profile": {
            "candidate_path": "data.selected_stocks",
            "symbol_field": "stock_code",
            "output_date_path": "data.as_of_date",
        },
    }


def _simple_code(label: str = "v1") -> str:
    return (
        "def run(inputs):\n"
        "    return {\n"
        "        'selected_stocks': [],\n"
        "        'as_of_date': '2026-07-24',\n"
        f"        'label': '{label}',\n"
        "        'key_process_info': {},\n"
        "    }\n"
    )


def _store(tmp_path: Path) -> CustomToolStoreService:
    return CustomToolStoreService(
        root_dir=str(tmp_path / "tools"),
        backend="filesystem",
    )


def _ref(*, revision: int | None = 1) -> StrategyReference:
    return StrategyReference(
        kind="tool",
        name="ct_rank_selector",
        version="v1",
        revision=revision,
    )


def test_historical_finance_runtime_injects_cutoff_and_security_scope(monkeypatch):
    captured = []

    def execute(call, _handles):
        captured.append(call)
        return ResultHandle(
            name=call.result_id,
            api=call.api,
            columns=["code", "tradedate"],
            data={
                "status": "ok",
                "rows": [{"code": "600001", "tradedate": CUTOFF.isoformat()}],
            },
        )

    monkeypatch.setattr(finance_runtime_module, "execute_api_call", execute)
    result = FinanceDataToolRuntimeService().execute_historical_request(
        request=(
            'r1 = stock.quote.dynamic_cal(task = "选择股票", k = 2, '
            'as_of = 2099-01-01) -> code, tradedate'
        ),
        effective_as_of=CUTOFF,
        allowed_symbols=["600001.SH", "000002.SZ"],
    )

    assert result["ok"] is True
    assert len(captured) == 1
    assert captured[0].args["as_of"] == "2026-07-25"
    assert captured[0].args["mode"] == 0
    assert captured[0].args["codes"] == ["600001", "000002"]
    assert captured[0].args[HISTORICAL_RAW_ONLY_ARG] is True
    assert "name" not in captured[0].args["fields"].split(", ")
    assert result["historical_scope"]["effective_as_of"] == "2026-07-24"
    assert result["historical_scope"]["policy"] == (
        "raw trade dates <= effective_as_of"
    )


@pytest.mark.parametrize(
    ("request_text", "status"),
    [
        (
            'r1 = stock.quote.dynamic_cal(task = "选择股票", k = 2, realtime = 1) '
            "-> code, tradedate",
            "historical_realtime_denied",
        ),
        (
            'r1 = stock.quote(filter = "code = 600001", limit = 1) '
            "-> code, tradedate",
            "historical_query_unsupported",
        ),
        (
            'r1 = stock.quote.dynamic_cal(task = "按名称筛选", k = 2, '
            'fields = "code, name, tradedate, close", realtime = 0) '
            "-> code, name, tradedate",
            "historical_field_unsupported",
        ),
        (
            'r1 = stock.quote.dynamic_cal(task = "分钟筛选", k = 2, '
            'fields = "code, tradedate, minute_index, close", realtime = 0) '
            "-> code, tradedate",
            "historical_field_unsupported",
        ),
    ],
)
def test_historical_finance_runtime_denies_unproven_or_realtime_api(
    monkeypatch,
    request_text,
    status,
):
    provider_calls = []
    monkeypatch.setattr(
        finance_runtime_module,
        "execute_api_call",
        lambda *args, **kwargs: provider_calls.append((args, kwargs)),
    )

    result = FinanceDataToolRuntimeService().execute_historical_request(
        request=request_text,
        effective_as_of=CUTOFF,
        allowed_symbols=["600001.SH"],
    )

    assert result["ok"] is False
    assert result["execution"]["status"] == status
    assert provider_calls == []


def test_dynamic_cal_historical_projection_omits_current_identity_fields():
    fields = _dynamic_fields(
        source=QUOTE_SOURCES["stock"],
        args={
            "fields": "code, name, tradedate, close",
            HISTORICAL_RAW_ONLY_ARG: True,
        },
    )

    assert fields == ["code", "tradedate", "close"]


def test_historical_finance_runtime_rejects_future_result_date(monkeypatch):
    def execute(call, _handles):
        return ResultHandle(
            name=call.result_id,
            api=call.api,
            columns=["code", "tradedate"],
            data={
                "status": "ok",
                "rows": [{"code": "600001", "tradedate": "2026-07-25"}],
            },
        )

    monkeypatch.setattr(finance_runtime_module, "execute_api_call", execute)

    result = FinanceDataToolRuntimeService().execute_historical_request(
        request=(
            'r1 = stock.quote.dynamic_cal(task = "选择股票", k = 2) '
            "-> code, tradedate"
        ),
        effective_as_of=CUTOFF,
        allowed_symbols=["600001.SH"],
    )

    assert result["ok"] is False
    assert result["execution"]["status"] == "historical_result_after_cutoff"
    assert result["result"] is None


class _RecordingRuntime:
    def __init__(self):
        self.calls = []

    def preflight_historical_replay(self, **_kwargs):
        return {
            "formal_sandbox": True,
            "backend": "controlled_test_runtime",
            "network": "none",
            "workspace_access": "none",
        }

    def run_loaded_bundle(self, **kwargs):
        self.calls.append(kwargs)
        manifest = kwargs["bundle"]["manifest"]
        return {
            "tool": manifest["tool_name"],
            "ok": True,
            "data": {"label": kwargs["bundle"]["code"]},
            "error": "",
            "meta": {"revision": manifest["current_revision"]},
        }


class _ControlledFixtureRuntime(CustomToolRuntimeService):
    """Executes deterministic fixture code without claiming production isolation."""

    def preflight_historical_replay(self, **_kwargs):
        return {
            "formal_sandbox": True,
            "backend": "controlled_test_runtime",
            "network": "fixture_controlled",
            "workspace_access": "fixture_controlled",
        }

    def run_loaded_bundle(self, **kwargs):
        kwargs.pop("runtime_backend", None)
        return self._run_loaded_bundle(**kwargs)


def test_historical_host_rejects_owner_mismatch_before_runtime(tmp_path):
    store = _store(tmp_path)
    store.save_draft(_strategy_bundle(code=_simple_code()), owner_id="owner-a")
    runtime = _RecordingRuntime()
    host = CustomToolHistoricalReplayHost(
        owner_ids=["owner-b"],
        store=store,
        runtime=runtime,  # type: ignore[arg-type]
    )

    with pytest.raises(BacktestError) as caught:
        host.preflight_historical_replay(strategy_ref=_ref())

    assert caught.value.code == "historical_asset_unavailable"
    assert runtime.calls == []


@pytest.mark.parametrize("revision", [None, 0])
def test_historical_host_requires_explicit_positive_revision(tmp_path, revision):
    host = CustomToolHistoricalReplayHost(
        owner_ids=["owner-a"],
        store=_store(tmp_path),
        runtime=_RecordingRuntime(),  # type: ignore[arg-type]
    )

    with pytest.raises(BacktestError) as caught:
        host.preflight_historical_replay(strategy_ref=_ref(revision=revision))

    assert caught.value.code == "historical_revision_required"


def test_historical_host_freezes_revision_when_active_pointer_changes(tmp_path):
    store = _store(tmp_path)
    first = store.save_draft(
        _strategy_bundle(code=_simple_code("v1")),
        owner_id="owner-a",
    )
    runtime = _RecordingRuntime()
    host = CustomToolHistoricalReplayHost(
        owner_ids=["owner-a"],
        store=store,
        runtime=runtime,  # type: ignore[arg-type]
    )
    preflight = host.preflight_historical_replay(strategy_ref=_ref(revision=1))

    second = store.save_draft(
        _strategy_bundle(code=_simple_code("v2")),
        owner_id="owner-a",
    )
    result = host.invoke(
        strategy_ref=_ref(revision=1),
        arguments={"universe": ["600001.SH"]},
        runtime_context={
            "strategy_run": {"effective_as_of": CUTOFF.isoformat()},
            "strategy_invocation": {"subjects": ["600001.SH"]},
        },
    )

    assert first["manifest"]["current_revision"] == 1
    assert second["manifest"]["current_revision"] == 2
    assert runtime.calls[0]["bundle"]["manifest"]["current_revision"] == 1
    assert runtime.calls[0]["runtime_backend"] == "controlled_test_runtime"
    assert "v1" in result["data"]["label"]
    assert "v2" not in result["data"]["label"]
    assert result["meta"]["historical_replay"]["asset_fingerprint"] == (
        preflight["asset_fingerprint"]
    )


def test_historical_host_requires_ranked_selection_companions(tmp_path):
    store = _store(tmp_path)
    bundle = _strategy_bundle(code=_simple_code())
    bundle.pop("selection_output_profile")
    store.save_draft(bundle, owner_id="owner-a")
    host = CustomToolHistoricalReplayHost(
        owner_ids=["owner-a"],
        store=store,
        runtime=_RecordingRuntime(),  # type: ignore[arg-type]
    )

    with pytest.raises(BacktestError) as caught:
        host.preflight_historical_replay(strategy_ref=_ref())

    assert caught.value.code == "strategy_backtest_contract_required"


def test_historical_host_rejects_when_no_formal_runtime_is_available(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        "src.services.python_execution_runtime.shutil.which",
        lambda _name: None,
    )
    store = _store(tmp_path)
    store.save_draft(_strategy_bundle(code=_simple_code()), owner_id="owner-a")
    host = CustomToolHistoricalReplayHost(
        owner_ids=["owner-a"],
        store=store,
        runtime=CustomToolRuntimeService(
            store=store,
            python_runtime=PythonExecutionRuntime(allow_unsafe_backends=True),
            runtime_root=str(tmp_path / "runtime"),
        ),
    )

    with pytest.raises(BacktestError) as caught:
        host.preflight_historical_replay(strategy_ref=_ref())

    assert caught.value.code == "historical_runtime_isolation_required"


def test_historical_runtime_preflight_accepts_available_auto_formal_backend(
    monkeypatch,
):
    bundle = _strategy_bundle(code=_simple_code())
    monkeypatch.setattr(
        "src.services.python_execution_runtime.shutil.which",
        lambda name: "/usr/bin/docker" if name == "docker" else None,
    )

    evidence = CustomToolRuntimeService().preflight_historical_replay(
        bundle=bundle
    )

    assert evidence == {
        "formal_sandbox": True,
        "backend": "docker",
        "network": "none",
        "workspace_access": "none",
    }


class _HistoricalFinanceRuntime:
    def __init__(self):
        self.calls = []

    def execute_historical_request(self, **kwargs):
        self.calls.append(kwargs)
        cutoff = str(kwargs["effective_as_of"])
        return {
            "ok": True,
            "validation": {"ok": True, "errors": []},
            "result": {
                "name": "r1",
                "api": "stock.quote.dynamic_cal",
                "columns": ["code", "tradedate"],
                "data": {
                    "status": "ok",
                    "rows": [
                        {"code": "600001", "tradedate": cutoff},
                        {"code": "000002", "tradedate": cutoff},
                    ],
                },
            },
            "execution": {"ok": True, "status": "ok"},
        }


def test_historical_host_runs_frozen_tool_with_hidden_cutoff_and_scope(tmp_path):
    code = """
from custom_tool_sdk import finance_query


def run(inputs):
    result = finance_query(
        'r1 = stock.quote.dynamic_cal(task = "选择股票", k = 2, realtime = 0) -> code, tradedate'
    )
    rows = result.get('rows') or []
    selected = [
        {'stock_code': row['code'] + ('.SH' if row['code'].startswith('6') else '.SZ')}
        for row in rows
    ]
    return {
        'selected_stocks': selected,
        'as_of_date': max((row['tradedate'] for row in rows), default=''),
        'key_process_info': {'row_count': len(rows)},
    }
""".strip()
    store = _store(tmp_path)
    store.save_draft(_strategy_bundle(code=code), owner_id="owner-a")
    finance_runtime = _HistoricalFinanceRuntime()
    runtime = _ControlledFixtureRuntime(
        store=store,
        python_runtime=PythonExecutionRuntime(allow_unsafe_backends=True),
        runtime_root=str(tmp_path / "runtime"),
        finance_runtime=finance_runtime,  # type: ignore[arg-type]
    )
    host = CustomToolHistoricalReplayHost(
        owner_ids=["owner-a"],
        store=store,
        runtime=runtime,
    )
    host.preflight_historical_replay(strategy_ref=_ref())

    result = host.invoke(
        strategy_ref=_ref(),
        arguments={"universe": ["600001.SH", "000002.SZ"]},
        runtime_context={
            "strategy_run": {"effective_as_of": CUTOFF.isoformat()},
            "strategy_invocation": {
                "subjects": ["600001.SH", "000002.SZ"],
            },
        },
    )

    assert result["ok"] is True
    assert result["data"] == {
        "selected_stocks": [
            {"stock_code": "600001.SH"},
            {"stock_code": "000002.SZ"},
        ],
        "as_of_date": CUTOFF.isoformat(),
        "key_process_info": {"row_count": 2},
    }
    assert len(finance_runtime.calls) == 1
    assert finance_runtime.calls[0]["effective_as_of"] == CUTOFF
    assert finance_runtime.calls[0]["allowed_symbols"] == [
        "600001.SH",
        "000002.SZ",
    ]
    assert "as_of" not in runtime.store.load_revision(
        "ct_rank_selector", 1
    )["input_schema"]["properties"]


def test_strategy_backtest_runs_through_real_custom_tool_host(tmp_path):
    dates = tuple(dt.date(2026, 7, day) for day in (20, 21, 22, 23, 24))
    symbols = ("600001.SH", "000002.SZ")
    bars = []
    for symbol, base in zip(symbols, (Decimal("10"), Decimal("20"))):
        for index, date in enumerate(dates):
            close = base + Decimal(index)
            bars.append(
                Bar(
                    date=date,
                    symbol=symbol,
                    open=close,
                    high=close + Decimal("1"),
                    low=close - Decimal("1"),
                    close=close,
                    volume=Decimal("100000"),
                )
            )
    data = InMemoryMarketData(
        bars,
        instruments=[Instrument(symbol=item, lot_size=1) for item in symbols],
        calendar=dates,
        source_name="historical_custom_tool_host_fixture",
    )
    code = """
from custom_tool_sdk import finance_query


def run(inputs):
    result = finance_query(
        'r1 = stock.quote.dynamic_cal(task = "选择股票", k = 2, realtime = 0) -> code, tradedate'
    )
    rows = result.get('rows') or []
    selected = [
        {'stock_code': row['code'] + ('.SH' if row['code'].startswith('6') else '.SZ')}
        for row in rows
    ]
    return {
        'selected_stocks': selected,
        'as_of_date': max((row['tradedate'] for row in rows), default=''),
        'key_process_info': {'row_count': len(rows)},
    }
""".strip()

    class RotatingHistoricalFinanceRuntime:
        def __init__(self):
            self.calls = []

        def execute_historical_request(self, **kwargs):
            self.calls.append(kwargs)
            cutoff = str(kwargs["effective_as_of"])
            selected = "600001" if len(self.calls) % 2 else "000002"
            return {
                "ok": True,
                "validation": {"ok": True, "errors": []},
                "result": {
                    "name": "r1",
                    "api": "stock.quote.dynamic_cal",
                    "columns": ["code", "tradedate"],
                    "data": {
                        "status": "ok",
                        "rows": [{"code": selected, "tradedate": cutoff}],
                    },
                },
                "execution": {"ok": True, "status": "ok"},
            }

    store = _store(tmp_path)
    store.save_draft(_strategy_bundle(code=code), owner_id="owner-a")
    finance_runtime = RotatingHistoricalFinanceRuntime()
    runtime = _ControlledFixtureRuntime(
        store=store,
        python_runtime=PythonExecutionRuntime(allow_unsafe_backends=True),
        runtime_root=str(tmp_path / "runtime"),
        finance_runtime=finance_runtime,  # type: ignore[arg-type]
    )
    host = CustomToolHistoricalReplayHost(
        owner_ids=["owner-a"],
        store=store,
        runtime=runtime,
    )
    profile = StrategyRuntimeProfile.from_mapping(
        store.load_revision("ct_rank_selector", 1)["strategy_runtime_profile"]
    )
    plan = StrategyRunResolver(
        calendar=MarketDataTradingCalendar(data)
    ).resolve(
        {
            "strategy_ref": _ref().to_dict(),
            "parameters": {},
            "scope": {"targets": list(symbols)},
            "temporal": {
                "as_of": dates[-1].isoformat(),
                "run_sessions": 3,
            },
        },
        runtime_profile=profile,
    )

    result = StrategyToolBacktestService().run(
        plan=plan,
        data=data,
        input_schema=store.load_revision("ct_rank_selector", 1)["input_schema"],
        output_profile=SelectionOutputProfile(
            candidate_path="data.selected_stocks",
            symbol_field="stock_code",
            output_date_path="data.as_of_date",
        ),
        selection_policy=EqualWeightSelectionPolicy(top_n=1),
        invoker=host,
        initial_cash="10000",
    )

    assert [item.date for item in result.backtest.decisions] == list(dates[2:4])
    assert [dict(item.target_weights) for item in result.backtest.decisions] == [
        {"600001.SH": Decimal("1")},
        {"000002.SZ": Decimal("1")},
    ]
    assert [str(item["effective_as_of"]) for item in finance_runtime.calls] == [
        dates[2].isoformat(),
        dates[3].isoformat(),
    ]
    assert all(item["allowed_symbols"] == list(symbols) for item in finance_runtime.calls)
    assert result.host_preflight["revision"] == 1
    assert result.execution_fingerprint.startswith("sha256:")
