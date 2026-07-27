from pathlib import Path
import time

import claude_agent_sdk

from src.services.agent_providers.claude import ClaudeSdkSkillHarness
from src.services.finance_claude_session_service import FinanceClaudeSessionService


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
