import json
from typing import Any, Dict, List

from src.skill_runtime.models import SkillDefinition, ToolSpec
from src.prompting.prompt_registry import get_prompt_registry


def build_agent_messages(
    skill: SkillDefinition,
    tool_specs: List[ToolSpec],
    input_payload: Dict[str, Any],
    prior_steps: List[Dict[str, Any]],
    agent_runtime_profile: Dict[str, Any] | None = None,
) -> List[Dict[str, str]]:
    tool_lines = []
    for spec in tool_specs:
        tool_payload = {
            "description": spec.description,
            "input_schema": spec.schema,
            "usage_notes": spec.usage_notes,
        }
        tool_lines.append(f"- {spec.name}: {json.dumps(tool_payload, ensure_ascii=False)}")
    registry = get_prompt_registry()
    return registry.render_messages(
        "system.skill_runtime.agent_main",
        {
            "agent_name": str((agent_runtime_profile or {}).get("agent_name") or ""),
            "agent_role": str((agent_runtime_profile or {}).get("role") or ""),
            "agent_system_prompt": str((agent_runtime_profile or {}).get("system_prompt_text") or ""),
            "skill_body": skill.skill_body,
            "output_schema": json.dumps(skill.output_schema, ensure_ascii=False),
            "tool_lines": "\n".join(tool_lines),
            "input_payload": json.dumps(input_payload, ensure_ascii=False),
            "prior_steps": json.dumps(prior_steps, ensure_ascii=False),
        },
    )
