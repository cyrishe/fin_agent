import json
import uuid
from typing import Any, Callable, Dict, List, Tuple

from src.skill_runtime.models import AgentStep, SkillDefinition, SkillRunResult, ToolCall
from src.skill_runtime.prompt_builder import build_agent_messages
from src.skill_runtime.context_retention import reduce_tool_result_for_runtime
from src.skill_runtime.schema_validator import SchemaValidationError, SchemaValidator
from src.skill_runtime.tool_adapter import ToolAdapter
from src.prompting.prompt_registry import get_prompt_registry
from src.utils.ai_service import chat_qwen, chat_qwen_json, extract_first_json


class AgentLoop:
    JSON_REPAIR_ATTEMPTS = 2
    FORMAT_RETRY_ATTEMPTS = 3

    def __init__(
        self,
        tool_adapter: ToolAdapter,
        schema_validator: SchemaValidator,
        max_steps: int = 6,
        enable_think: bool = False,
        default_execution_profile: str = "real",
        runtime_context: Dict[str, Any] | None = None,
        event_handler: Callable[[Dict[str, Any]], None] | None = None,
    ) -> None:
        self.tool_adapter = tool_adapter
        self.schema_validator = schema_validator
        self.max_steps = max(1, int(max_steps))
        self.enable_think = bool(enable_think)
        self.default_execution_profile = str(default_execution_profile or "real").strip() or "real"
        self.runtime_context = dict(runtime_context or {})
        self.event_handler = event_handler

    def run(
        self,
        skill: SkillDefinition,
        input_payload: Dict[str, Any],
        allowed_tools: List[str] | None = None,
    ) -> SkillRunResult:
        steps: List[AgentStep] = []
        tool_specs = self.tool_adapter.list_tool_specs(allowed_tools=allowed_tools)
        called_tools: List[str] = []

        for index in range(1, self.max_steps + 1):
            final_step = index >= self.max_steps
            self._emit_event(
                {
                    "event_type": "step_started",
                    "step_index": index,
                    "stage": "collecting_data" if not final_step else "rendering",
                    "message": f"开始第 {index} 步分析。"
                    if not final_step
                    else f"开始第 {index} 步综合输出。",
                }
            )
            prior_steps = [self._step_to_trace(step) for step in steps]
            step, final_output = self._run_step_with_format_retries(
                index=index,
                skill=skill,
                tool_specs=tool_specs,
                input_payload=input_payload,
                prior_steps=prior_steps,
                called_tools=called_tools,
                final_step=final_step,
            )
            steps.append(step)
            if step.tool_call:
                called_tools.append(step.tool_call.name)
            if final_output is not None:
                self._emit_event(
                    {
                        "event_type": "run_completed",
                        "step_index": index,
                        "stage": "completed",
                        "message": "结构化结果已生成。",
                    }
                )
                return SkillRunResult(
                    ok=True,
                    skill_name=skill.name,
                    final_output=final_output,
                    steps=steps,
                )

        return SkillRunResult(
            ok=False,
            skill_name=skill.name,
            final_output={},
            steps=steps,
            error=f"agent loop exceeded max steps: {self.max_steps}",
        )

    def _run_step_with_format_retries(
        self,
        *,
        index: int,
        skill: SkillDefinition,
        tool_specs: List[Any],
        input_payload: Dict[str, Any],
        prior_steps: List[Dict[str, Any]],
        called_tools: List[str],
        final_step: bool,
    ) -> Tuple[AgentStep, Dict[str, Any] | None]:
        base_messages = build_agent_messages(
            skill,
            tool_specs,
            input_payload,
            prior_steps,
            agent_runtime_profile=self.runtime_context.get("agent_runtime_profile")
            if isinstance(self.runtime_context.get("agent_runtime_profile"), dict)
            else None,
        )
        retry_feedback = ""
        last_raw = ""
        llm_calls: List[Dict[str, Any]] = []

        for _ in range(self.FORMAT_RETRY_ATTEMPTS + 1):
            messages = self._with_format_retry_feedback(base_messages, last_raw, retry_feedback)
            assistant_raw, parsed, parse_error, call_records = self._get_structured_response(messages)
            llm_calls.extend(call_records)
            last_raw = assistant_raw
            step = AgentStep(
                index=index,
                assistant_raw=assistant_raw,
                llm_calls=list(llm_calls),
                llm_usage=self._summarize_llm_usage(llm_calls),
            )

            if not isinstance(parsed, dict):
                retry_feedback = parse_error or "assistant output is not valid json object"
                step.validation_error = retry_feedback
                self._emit_event(
                    {
                        "event_type": "validation_retry",
                        "step_index": index,
                        "stage": "synthesizing",
                        "message": retry_feedback,
                    }
                )
                continue

            action = str(parsed.get("action") or "").strip()
            if action == "tool_call":
                if final_step:
                    retry_feedback = (
                        "当前已经是最后一步，不能继续调用工具。"
                        "请基于现有证据直接输出 final_output；如果证据不足，请明确写出缺失信息和不确定性。"
                    )
                    step.validation_error = retry_feedback
                    self._emit_event(
                        {
                            "event_type": "validation_retry",
                            "step_index": index,
                            "stage": "rendering",
                            "message": retry_feedback,
                        }
                    )
                    continue
                tool_name = str(parsed.get("tool_name") or "").strip()
                arguments = parsed.get("arguments") or {}
                execution_profile = str(
                    parsed.get("execution_profile")
                    or (arguments or {}).get("_execution_profile")
                    or self.default_execution_profile
                ).strip() or self.default_execution_profile
                if not tool_name or not isinstance(arguments, dict):
                    retry_feedback = "invalid tool_call payload"
                    step.validation_error = retry_feedback
                    self._emit_event(
                        {
                            "event_type": "validation_retry",
                            "step_index": index,
                            "stage": "synthesizing",
                            "message": retry_feedback,
                        }
                    )
                    continue
                call = ToolCall(
                    name=tool_name,
                    arguments=arguments,
                    execution_profile=execution_profile,
                    call_id=f"toolcall_{uuid.uuid4().hex[:12]}",
                )
                step.tool_call = call
                self._emit_event(
                    {
                        "event_type": "tool_call_started",
                        "step_index": index,
                        "stage": "collecting_data",
                        "tool_name": tool_name,
                        "arguments": arguments,
                        "execution_profile": execution_profile,
                        "call_id": call.call_id,
                        "message": f"开始调用 {tool_name}",
                    }
                )
                execution_arguments = dict(arguments or {})
                if self.runtime_context:
                    execution_arguments["_runtime"] = dict(self.runtime_context)
                step.tool_result = self.tool_adapter.execute(
                    tool_name,
                    execution_arguments,
                    execution_profile=execution_profile,
                )
                reduced = reduce_tool_result_for_runtime(tool_name, step.tool_result if isinstance(step.tool_result, dict) else {})
                step.prompt_context = reduced.get("prompt_context")
                step.tool_profile = reduced.get("profile")
                step.retention_plan = reduced.get("retention_plan")
                step.render_artifacts = reduced.get("render_artifacts")
                self._emit_event(
                    {
                        "event_type": "tool_call_finished",
                        "step_index": index,
                        "stage": "collecting_data",
                        "tool_name": tool_name,
                        "arguments": arguments,
                        "execution_profile": execution_profile,
                        "call_id": call.call_id,
                        "tool_result": step.tool_result,
                        "prompt_context": step.prompt_context,
                        "render_artifacts": step.render_artifacts,
                        "message": f"{tool_name} 已返回结果",
                    }
                )
                return step, None

            if action == "final":
                final_output = parsed.get("final_output")
                if not isinstance(final_output, dict):
                    retry_feedback = "final_output must be an object"
                    step.validation_error = retry_feedback
                    self._emit_event(
                        {
                            "event_type": "validation_retry",
                            "step_index": index,
                            "stage": "rendering",
                            "message": retry_feedback,
                        }
                    )
                    continue
                try:
                    final_output = self._normalize_final_output(skill=skill, final_output=final_output)
                    self.schema_validator.validate(final_output, skill.output_schema)
                    constraint_check = self._validate_skill_constraints(
                        skill=skill,
                        final_output=final_output,
                        called_tools=called_tools,
                    )
                    blocking_error = str(constraint_check.get("blocking_error") or "").strip()
                    warning_messages = [
                        str(item or "").strip()
                        for item in (constraint_check.get("warnings") or [])
                        if str(item or "").strip()
                    ]
                    if blocking_error:
                        retry_feedback = blocking_error
                        step.validation_error = retry_feedback
                        self._emit_event(
                            {
                                "event_type": "validation_retry",
                                "step_index": index,
                                "stage": "rendering",
                                "message": retry_feedback,
                            }
                        )
                        continue
                    for warning in warning_messages:
                        self._emit_event(
                            {
                                "event_type": "constraint_warning",
                                "step_index": index,
                                "stage": "rendering",
                                "message": warning,
                            }
                        )
                    step.final_output = final_output
                    self._emit_event(
                        {
                            "event_type": "final_ready",
                            "step_index": index,
                            "stage": "rendering",
                            "final_output": final_output,
                            "message": "最终结果已通过校验。",
                        }
                    )
                    return step, final_output
                except SchemaValidationError as exc:
                    retry_feedback = str(exc)
                    step.validation_error = retry_feedback
                    self._emit_event(
                        {
                            "event_type": "validation_retry",
                            "step_index": index,
                            "stage": "rendering",
                            "message": retry_feedback,
                        }
                    )
                    continue

            retry_feedback = f"unsupported action: {action or '<empty>'}"
            step.validation_error = retry_feedback
            self._emit_event(
                {
                    "event_type": "validation_retry",
                    "step_index": index,
                    "stage": "synthesizing",
                    "message": retry_feedback,
                }
            )

        return step, None

    @staticmethod
    def _normalize_final_output(
        *,
        skill: SkillDefinition,
        final_output: Dict[str, Any],
    ) -> Dict[str, Any]:
        normalized = dict(final_output)
        expected_page_type = str(skill.config.get("expected_render_page_type") or "").strip()
        render_payload = normalized.get("render_payload")
        if expected_page_type and isinstance(render_payload, dict):
            render_copy = dict(render_payload)
            render_copy["page_type"] = expected_page_type
            normalized["render_payload"] = render_copy
        return normalized

    @staticmethod
    def _validate_skill_constraints(
        *,
        skill: SkillDefinition,
        final_output: Dict[str, Any],
        called_tools: List[str],
    ) -> Dict[str, Any]:
        warnings: List[str] = []
        required_tools = skill.config.get("required_tools_before_final") or []
        if isinstance(required_tools, list):
            called_tool_set = {str(name or "").strip() for name in called_tools if str(name or "").strip()}
            missing_tools = [str(name).strip() for name in required_tools if str(name).strip() and str(name).strip() not in called_tool_set]
            if missing_tools:
                warnings.append(f"missing preferred tools before final: {', '.join(missing_tools)}")

        expected_page_type = str(skill.config.get("expected_render_page_type") or "").strip()
        if expected_page_type:
            render_payload = final_output.get("render_payload")
            actual_page_type = ""
            if isinstance(render_payload, dict):
                actual_page_type = str(render_payload.get("page_type") or "").strip()
            if actual_page_type != expected_page_type:
                return {
                    "blocking_error": f"render_payload.page_type must be '{expected_page_type}'",
                    "warnings": warnings,
                }

        return {
            "blocking_error": "",
            "warnings": warnings,
        }

    def _get_structured_response(
        self,
        messages: List[Dict[str, str]],
    ) -> Tuple[str, Dict[str, Any] | None, str, List[Dict[str, Any]]]:
        call_records: List[Dict[str, Any]] = []
        try:
            parsed_json, usage = chat_qwen_json(messages, enable_think=self.enable_think)
            call_records.append(
                self._build_llm_call_record(
                    call_type="json_primary",
                    usage=usage,
                    parsed_ok=isinstance(parsed_json, dict),
                )
            )
            if isinstance(parsed_json, dict):
                return json.dumps(parsed_json, ensure_ascii=False), parsed_json, "", call_records
        except Exception:
            pass

        assistant_raw, usage = chat_qwen(messages, enable_think=self.enable_think)
        call_records.append(
            self._build_llm_call_record(
                call_type="text_primary",
                usage=usage,
                parsed_ok=False,
            )
        )
        parsed = extract_first_json(assistant_raw, log_errors=False)
        if isinstance(parsed, dict):
            call_records[-1]["parsed_ok"] = True
            return assistant_raw, parsed, "", call_records

        last_raw = assistant_raw
        for _ in range(self.JSON_REPAIR_ATTEMPTS):
            repair_messages = self._build_json_repair_messages(last_raw)
            repaired_raw, repair_usage = chat_qwen(repair_messages, enable_think=False)
            call_records.append(
                self._build_llm_call_record(
                    call_type="json_repair",
                    usage=repair_usage,
                    parsed_ok=False,
                )
            )
            repaired = extract_first_json(repaired_raw, log_errors=False)
            if isinstance(repaired, dict):
                call_records[-1]["parsed_ok"] = True
                return repaired_raw, repaired, "", call_records
            last_raw = repaired_raw

        return last_raw, None, "assistant output is not valid json object after json repair retries", call_records

    @staticmethod
    def _build_json_repair_messages(bad_output: str) -> List[Dict[str, str]]:
        return get_prompt_registry().render_messages(
            "system.skill_runtime.json_repair",
            {"bad_output": bad_output},
        )

    @staticmethod
    def _with_format_retry_feedback(
        base_messages: List[Dict[str, str]],
        assistant_raw: str,
        retry_feedback: str,
    ) -> List[Dict[str, str]]:
        if not retry_feedback:
            return list(base_messages)
        return list(base_messages) + get_prompt_registry().render_messages(
            "system.skill_runtime.format_retry",
            {
                "assistant_raw": assistant_raw,
                "retry_feedback": retry_feedback,
            },
        )

    @staticmethod
    def _normalize_usage(usage: Any) -> Dict[str, int]:
        if usage is None:
            return {}
        if isinstance(usage, dict):
            source = usage
        else:
            source = {
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None),
                "total_tokens": getattr(usage, "total_tokens", None),
            }
        normalized: Dict[str, int] = {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = source.get(key)
            if value is None:
                continue
            try:
                normalized[key] = int(value)
            except Exception:
                continue
        return normalized

    @classmethod
    def _build_llm_call_record(
        cls,
        *,
        call_type: str,
        usage: Any,
        parsed_ok: bool,
    ) -> Dict[str, Any]:
        normalized_usage = cls._normalize_usage(usage)
        return {
            "call_type": call_type,
            "parsed_ok": bool(parsed_ok),
            "usage": normalized_usage,
        }

    @classmethod
    def _summarize_llm_usage(cls, llm_calls: List[Dict[str, Any]]) -> Dict[str, Any]:
        summary = {
            "call_count": len(llm_calls),
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        for call in llm_calls:
            usage = cls._normalize_usage((call or {}).get("usage"))
            summary["prompt_tokens"] += int(usage.get("prompt_tokens", 0) or 0)
            summary["completion_tokens"] += int(usage.get("completion_tokens", 0) or 0)
            summary["total_tokens"] += int(usage.get("total_tokens", 0) or 0)
        return summary

    @staticmethod
    def _step_to_trace(step: AgentStep) -> Dict[str, Any]:
        return {
            "index": step.index,
            "assistant_raw": step.assistant_raw,
            "tool_call": {
                "name": step.tool_call.name,
                "arguments": step.tool_call.arguments,
            }
            if step.tool_call
            else None,
            "prompt_context": step.prompt_context,
            "tool_result_meta": {
                "ok": bool((step.tool_result or {}).get("ok")) if isinstance(step.tool_result, dict) else None,
                "error": str((step.tool_result or {}).get("error") or "") if isinstance(step.tool_result, dict) else "",
                "profile_root_size_chars": int(((step.tool_profile or {}).get("root_size_chars") or 0)),
            },
            "final_output": step.final_output,
            "validation_error": step.validation_error,
            "llm_usage": step.llm_usage,
        }

    def _emit_event(self, payload: Dict[str, Any]) -> None:
        if not self.event_handler:
            return
        try:
            self.event_handler(payload)
        except Exception:
            return
