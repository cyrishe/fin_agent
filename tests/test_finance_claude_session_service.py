import asyncio
import json
from pathlib import Path
import time

import claude_agent_sdk
import pytest

from src.scenarios.financial_qa.tools import FinanceDataQueryCcTools
from src.services.agent_providers.claude import ClaudeSdkSkillHarness
from src.services.finance_claude_session_service import (
    FinanceClaudeSessionService,
    _append_runtime_context,
)
from src.services.session_variable_store_service import SessionVariableStoreService


def test_shadow_service_preserves_session_and_isolates_owner_threads(tmp_path: Path) -> None:
    calls = []

    def fake_runner(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "result": "ok", "stream_event_count": 3, "text_delta_count": 1}

    service = FinanceClaudeSessionService(
        enabled=True,
        root_dir=tmp_path / "sessions",
        log_path=tmp_path / "events.jsonl",
        turn_runner=fake_runner,
    )
    first = service.run_turn(thread_id=7, owner_id="owner-a", user_text="first")
    second = service.run_turn(thread_id=7, owner_id="owner-a", user_text="second")
    other = service.run_turn(thread_id=7, owner_id="owner-b", user_text="other")

    assert first["resumed"] is False
    assert second["resumed"] is True
    assert first["session_id"] == second["session_id"]
    assert other["session_id"] != first["session_id"]
    assert calls[0]["resume"] is False
    assert calls[1]["resume"] is True
    assert calls[2]["session_dir"] != calls[0]["session_dir"]
    assert calls[0]["tool_context"]["_agent_runtime_scope"] == calls[1]["tool_context"]["_agent_runtime_scope"]
    assert calls[2]["tool_context"]["_agent_runtime_scope"] != calls[0]["tool_context"]["_agent_runtime_scope"]


def test_custom_tool_flow_id_isolates_cc_sessions_within_one_thread(
    tmp_path: Path,
) -> None:
    calls = []

    def fake_runner(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "result": "ok"}

    service = FinanceClaudeSessionService(
        enabled=True,
        root_dir=tmp_path / "sessions",
        log_path=tmp_path / "events.jsonl",
        warm_pool_size=0,
        turn_runner=fake_runner,
    )
    base_context = {
        "entry": "custom_tool_flow",
        "turn_mode": "tool_development",
    }
    first = service.run_turn(
        thread_id=7,
        owner_id="owner-a",
        user_text="first",
        context={**base_context, "custom_tool_flow_id": "flow-a"},
    )
    followup = service.run_turn(
        thread_id=7,
        owner_id="owner-a",
        user_text="followup",
        context={**base_context, "custom_tool_flow_id": "flow-a"},
    )
    new_tool = service.run_turn(
        thread_id=7,
        owner_id="owner-a",
        user_text="new tool",
        context={**base_context, "custom_tool_flow_id": "flow-b"},
    )

    assert first["resumed"] is False
    assert followup["resumed"] is True
    assert followup["session_id"] == first["session_id"]
    assert new_tool["resumed"] is False
    assert new_tool["session_id"] != first["session_id"]
    assert calls[0]["session_dir"] == calls[1]["session_dir"]
    assert calls[2]["session_dir"] != calls[0]["session_dir"]


def test_disabled_shadow_service_does_not_schedule_work(tmp_path: Path) -> None:
    service = FinanceClaudeSessionService(
        enabled=False,
        root_dir=tmp_path / "sessions",
        log_path=tmp_path / "events.jsonl",
        turn_runner=lambda **kwargs: {"ok": True},
    )

    assert service.submit(thread_id=1, owner_id="owner", user_text="question") is False
    assert not (tmp_path / "events.jsonl").exists()


def test_shadow_prompt_contains_only_current_turn_delta() -> None:
    prompt = FinanceClaudeSessionService.build_user_prompt(
        "那五粮液呢？",
        {
            "selected_agent": "investment_analyst",
            "turn_mode": "normal_qa",
            "entry": "agent_route",
            "has_custom_tool_state": True,
            "ui_action": {"action_id": "custom_tool.confirm_design", "label": "确认当前设计"},
            "ignored_large_payload": "x" * 10_000,
        },
    )

    assert "那五粮液呢？" in prompt
    assert "确认当前设计" in prompt
    assert "investment_analyst" not in prompt
    assert "normal_qa" not in prompt
    assert "agent_route" not in prompt
    assert "自定义工具开发现场" not in prompt
    assert "ignored_large_payload" not in prompt


