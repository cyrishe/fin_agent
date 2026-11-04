from pathlib import Path

from src.services.agent_providers.runtime_context import AgentRuntimeContextAdapter


def test_adapter_reuses_delegated_agent_session_without_business_state(tmp_path: Path) -> None:
    adapter = AgentRuntimeContextAdapter(tmp_path)
    received = []

    def runner(*, state, **_):
        received.append(dict(state["agent_runtime"]))
        session_id = state["agent_runtime"]["session_id"]
        return {
            "state": {
                "tool_name": "demo_tool",
                "agent_runtime": {
                    "session_id": session_id,
                    "provider_session_id": "provider-1",
                },
            },
            "thread_context_patch": {
                "custom_tool_state": {
                    "tool_name": "demo_tool",
                    "agent_runtime": {"session_id": session_id},
                }
            },
            "implementation_meta": {"provider_session_id": "provider-1"},
        }

    first = adapter.invoke(
        scope_id="owner-a:thread-7",
        role="tool_implementation",
        runner=runner,
        state={"tool_name": "demo_tool"},
    )
    second = adapter.invoke(
        scope_id="owner-a:thread-7",
        role="tool_implementation",
        runner=runner,
        state={"tool_name": "demo_tool"},
    )

    assert received[0]["session_id"] == received[1]["session_id"]
    assert received[1]["provider_session_id"] == "provider-1"
    assert "agent_runtime" not in first["state"]
    assert "agent_runtime" not in second["state"]
    assert "agent_runtime" not in second["thread_context_patch"]["custom_tool_state"]
    assert adapter.read(scope_id="owner-a:thread-7", role="tool_implementation") == {
        "session_id": received[0]["session_id"],
        "provider_session_id": "provider-1",
    }


def test_adapter_isolates_agent_roles_and_system_conversations(tmp_path: Path) -> None:
    adapter = AgentRuntimeContextAdapter(tmp_path)

    def runner(*, state, **_):
        return {"state": {"agent_runtime": dict(state["agent_runtime"])}}

    adapter.invoke(
        scope_id="owner-a:thread-7",
        role="tool_implementation",
        runner=runner,
        state={},
    )
    adapter.invoke(
        scope_id="owner-a:thread-7",
        role="finance_cc",
        runner=runner,
        state={},
    )
    adapter.invoke(
        scope_id="owner-a:thread-8",
        role="tool_implementation",
        runner=runner,
        state={},
    )

    coding = adapter.read(scope_id="owner-a:thread-7", role="tool_implementation")
    controller = adapter.read(scope_id="owner-a:thread-7", role="finance_cc")
    other = adapter.read(scope_id="owner-a:thread-8", role="tool_implementation")
    assert len({coding["session_id"], controller["session_id"], other["session_id"]}) == 3


def test_adapter_keeps_logical_session_when_delegate_raises(tmp_path: Path) -> None:
    adapter = AgentRuntimeContextAdapter(tmp_path)
    received = []

    def failing_runner(*, state, **_):
        received.append(state["agent_runtime"]["session_id"])
        raise RuntimeError("post-provider persistence failed")

    try:
        adapter.invoke(
            scope_id="owner-a:thread-9",
            role="tool_implementation",
            runner=failing_runner,
            state={},
        )
    except RuntimeError:
        pass

    def recovered_runner(*, state, **_):
        received.append(state["agent_runtime"]["session_id"])
        return {"state": {"agent_runtime": dict(state["agent_runtime"])}}

    adapter.invoke(
        scope_id="owner-a:thread-9",
        role="tool_implementation",
        runner=recovered_runner,
        state={},
    )
    assert received[0] == received[1]
