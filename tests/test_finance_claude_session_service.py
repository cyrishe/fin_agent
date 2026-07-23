from pathlib import Path

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


def test_requirement_only_mode_is_forwarded_to_runtime_tools(tmp_path: Path) -> None:
    calls = []

    def fake_runner(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "result": "ok"}

    service = FinanceClaudeSessionService(
        enabled=True,
        requirement_only=True,
        root_dir=tmp_path / "sessions",
        log_path=tmp_path / "events.jsonl",
        turn_runner=fake_runner,
    )

    service.run_turn(thread_id=9, owner_id="owner-a", user_text="做一个选股工具")

    assert calls[0]["tool_context"]["_finance_cc_requirement_only"] is True


def test_failed_session_rotates_before_next_turn_then_resumes(tmp_path: Path) -> None:
    calls = []

    def fake_runner(**kwargs):
        calls.append(kwargs)
        return {"ok": len(calls) > 1, "result": "ok" if len(calls) > 1 else "failed", "error": "failed"}

    service = FinanceClaudeSessionService(
        enabled=True,
        root_dir=tmp_path / "sessions",
        log_path=tmp_path / "events.jsonl",
        turn_runner=fake_runner,
    )
    failed = service.run_turn(thread_id=8, owner_id="owner-a", user_text="first")
    recovered = service.run_turn(thread_id=8, owner_id="owner-a", user_text="second")
    resumed = service.run_turn(thread_id=8, owner_id="owner-a", user_text="third")

    assert failed["ok"] is False
    assert recovered["resumed"] is False
    assert recovered["session_id"] != failed["session_id"]
    assert resumed["resumed"] is True
    assert resumed["session_id"] == recovered["session_id"]
