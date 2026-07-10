from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class SkillDefinition:
    name: str
    skill_md: str
    skill_body: str
    output_schema: Dict[str, Any]
    skill_dir: str
    config: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolSpec:
    name: str
    schema: Dict[str, Any]
    description: str = ""
    usage_notes: List[str] = field(default_factory=list)


@dataclass
class ToolCall:
    name: str
    arguments: Dict[str, Any]
    execution_profile: str = "real"
    call_id: str = ""


@dataclass
class ToolExecutionResult:
    call: ToolCall
    result: Dict[str, Any]


@dataclass
class AgentStep:
    index: int
    assistant_raw: str
    tool_call: Optional[ToolCall] = None
    tool_result: Optional[Dict[str, Any]] = None
    prompt_context: Optional[Dict[str, Any]] = None
    tool_profile: Optional[Dict[str, Any]] = None
    retention_plan: Optional[Dict[str, Any]] = None
    render_artifacts: Optional[Dict[str, Any]] = None
    final_output: Optional[Dict[str, Any]] = None
    validation_error: str = ""
    llm_usage: Dict[str, Any] = field(default_factory=dict)
    llm_calls: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class SkillRunResult:
    ok: bool
    skill_name: str
    final_output: Dict[str, Any]
    steps: List[AgentStep] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "skill_name": self.skill_name,
            "final_output": self.final_output,
            "steps": [
                {
                    "index": step.index,
                    "assistant_raw": step.assistant_raw,
                    "tool_call": {
                        "name": step.tool_call.name,
                        "arguments": step.tool_call.arguments,
                        "execution_profile": step.tool_call.execution_profile,
                        "call_id": step.tool_call.call_id,
                    }
                    if step.tool_call
                    else None,
                    "tool_result": step.tool_result,
                    "prompt_context": step.prompt_context,
                    "tool_profile": step.tool_profile,
                    "retention_plan": step.retention_plan,
                    "render_artifacts": step.render_artifacts,
                    "final_output": step.final_output,
                    "validation_error": step.validation_error,
                    "llm_usage": step.llm_usage,
                    "llm_calls": step.llm_calls,
                }
                for step in self.steps
            ],
            "error": self.error,
        }
