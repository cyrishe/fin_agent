import asyncio
from pathlib import Path

import pytest

from app.config import ROOT_DIR, Settings
from app.contracts import RunRequest
from app.fake_backend import FakeAgentBackend
from app.harness import AgentHarness


def test_fake_harness_has_monotonic_events_and_one_terminal() -> None:
    settings = Settings(root_dir=ROOT_DIR, backend="fake")
    harness = AgentHarness(FakeAgentBackend(), settings)

    async def collect():
        return [
            event
            async for event in harness.stream(
                RunRequest(question="测试", skill_names=["financial-research"]),
                run_id="run_test",
            )
        ]

    events = asyncio.run(collect())

    assert [event.seq for event in events] == list(range(1, len(events) + 1))
    assert events[0].type == "run.started"
    assert [event.type for event in events].count("run.completed") == 1
    assert events[-1].data["session_id"].startswith("fake_")


def test_unknown_skill_is_rejected_before_provider_call() -> None:
    settings = Settings(root_dir=ROOT_DIR, backend="fake")
    harness = AgentHarness(FakeAgentBackend(), settings)

    with pytest.raises(ValueError, match="unknown skills"):
        harness.validate_request(RunRequest(question="测试", skill_names=["missing-skill"]))


def test_disabled_search_is_rejected() -> None:
    settings = Settings(root_dir=ROOT_DIR, backend="fake", web_search_backend="disabled")
    harness = AgentHarness(FakeAgentBackend(), settings)

    with pytest.raises(ValueError, match="disabled"):
        harness.validate_request(RunRequest(question="测试", enable_web_search=True))


def test_session_resume_requires_host_ownership_gate() -> None:
    settings = Settings(root_dir=ROOT_DIR, backend="fake", allow_session_resume=False)
    harness = AgentHarness(FakeAgentBackend(), settings)

    with pytest.raises(ValueError, match="ownership"):
        harness.validate_request(RunRequest(question="测试", session_id="session_123"))


def test_fake_harness_returns_trusted_structured_output_mode() -> None:
    harness = AgentHarness(FakeAgentBackend(), Settings(root_dir=ROOT_DIR, backend="fake"))

    async def collect():
        return [
            event
            async for event in harness.stream(
                RunRequest(question="测试", output_mode="research_json"),
                run_id="run_structured",
            )
        ]

    terminal = asyncio.run(collect())[-1]

    assert terminal.type == "run.completed"
    assert terminal.data["structured_output"]["facts"] == []
