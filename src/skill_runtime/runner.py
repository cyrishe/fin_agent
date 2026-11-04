import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.skill_runtime.agent_loop import AgentLoop
from src.skill_runtime.availability import legacy_skill_is_active
from src.skill_runtime.models import SkillDefinition, SkillRunResult
from src.skill_runtime.schema_validator import SchemaValidator
from src.skill_runtime.step_budget import resolve_step_budget
from src.skill_runtime.tool_adapter import ToolAdapter
from src.skill_runtime.tool_selector import ToolSelector


class SkillRunner:
    def __init__(
        self,
        skills_root: str = "src/skills",
        schema_validator: Optional[SchemaValidator] = None,
        tool_adapter: Optional[ToolAdapter] = None,
        tool_selector: Optional[ToolSelector] = None,
    ) -> None:
        self.skills_root = Path(skills_root)
        self.schema_validator = schema_validator or SchemaValidator()
        self.tool_adapter = tool_adapter or ToolAdapter()
        self.tool_selector = tool_selector or ToolSelector()

    def load_skill(self, skill_name: str) -> SkillDefinition:
        skill_dir = self.skills_root / skill_name
        skill_md_path = skill_dir / "SKILL.md"
        schema_path = skill_dir / "schema.json"
        config_path = skill_dir / "skill.json"
        if not skill_md_path.exists():
            raise FileNotFoundError(f"missing skill file: {skill_md_path}")
        if not schema_path.exists():
            raise FileNotFoundError(f"missing schema file: {schema_path}")
        config = {}
        if config_path.exists():
            config = json.loads(config_path.read_text(encoding="utf-8"))
        skill_md_text = skill_md_path.read_text(encoding="utf-8")
        skill_body = str(config.get("skill_body") or "").strip() or skill_md_text
        return SkillDefinition(
            name=skill_name,
            skill_md=skill_md_text,
            skill_body=skill_body,
            output_schema=json.loads(schema_path.read_text(encoding="utf-8")),
            skill_dir=str(skill_dir),
            config=config,
        )

    def require_active_skill(self, skill_name: str) -> SkillDefinition:
        """Load a compiled Skill while enforcing its execution lifecycle."""

        skill = self.load_skill(skill_name)
        if not legacy_skill_is_active(skill.config):
            raise ValueError(f"skill '{skill.name}' is not active")
        return skill

    def run(
        self,
        skill_name: str,
        input_payload: Dict[str, Any],
        *,
        allowed_tools: Optional[List[str]] = None,
        max_steps: Optional[int] = None,
        enable_think: bool = False,
        default_execution_profile: str = "real",
        runtime_context: Optional[Dict[str, Any]] = None,
        event_handler: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> SkillRunResult:
        skill = self.require_active_skill(skill_name)
        # ``None`` means the caller delegates selection to the Skill policy.
        # An explicit empty list is a deny-all grant and must never fall back
        # to automatic selection.
        resolved_tools = (
            self.tool_selector.select(
                skill_name=skill.name,
                skill_md=skill.skill_md,
                skill_config=skill.config,
                input_payload=input_payload,
            )
            if allowed_tools is None
            else list(allowed_tools)
        )
        resolved_max_steps = resolve_step_budget(
            base_max_steps=(
                int(max_steps)
                if max_steps is not None
                else int(skill.config.get("default_max_steps", 6) or 6)
            ),
            tool_mode=str(((skill.config.get("tool_policy") or {}).get("mode") or "strict")).strip(),
            selected_tools=resolved_tools,
            required_tools_before_final=skill.config.get("required_tools_before_final") or [],
        )
        loop = AgentLoop(
            tool_adapter=self.tool_adapter,
            schema_validator=self.schema_validator,
            max_steps=resolved_max_steps,
            enable_think=bool(skill.config.get("enable_think", enable_think)),
            default_execution_profile=default_execution_profile,
            runtime_context=runtime_context,
            event_handler=event_handler,
        )
        return loop.run(
            skill=skill,
            input_payload=input_payload,
            allowed_tools=resolved_tools,
        )
