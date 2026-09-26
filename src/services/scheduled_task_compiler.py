from __future__ import annotations

import datetime as dt
from typing import Any, Callable, Dict, Mapping

from src.prompting.prompt_registry import get_prompt_registry
from src.services.asset_invocation_service import AssetInvocationError, AssetInvocationService
from src.services.scheduled_task_protocol import (
    ScheduledTaskProtocolError,
    ensure_utc,
    normalize_schedule_draft,
)
from src.services.skill_studio_service import SkillStudioService
from src.services.tool_studio_service import ToolStudioService
from src.tools.registry import is_tool_definition_disabled
from src.utils.ai_service import chat_qwen_flash_json


class ScheduledTaskCompileError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ScheduledTaskCompiler:
    """SOFT natural language understanding followed by a small HARD schedule."""

    def __init__(
        self,
        *,
        llm_chat: Callable[..., Any] | None = None,
        asset_service: AssetInvocationService | None = None,
        tool_studio: ToolStudioService | None = None,
        skill_studio: SkillStudioService | None = None,
    ) -> None:
        self.llm_chat = llm_chat or chat_qwen_flash_json
        self.asset_service = asset_service or AssetInvocationService()
        self.tool_studio = tool_studio or ToolStudioService()
        self.skill_studio = skill_studio or SkillStudioService()
        self.registry = get_prompt_registry()

    def compile(
        self,
        *,
        instruction: str,
        owner_user_id: str,
        draft: Mapping[str, Any] | None = None,
        now: dt.datetime | None = None,
    ) -> Dict[str, Any]:
        normalized_instruction = str(instruction or "").strip()
        if draft is not None:
            raw = dict(draft)
            raw.setdefault("requirement_brief", normalized_instruction)
            source = "structured"
            usage: Any = None
        else:
            if not normalized_instruction:
                raise ScheduledTaskCompileError("missing_instruction", "缺少定时任务说明")
            messages = self.registry.render_messages(
                "system.assistant.scheduled_task_compile",
                {
                    "now_utc": ensure_utc(now).isoformat(),
                    "instruction": normalized_instruction,
                    "available_assets": self._asset_catalog(),
                },
            )
            try:
                raw, usage = self.llm_chat(messages, enable_think=False)
            except Exception as exc:
                raise ScheduledTaskCompileError(
                    "schedule_compile_failed",
                    f"定时任务理解失败：{exc}",
                ) from exc
            if not isinstance(raw, Mapping):
                raise ScheduledTaskCompileError(
                    "schedule_compile_failed",
                    "定时任务理解失败：模型没有返回 JSON 对象",
                )
            error = raw.get("error") if isinstance(raw.get("error"), Mapping) else {}
            if error:
                raise ScheduledTaskCompileError(
                    str(error.get("code") or "schedule_needs_clarification"),
                    str(error.get("message") or "需要补充定时任务的时间或执行目标"),
                )
            source = "natural_language"
        try:
            normalized = normalize_schedule_draft(raw, now=now)
            contracts = self._authorize_steps(
                normalized["execution_plan"],
                owner_user_id=owner_user_id,
            )
            self._validate_required_inputs(
                normalized["execution_plan"],
                contracts=contracts,
            )
        except ScheduledTaskProtocolError as exc:
            raise ScheduledTaskCompileError(exc.code, exc.message) from exc
        except AssetInvocationError as exc:
            raise ScheduledTaskCompileError("asset_not_available", str(exc)) from exc
        return {
            **normalized,
            "preview": self._build_preview(normalized, contracts=contracts),
            "compile_source": source,
            "llm_usage": self._normalize_usage(usage),
        }

    def authorize_plan(
        self,
        execution_plan: Mapping[str, Any],
        *,
        owner_user_id: str,
    ) -> list[Dict[str, Any]]:
        return self._authorize_steps(execution_plan, owner_user_id=owner_user_id)

    def _authorize_steps(
        self,
        execution_plan: Mapping[str, Any],
        *,
        owner_user_id: str,
    ) -> list[Dict[str, Any]]:
        contracts: list[Dict[str, Any]] = []
        skill_rows = {
            str(item.get("skill_name") or ""): item
            for item in self.skill_studio.list_skills()
        }
        for step in execution_plan.get("steps") or []:
            target = dict(step.get("target_ref") or {})
            kind = str(target.get("kind") or step.get("type") or "")
            name = str(target.get("name") or "")
            if kind == "tool" and is_tool_definition_disabled(name):
                raise ScheduledTaskCompileError(
                    "asset_not_available",
                    f"Tool 不可用于新定时任务：{name}",
                )
            if kind == "skill" and not self._is_skill_schedulable(skill_rows.get(name)):
                raise ScheduledTaskCompileError(
                    "asset_not_available",
                    f"Skill 不可用于新定时任务：{name}",
                )
            contracts.append(
                self.asset_service.load_contract(
                    kind=kind,
                    name=name,
                    owner_ids=[str(owner_user_id or "").strip()],
                    allow_inactive=False,
                )
            )
        return contracts

    def _asset_catalog(self) -> list[Dict[str, Any]]:
        assets: list[Dict[str, Any]] = []
        for item in self.tool_studio.list_tools():
            if is_tool_definition_disabled(str(item.get("tool_name") or "")):
                continue
            assets.append(
                {
                    "kind": "tool",
                    "name": item.get("tool_name"),
                    "description": item.get("description"),
                    "input_schema": self._compact_schema(item.get("input_schema")),
                }
            )
        for item in self.skill_studio.list_skills():
            if not self._is_skill_schedulable(item):
                continue
            assets.append(
                {
                    "kind": "skill",
                    "name": item.get("skill_name"),
                    "description": item.get("description"),
                    "input_schema": self._compact_schema(item.get("input_schema")),
                }
            )
        return assets

    @staticmethod
    def _is_skill_schedulable(item: Mapping[str, Any] | None) -> bool:
        if not isinstance(item, Mapping):
            return False
        availability = item.get("availability") if isinstance(item.get("availability"), Mapping) else {}
        lifecycle = str(availability.get("lifecycle") or "active").strip().lower()
        status = str(item.get("status") or "").strip().lower()
        return (
            lifecycle in {"active", "published", ""}
            and status not in {"draft", "disabled", "archived", "retired", "deprecated"}
        )

    @staticmethod
    def _compact_schema(value: Any) -> Dict[str, Any]:
        schema = value if isinstance(value, Mapping) else {}
        properties = schema.get("properties") if isinstance(schema.get("properties"), Mapping) else {}
        return {
            "type": "object",
            "required": [
                str(item)
                for item in (schema.get("required") or [])
                if str(item or "").strip()
            ],
            "properties": {
                str(name): {
                    key: definition.get(key)
                    for key in ("type", "description", "title", "default")
                    if isinstance(definition, Mapping) and definition.get(key) is not None
                }
                for name, definition in properties.items()
            },
        }

    @staticmethod
    def _validate_required_inputs(
        execution_plan: Mapping[str, Any],
        *,
        contracts: list[Mapping[str, Any]],
    ) -> None:
        steps = list(execution_plan.get("steps") or [])
        for index, step in enumerate(steps):
            contract = contracts[index] if index < len(contracts) else {}
            schema = contract.get("input_schema") if isinstance(contract.get("input_schema"), Mapping) else {}
            required = [
                str(item or "").strip()
                for item in (schema.get("required") or [])
                if str(item or "").strip()
            ]
            inputs = step.get("inputs") if isinstance(step.get("inputs"), Mapping) else {}
            missing = [
                name
                for name in required
                if name not in inputs or inputs.get(name) in (None, "", [], {})
            ]
            if missing:
                raise ScheduledTaskCompileError(
                    "missing_step_input",
                    f"步骤 {step.get('step_id')} 缺少必填输入：{missing[0]}",
                )

    @staticmethod
    def _build_preview(
        draft: Mapping[str, Any],
        *,
        contracts: list[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        steps = list((draft.get("execution_plan") or {}).get("steps") or [])
        return {
            "title": "定时任务预览",
            "requirement_brief": draft.get("requirement_brief"),
            "schedule": {
                **dict(draft.get("trigger") or {}),
                "next_run_at": draft.get("next_run_at"),
            },
            "steps": [
                {
                    "step_id": step.get("step_id"),
                    "type": step.get("type"),
                    "target_name": (step.get("target_ref") or {}).get("name"),
                    "display_name": (
                        contracts[index].get("display_name")
                        if index < len(contracts)
                        else (step.get("target_ref") or {}).get("name")
                    ),
                    "depends_on": step.get("depends_on") or [],
                    "inputs": step.get("inputs") or {},
                }
                for index, step in enumerate(steps)
            ],
        }

    @staticmethod
    def _normalize_usage(value: Any) -> Dict[str, int]:
        if isinstance(value, Mapping):
            return {
                "prompt_tokens": int(value.get("prompt_tokens") or 0),
                "completion_tokens": int(value.get("completion_tokens") or 0),
                "total_tokens": int(value.get("total_tokens") or 0),
            }
        return {
            "prompt_tokens": int(getattr(value, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(value, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(value, "total_tokens", 0) or 0),
        }