def test_runtime_index_is_appended_without_replacing_current_turn() -> None:
    class _Runtime:
        @staticmethod
        def current_context_prompt():
            return '{"results": [{"result_name": "r1"}]}'

    prompt = _append_runtime_context("那五粮液呢？", _Runtime())

    assert prompt.startswith("那五粮液呢？")
    assert "[系统提供的当前运行时索引]" in prompt
    assert '"result_name": "r1"' in prompt


def test_runtime_context_does_not_include_evaluation_stage_gate(tmp_path: Path, monkeypatch) -> None:
    calls = []
    monkeypatch.setenv("FINANCE_CC_REQUIREMENT_ONLY", "1")

    def fake_runner(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "result": "ok"}

    service = FinanceClaudeSessionService(
        enabled=True,
        root_dir=tmp_path / "sessions",
        log_path=tmp_path / "events.jsonl",
        turn_runner=fake_runner,
    )

    service.run_turn(thread_id=9, owner_id="owner-a", user_text="做一个选股工具")

    assert "_finance_cc_requirement_only" not in calls[0]["tool_context"]


def test_turn_feedback_does_not_rotate_the_cc_session(tmp_path: Path) -> None:
    calls = []

    def fake_runner(**kwargs):
        calls.append(kwargs)
        return {"result": "ok" if len(calls) > 1 else "failed", "error": "failed"}

    service = FinanceClaudeSessionService(
        enabled=True,
        root_dir=tmp_path / "sessions",
        log_path=tmp_path / "events.jsonl",
        turn_runner=fake_runner,
    )
    first = service.run_turn(thread_id=8, owner_id="owner-a", user_text="first")
    followup = service.run_turn(thread_id=8, owner_id="owner-a", user_text="second")
    resumed = service.run_turn(thread_id=8, owner_id="owner-a", user_text="third")

    assert first["error"] == "failed"
    assert followup["resumed"] is True
    assert followup["session_id"] == first["session_id"]
    assert resumed["resumed"] is True
    assert resumed["session_id"] == followup["session_id"]


