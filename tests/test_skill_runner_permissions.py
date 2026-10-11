import json

import pytest

from src.skill_runtime.models import SkillDefinition
from src.skill_runtime.runner import SkillRunner
from src.skill_runtime.skill_bundle_compiler import SkillBundleCompiler
import src.skill_runtime.runner as runner_module


class _Selector:
    def __init__(self) -> None:
        self.calls = 0

    def select(self, **_kwargs):
        self.calls += 1
        return ["auto_selected_tool"]


class _Loop:
    last_allowed_tools = None

    def __init__(self, **_kwargs) -> None:
        pass

    def run(self, *, skill, input_payload, allowed_tools):
        del skill, input_payload
        type(self).last_allowed_tools = list(allowed_tools)
        return "completed"


def _runner(monkeypatch):
    selector = _Selector()
    runner = SkillRunner(tool_selector=selector)
    monkeypatch.setattr(
        runner,
        "load_skill",
        lambda _name: SkillDefinition(
            name="demo",
            skill_md="demo",
            skill_body="demo",
            output_schema={"type": "object"},
            skill_dir="src/skills/demo",
            config={"default_max_steps": 1},
        ),
    )
    monkeypatch.setattr(runner_module, "AgentLoop", _Loop)
    return runner, selector


def test_explicit_empty_allowed_tools_is_deny_all(monkeypatch):
    runner, selector = _runner(monkeypatch)

    result = runner.run("demo", {}, allowed_tools=[])

    assert result == "completed"
    assert selector.calls == 0
    assert _Loop.last_allowed_tools == []


def test_none_allowed_tools_uses_skill_selector(monkeypatch):
    runner, selector = _runner(monkeypatch)

    result = runner.run("demo", {}, allowed_tools=None)

    assert result == "completed"
    assert selector.calls == 1
    assert _Loop.last_allowed_tools == ["auto_selected_tool"]


def test_retired_skill_cannot_run_even_when_called_directly(monkeypatch):
    runner, selector = _runner(monkeypatch)
    monkeypatch.setattr(
        runner,
        "load_skill",
        lambda _name: SkillDefinition(
            name="retired_demo",
            skill_md="demo",
            skill_body="demo",
            output_schema={"type": "object"},
            skill_dir="src/skills/retired_demo",
            config={
                "default_max_steps": 1,
                "availability": {
                    "lifecycle": "retired",
                    "retrieval_mode": "direct_only",
                },
            },
        ),
    )

    with pytest.raises(ValueError, match="is not active"):
        runner.run("retired_demo", {}, allowed_tools=[])

    assert selector.calls == 0


def test_retired_skill_cannot_compile_an_async_execution_plan(tmp_path):
    skill_dir = tmp_path / "retired_demo"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Retired demo\n", encoding="utf-8")
    (skill_dir / "schema.json").write_text(
        json.dumps({"type": "object"}),
        encoding="utf-8",
    )
    (skill_dir / "skill.json").write_text(
        json.dumps(
            {
                "availability": {
                    "lifecycle": "retired",
                    "retrieval_mode": "direct_only",
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="is not active"):
        SkillBundleCompiler(skills_root=tmp_path).build_execution_plan(
            skill_name="retired_demo",
            input_payload={},
        )
