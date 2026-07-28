from pathlib import Path
import time

import claude_agent_sdk

from src.services.agent_providers.claude import ClaudeSdkSkillHarness
from src.services.finance_claude_session_service import (
    FinanceClaudeSessionService,
    _append_runtime_context,
)


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


class ResultMessage:
    def __init__(self, result: str = "ok") -> None:
        self.result = result
        self.is_error = False
        self.subtype = "success"


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
    FakeClaudeClient.instances = []
    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", FakeClaudeClient)
    monkeypatch.setattr(
        ClaudeSdkSkillHarness,
        "provider_env",
        lambda self: {"ANTHROPIC_AUTH_TOKEN": "test-token"},
    )
    return FinanceClaudeSessionService(
        enabled=True,
        root_dir=tmp_path / "sessions",
        log_path=tmp_path / "events.jsonl",
        **kwargs,
    )


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
    service = FinanceClaudeSessionService(
        enabled=True,
        root_dir=tmp_path / "sessions",
        log_path=tmp_path / "events.jsonl",
        system_prompt_path=prompt_path,
        skill_root=tmp_path,
        skill_names=[
            "fin-agent-finance-business:earnings-analysis",
        ],
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