def test_transport_failure_rotates_session_instead_of_resuming_it(tmp_path: Path) -> None:
    calls = []

    def fake_runner(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {
                "result": "",
                "error": "Finance CC first response timed out after 1s",
                "failure_kind": "first_response_timeout",
            }
        return {"result": "ok", "error": ""}

    service = FinanceClaudeSessionService(
        enabled=True,
        root_dir=tmp_path / "sessions",
        log_path=tmp_path / "events.jsonl",
        warm_pool_size=0,
        turn_runner=fake_runner,
    )

    first = service.run_turn(thread_id=8, owner_id="owner-a", user_text="first")
    followup = service.run_turn(thread_id=8, owner_id="owner-a", user_text="second")

    assert first["failure_kind"] == "first_response_timeout"
    assert followup["resumed"] is False
    assert followup["session_id"] != first["session_id"]
    assert calls[1]["resume"] is False
    marker = json.loads(next((tmp_path / "sessions").rglob("session.json")).read_text())
    assert marker["resumable"] is True
    assert marker["generation"] == 1


class ResultMessage:
    def __init__(self, result: str = "ok") -> None:
        self.result = result
        self.is_error = False
        self.subtype = "success"
        self.usage = {"input_tokens": 12, "output_tokens": 4}


class FakeClaudeClient:
    instances = []

    def __init__(self, options) -> None:
        self.options = options
        self.prompts = []
        self.connect_count = 0
        self.disconnect_count = 0
        self.__class__.instances.append(self)

    async def connect(self) -> None:
        self.connect_count += 1

    async def disconnect(self) -> None:
        self.disconnect_count += 1

    async def query(self, prompt: str) -> None:
        self.prompts.append(prompt)

    async def receive_response(self):
        yield ResultMessage()


def _pooled_service(tmp_path: Path, monkeypatch, **kwargs) -> FinanceClaudeSessionService:
    client_class = kwargs.pop("_client_class", FakeClaudeClient)
    provider_env = kwargs.pop(
        "_provider_env",
        {"ANTHROPIC_AUTH_TOKEN": "test-token"},
    )
    client_class.instances = []
    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", client_class)
    monkeypatch.setattr(
        ClaudeSdkSkillHarness,
        "provider_env",
        lambda self: dict(provider_env),
    )
    kwargs.setdefault("warm_pool_size", 0)
    return FinanceClaudeSessionService(
        enabled=True,
        root_dir=tmp_path / "sessions",
        log_path=tmp_path / "events.jsonl",
        **kwargs,
    )


def test_finance_skill_snapshot_revision_changes_runtime_fingerprint(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _pooled_service(tmp_path, monkeypatch, skill_names=[])
    try:
        first = service._runtime_options(
            {"_finance_skill_catalog_revision": "revision-a"}
        )
        unchanged = service._runtime_options(
            {"_finance_skill_catalog_revision": "revision-a"}
        )
        changed = service._runtime_options(
            {"_finance_skill_catalog_revision": "revision-b"}
        )

        assert first["fingerprint"] == unchanged["fingerprint"]
        assert first["fingerprint"] != changed["fingerprint"]
    finally:
        service.close()


def test_finance_skill_snapshot_provider_switches_root_and_visible_skills(
    tmp_path: Path,
    monkeypatch,
) -> None:
    first_root = tmp_path / "snapshot-a"
    second_root = tmp_path / "snapshot-b"
    first_root.mkdir()
    second_root.mkdir()
    snapshot = {
        "revision": "revision-a",
        "runtime_root": str(first_root),
        "skill_names": ["finance:first"],
    }
    service = _pooled_service(
        tmp_path,
        monkeypatch,
        skill_root=tmp_path / "fallback",
        skill_names=["finance:fallback"],
        skill_snapshot_provider=lambda: dict(snapshot),
    )
    try:
        first = service._runtime_options({})
        snapshot.update(
            {
                "revision": "revision-b",
                "runtime_root": str(second_root),
                "skill_names": ["finance:second"],
            }
        )
        second = service._runtime_options({})

        assert first["skill_root"] == first_root
        assert first["effective_skill_names"] == ("finance:first",)
        assert second["skill_root"] == second_root
        assert second["effective_skill_names"] == ("finance:second",)
        assert first["fingerprint"] != second["fingerprint"]

        service.run_turn(
            thread_id=91,
            owner_id="owner-a",
            user_text="use current skill snapshot",
        )

        options = FakeClaudeClient.instances[0].options
        assert options.skills == ["finance:second"]
        assert options.plugins == [
            {"type": "local", "path": str(second_root.resolve())}
        ]

        snapshot["skill_names"] = []
        empty = service._runtime_options({})
        assert empty["effective_skill_names"] == ()
    finally:
        service.close()


def test_live_session_rebuilds_and_resumes_after_skill_snapshot_switch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    first_root = tmp_path / "snapshots" / "revision-a"
    second_root = tmp_path / "snapshots" / "revision-b"
    first_root.mkdir(parents=True)
    second_root.mkdir()
    snapshot = {
        "revision": "revision-a",
        "runtime_root": str(first_root),
        "skill_names": ["finance:first"],
    }
    service = _pooled_service(
        tmp_path,
        monkeypatch,
        skill_root=first_root,
        skill_names=["finance:first"],
        skill_snapshot_provider=lambda: dict(snapshot),
    )
    try:
        first = service.run_turn(
            thread_id=92,
            owner_id="owner-a",
            user_text="first revision",
        )
        snapshot.update(
            {
                "revision": "revision-b",
                "runtime_root": str(second_root),
                "skill_names": ["finance:second"],
            }
        )

        second = service.run_turn(
            thread_id=92,
            owner_id="owner-a",
            user_text="second revision",
        )

        assert second["session_id"] == first["session_id"]
        assert second["resumed"] is True
        assert len(FakeClaudeClient.instances) == 2
        old_client, new_client = FakeClaudeClient.instances
        assert old_client.disconnect_count == 1
        assert new_client.options.resume == first["session_id"]
        assert new_client.options.session_id is None
        assert new_client.options.skills == ["finance:second"]
        assert new_client.options.plugins == [
            {"type": "local", "path": str(second_root.resolve())}
        ]
    finally:
        service.close()


def test_turn_pinned_skill_binding_wins_over_a_concurrent_reload(
    tmp_path: Path,
    monkeypatch,
) -> None:
    snapshot_base = tmp_path / "snapshots"
    old_revision = "a" * 64
    new_revision = "b" * 64
    old_root = snapshot_base / old_revision
    new_root = snapshot_base / new_revision
    old_root.mkdir(parents=True)
    new_root.mkdir()
    current = {
        "revision": new_revision,
        "runtime_root": str(new_root),
        "skill_names": ["finance:new"],
    }
    service = _pooled_service(
        tmp_path,
        monkeypatch,
        skill_root=old_root,
        skill_names=["finance:old"],
        skill_snapshot_provider=lambda: dict(current),
    )
    try:
        old_turn_context = {
            "_finance_skill_catalog_revision": old_revision,
            "_finance_skill_runtime_binding": {
                "revision": old_revision,
                "runtime_root": str(old_root),
                "skill_names": ["finance:old"],
            },
            "skill_tool_access": {
                "old": ["financial_news_search"],
            },
        }

        pinned = service._runtime_options(old_turn_context)
        next_turn = service._runtime_options({})

        assert pinned["skill_revision"] == old_revision
        assert pinned["skill_root"] == old_root.resolve()
        assert pinned["effective_skill_names"] == ("finance:old",)
        assert next_turn["skill_revision"] == new_revision
        assert next_turn["skill_root"] == new_root
        assert next_turn["effective_skill_names"] == ("finance:new",)

        with pytest.raises(RuntimeError, match="invalid Finance Skill runtime binding"):
            service._runtime_options(
                {
                    "_finance_skill_catalog_revision": old_revision,
                    "_finance_skill_runtime_binding": {
                        "revision": old_revision,
                        "runtime_root": str(tmp_path / "outside" / old_revision),
                        "skill_names": ["finance:forged"],
                    },
                }
            )
    finally:
        service.close()


def test_business_result_is_not_truncated_by_observability_log(tmp_path: Path) -> None:
    full_answer = "完整金融分析。" * 500

    service = FinanceClaudeSessionService(
        enabled=True,
        root_dir=tmp_path / "sessions",
        log_path=tmp_path / "events.jsonl",
        warm_pool_size=0,
        turn_runner=lambda **_kwargs: {
            "result": full_answer,
            "error": "",
        },
    )
    try:
        result = service.run_turn(
            thread_id=99,
            owner_id="owner-a",
            user_text="请做完整分析",
        )
    finally:
        service.close()

    logged = json.loads((tmp_path / "events.jsonl").read_text(encoding="utf-8"))
    assert result["result"] == full_answer
    assert len(result["result"]) > 2_000
    assert len(logged["result"]) == 2_000


def test_dashscope_loopback_bridge_is_never_sent_through_inherited_proxy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NO_PROXY", "internal.example")
    monkeypatch.setattr(
        "src.services.fixed_upstream_loopback_bridge."
        "FixedUpstreamLoopbackBridge.start",
        lambda self: "http://127.0.0.1:32109/local-token",
    )
    service = _pooled_service(
        tmp_path,
        monkeypatch,
        provider="dashscope",
        _provider_env={
            "ANTHROPIC_AUTH_TOKEN": "test-token",
            "ANTHROPIC_BASE_URL": "https://dashscope.aliyuncs.com/apps/anthropic",
        },
    )
    try:
        result = service.run_turn(
            thread_id=6,
            owner_id="owner-a",
            user_text="first",
        )

        assert result["ok"] is True
        child_env = FakeClaudeClient.instances[0].options.env
        assert child_env["ANTHROPIC_BASE_URL"].startswith(
            "http://127.0.0.1:"
        )
        assert child_env["NO_PROXY"].split(",") == [
            "internal.example",
            "127.0.0.1",
            "localhost",
            "::1",
        ]
        assert child_env["no_proxy"] == child_env["NO_PROXY"]
    finally:
        service.close()


def test_live_client_is_reused_for_followup_turns(tmp_path: Path, monkeypatch) -> None:
    service = _pooled_service(tmp_path, monkeypatch)
    try:
        first = service.run_turn(thread_id=7, owner_id="owner-a", user_text="first")
        second = service.run_turn(thread_id=7, owner_id="owner-a", user_text="second")

        assert first["resumed"] is False
        assert second["resumed"] is True
        assert first["client_reused"] is False
        assert second["client_reused"] is True
        assert len(FakeClaudeClient.instances) == 1
        assert FakeClaudeClient.instances[0].connect_count == 1
        assert len(FakeClaudeClient.instances[0].prompts) == 2
        assert second["llm_usage"] == {
            "prompt_tokens": 12,
            "completion_tokens": 4,
            "total_tokens": 16,
            "call_count": 1,
        }
    finally:
        service.close()


def test_tool_development_progress_uses_tool_language_not_financial_qa_copy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _pooled_service(tmp_path, monkeypatch)
    events = []
    try:
        result = service.run_turn(
            thread_id=71,
            owner_id="owner-a",
            user_text="创建一个金叉工具",
            context={
                "turn_mode": "tool_development",
                "entry": "custom_tool_flow",
                "custom_tool_state": {},
            },
            event_sink=events.append,
        )

        visible_text = "\n".join(
            str(item.get("content") or "")
            for item in events
            if item.get("type") == "reasoning_summary_delta"
        )
        assert result["ok"] is True
        assert "自定义工具创建" in visible_text
        assert "工具需求与设计" == events[1]["metadata"]["title"]
        assert "金融问答" not in visible_text
        assert "金融口径" not in visible_text
        assert "本题" not in visible_text
    finally:
        service.close()


def test_tool_understanding_progress_keeps_one_identity_when_active_stage_changes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class ToolUseBlock:
        id = "save-design-1"
        name = "Skill"
        input = {"skill": "fin-agent-finance-business:stock-research"}

    class AssistantMessage:
        content = [ToolUseBlock()]

    class StageChangingClient(FakeClaudeClient):
        async def receive_response(self):
            yield AssistantMessage()
            yield ResultMessage("设计已保存。")

    service = _pooled_service(
        tmp_path,
        monkeypatch,
        _client_class=StageChangingClient,
    )
    events = []
    try:
        result = service.run_turn(
            thread_id=73,
            owner_id="owner-a",
            user_text="继续完善设计",
            context={
                "turn_mode": "tool_development",
                "entry": "custom_tool_flow",
                "custom_tool_state": {"requirement_brief": "创建一个选股工具"},
            },
            event_sink=events.append,
        )

        understanding = [
            item
            for item in events
            if item.get("metadata", {}).get("progress_id")
            == "custom_tool_understanding"
        ]
        assert result["ok"] is True
        assert [item["metadata"]["status"] for item in understanding] == [
            "running",
            "completed",
        ]
        assert {item["metadata"]["stage"] for item in understanding} == {
            "design"
        }
    finally:
        service.close()


def test_first_response_watchdog_fails_fast_with_recoverable_tool_progress(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class NoResponseClient(FakeClaudeClient):
        async def receive_response(self):
            await asyncio.sleep(60)
            yield ResultMessage()

    service = _pooled_service(
        tmp_path,
        monkeypatch,
        _client_class=NoResponseClient,
        first_response_timeout_seconds=0.02,
    )
    events = []
    started_at = time.monotonic()
    try:
        result = service.run_turn(
            thread_id=72,
            owner_id="owner-a",
            user_text="创建一个选股工具",
            context={
                "turn_mode": "tool_development",
                "entry": "custom_tool_flow",
                "custom_tool_state": {},
            },
            event_sink=events.append,
        )

        assert time.monotonic() - started_at < 1
        assert result["failure_kind"] == "first_response_timeout"
        assert result["stream_event_count"] == 0
        assert any(
            item.get("metadata", {}).get("status") == "error"
            and "需求已经保留" in str(item.get("content") or "")
            for item in events
        )
        marker = json.loads(
            next((tmp_path / "sessions").rglob("session.json")).read_text()
        )
        assert marker["resumable"] is False
        assert marker["generation"] == 1
    finally:
        service.close()


def test_first_response_watchdog_ignores_sdk_lifecycle_message(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class SystemMessage:
        pass

    class LifecycleOnlyClient(FakeClaudeClient):
        async def receive_response(self):
            yield SystemMessage()
            await asyncio.sleep(60)
            yield ResultMessage()

    service = _pooled_service(
        tmp_path,
        monkeypatch,
        _client_class=LifecycleOnlyClient,
        first_response_timeout_seconds=0.02,
    )
    started_at = time.monotonic()
    try:
        result = service.run_turn(
            thread_id=73,
            owner_id="owner-a",
            user_text="创建一个选股工具",
            context={
                "turn_mode": "tool_development",
                "entry": "custom_tool_flow",
                "custom_tool_state": {},
            },
        )

        assert time.monotonic() - started_at < 1
        assert result["failure_kind"] == "first_response_timeout"
        assert result["result"] == ""
    finally:
        service.close()


def test_provider_api_retry_is_transport_failure_and_rotates_session(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class SystemMessage:
        subtype = "api_retry"
        data = {
            "attempt": 1,
            "max_retries": 2,
            "error": "unknown",
        }

    class ResultMessage:
        result = (
            "API Error: Unable to connect to API "
            "(UNKNOWN_CERTIFICATE_VERIFICATION_ERROR)"
        )
        is_error = True
        subtype = "success"
        usage = {}
        api_error_status = None

    class ProviderErrorClient(FakeClaudeClient):
        async def receive_response(self):
            yield SystemMessage()
            yield ResultMessage()

    service = _pooled_service(
        tmp_path,
        monkeypatch,
        _client_class=ProviderErrorClient,
    )
    try:
        result = service.run_turn(
            thread_id=74,
            owner_id="owner-a",
            user_text="创建一个选股工具",
            context={
                "turn_mode": "tool_development",
                "entry": "custom_tool_flow",
                "custom_tool_state": {},
            },
        )

        assert result["failure_kind"] == "provider_api_error"
        assert result["api_retry_count"] == 1
        marker = json.loads(
            next((tmp_path / "sessions").rglob("session.json")).read_text()
        )
        assert marker["resumable"] is False
        assert marker["generation"] == 1
    finally:
        service.close()


def test_agent_profile_is_loaded_once_into_the_cc_harness(
    tmp_path: Path,
    monkeypatch,
) -> None:
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("金融专业问答", encoding="utf-8")
    service = _pooled_service(
        tmp_path,
        monkeypatch,
        system_prompt_path=prompt_path,
        skill_names=[],
    )
    try:
        service.run_turn(
            thread_id=7,
            owner_id="owner-a",
            user_text="贵州茅台收盘价",
            context={"_agent_system_prompt": "Investment Analyst 原有角色提示"},
        )

        options = FakeClaudeClient.instances[0].options
        assert "金融专业问答" in options.system_prompt
        assert "Investment Analyst 原有角色提示" in options.system_prompt
        assert options.skills == []
    finally:
        service.close()


def test_finance_skill_catalog_summary_is_last_in_system_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("金融专业问答", encoding="utf-8")
    service = _pooled_service(
        tmp_path,
        monkeypatch,
        system_prompt_path=prompt_path,
        skill_names=["finance:test"],
    )
    try:
        service.run_turn(
            thread_id=7,
            owner_id="owner-a",
            user_text="比较动量因子",
            context={
                "_agent_system_prompt": "Investment Analyst",
                "_finance_skill_catalog_prompt": (
                    "- factor-analysis: 计算、比较或解释因子"
                ),
            },
        )

        system_prompt = FakeClaudeClient.instances[0].options.system_prompt
        assert system_prompt.endswith(
            "- factor-analysis: 计算、比较或解释因子"
        )
        assert "[当前可用的金融业务 Skill 摘要]" in system_prompt
    finally:
        service.close()


def test_finance_skill_allowlist_limits_native_skill_tools(
    tmp_path: Path,
    monkeypatch,
) -> None:
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("金融专业问答", encoding="utf-8")
    service = _pooled_service(
        tmp_path,
        monkeypatch,
        system_prompt_path=prompt_path,
        skill_root=tmp_path,
        skill_names=[
            "fin-agent-finance-business:market-overview",
            "fin-agent-finance-business:stock-research",
        ],
    )
    try:
        service.run_turn(
            thread_id=7,
            owner_id="owner-a",
            user_text="全面分析宁德时代",
            context={"allowed_finance_skills": ["stock-research"]},
        )

        options = FakeClaudeClient.instances[0].options
        assert options.skills == [
            "fin-agent-finance-business:stock-research"
        ]
        assert "Skill" in options.allowed_tools
    finally:
        service.close()


def test_native_finance_skill_entry_records_exact_skill_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class ToolUseBlock:
        id = "skill-use-1"
        name = "Skill"
        input = {
            "skill": "fin-agent-finance-business:earnings-analysis",
        }

    class AssistantMessage:
        content = [ToolUseBlock()]

    class SkillClient(FakeClaudeClient):
        async def receive_response(self):
            yield AssistantMessage()
            yield ResultMessage("已按财报分析方法完成。")

    class RecordingTools(FinanceDataQueryCcTools):
        runtime = None

        def create_runtime(self):
            self.runtime = super().create_runtime()
            return self.runtime

    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("金融专业问答", encoding="utf-8")
    FakeClaudeClient.instances = []
    SkillClient.instances = []
    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", SkillClient)
    monkeypatch.setattr(
        ClaudeSdkSkillHarness,
        "provider_env",
        lambda self: {"ANTHROPIC_AUTH_TOKEN": "test-token"},
    )
    system_tools = RecordingTools(
        result_store=SessionVariableStoreService(data_root=tmp_path / "data")
    )
    service = FinanceClaudeSessionService(
        enabled=True,
        root_dir=tmp_path / "sessions",
        log_path=tmp_path / "events.jsonl",
        system_prompt_path=prompt_path,
        skill_root=tmp_path,
        skill_names=[
            "fin-agent-finance-business:earnings-analysis",
        ],
        system_tools=system_tools,
    )
    try:
        result = service.run_turn(
            thread_id=8,
            owner_id="owner-a",
            user_text="分析贵州茅台年报的增长质量",
            context={
                "allowed_finance_skills": ["earnings-analysis"],
            },
        )

        assert result["skill_entries"] == [
            {
                "skill_id": "earnings-analysis",
                "qualified_skill": (
                    "fin-agent-finance-business:earnings-analysis"
                ),
            }
        ]
        assert result["agent_tool_names"] == ["Skill"]
        assert system_tools.runtime.tracker["active_skill_ids"] == [
            "earnings-analysis"
        ]
    finally:
        service.close()


def test_cold_client_binds_first_turn_tool_progress_sink(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class RecordingTools(FinanceDataQueryCcTools):
        runtime = None

        def create_runtime(self):
            self.runtime = super().create_runtime()
            return self.runtime

    system_tools = RecordingTools(
        result_store=SessionVariableStoreService(data_root=tmp_path / "data")
    )
    service = _pooled_service(
        tmp_path,
        monkeypatch,
        system_tools=system_tools,
    )
    events = []
    sink = events.append
    try:
        result = service.run_turn(
            thread_id=81,
            owner_id="owner-a",
            user_text="查询贵州茅台行情",
            event_sink=sink,
        )

        assert result["ok"] is True
        assert system_tools.runtime.event_sink is sink
    finally:
        service.close()


def test_native_finance_business_skill_reports_runtime_progress() -> None:
    assert FinanceClaudeSessionService._stage_for_tool(
        "Skill",
        {
            "skill": "fin-agent-finance-business:stock-research",
        },
    ) == "runtime"
    assert FinanceClaudeSessionService._stage_for_tool(
        "Skill",
        {
            "skill": "custom-tool-workflow:requirement",
        },
    ) == "requirement"


def test_live_client_pool_isolates_conversations(tmp_path: Path, monkeypatch) -> None:
    service = _pooled_service(tmp_path, monkeypatch, max_live_clients=2)
    try:
        service.run_turn(thread_id=7, owner_id="owner-a", user_text="first")
        service.run_turn(thread_id=8, owner_id="owner-a", user_text="other")

        assert len(FakeClaudeClient.instances) == 2
        assert all(len(client.prompts) == 1 for client in FakeClaudeClient.instances)
    finally:
        service.close()


def test_warm_pool_is_ready_before_first_conversation_and_replenishes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _pooled_service(
        tmp_path,
        monkeypatch,
        warm_pool_size=2,
        max_live_clients=4,
    )
    try:
        ready = service.prewarm(context={})

        assert ready["warm_clients"] == 2
        assert ready["assigned_live_clients"] == 0
        assert len(FakeClaudeClient.instances) == 2
        assert all(client.connect_count == 1 for client in FakeClaudeClient.instances)
        assert all(client.prompts == [] for client in FakeClaudeClient.instances)

        first = service.run_turn(
            thread_id=7,
            owner_id="owner-a",
            user_text="first",
        )
        deadline = time.monotonic() + 1
        while service.pool_status()["warm_clients"] < 2 and time.monotonic() < deadline:
            time.sleep(0.01)

        prompted = [client for client in FakeClaudeClient.instances if client.prompts]
        assert first["client_prewarmed"] is True
        assert first["client_reused"] is False
        assert len(prompted) == 1
        assert prompted[0].prompts == [
            "用户当前的问题是：\nfirst\n\n请结合当前会话历史和按需读取的系统资产，处理本轮新增信息。"
        ]
        assert service.pool_status()["warm_clients"] == 2
        assert service.pool_status()["assigned_live_clients"] == 1
    finally:
        service.close()


def test_warm_pool_keeps_conversation_sessions_isolated(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _pooled_service(
        tmp_path,
        monkeypatch,
        warm_pool_size=2,
        max_live_clients=4,
    )
    try:
        service.prewarm(context={})
        first = service.run_turn(thread_id=7, owner_id="owner-a", user_text="first")
        second = service.run_turn(thread_id=8, owner_id="owner-a", user_text="second")
        followup = service.run_turn(thread_id=7, owner_id="owner-a", user_text="followup")

        prompted = [client for client in FakeClaudeClient.instances if client.prompts]
        first_client = next(client for client in prompted if client.prompts[0].endswith("处理本轮新增信息。") and "first" in client.prompts[0])
        second_client = next(client for client in prompted if "second" in client.prompts[0])

        assert first["session_id"] != second["session_id"]
        assert followup["session_id"] == first["session_id"]
        assert followup["client_reused"] is True
        assert first_client is not second_client
        assert len(first_client.prompts) == 2
        assert len(second_client.prompts) == 1
    finally:
        service.close()


def test_warm_pool_rebuilds_when_runtime_context_revision_changes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _pooled_service(
        tmp_path,
        monkeypatch,
        warm_pool_size=1,
        max_live_clients=4,
    )
    try:
        service.prewarm(context={"_agent_system_prompt": "revision-a"})

        changed = service.run_turn(
            thread_id=7,
            owner_id="owner-a",
            user_text="changed",
            context={"_agent_system_prompt": "revision-b"},
        )
        deadline = time.monotonic() + 1
        while service.pool_status()["warm_clients"] < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        next_turn = service.run_turn(
            thread_id=8,
            owner_id="owner-a",
            user_text="next",
            context={"_agent_system_prompt": "revision-b"},
        )

        assert changed["client_prewarmed"] is False
        assert next_turn["client_prewarmed"] is True
    finally:
        service.close()


def test_idle_client_is_released_and_later_resumed(tmp_path: Path, monkeypatch) -> None:
    service = _pooled_service(tmp_path, monkeypatch, client_idle_seconds=0.05)
    try:
        first = service.run_turn(thread_id=7, owner_id="owner-a", user_text="first")
        time.sleep(0.15)
        assert FakeClaudeClient.instances[0].disconnect_count == 1

        second = service.run_turn(thread_id=7, owner_id="owner-a", user_text="second")
        assert second["session_id"] == first["session_id"]
        assert second["resumed"] is True
        assert len(FakeClaudeClient.instances) == 2
        assert FakeClaudeClient.instances[1].options.resume == first["session_id"]
    finally:
        service.close()
