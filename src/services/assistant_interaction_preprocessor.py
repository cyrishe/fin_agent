from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.prompting.prompt_registry import get_prompt_registry
from src.utils.ai_service import chat_qwen_flash_json


class AssistantInteractionPreprocessError(RuntimeError):
    pass


class AssistantInteractionPreprocessor:
    TURN_MODES = {"normal_qa", "system_operation", "tool_development"}

    def __init__(self) -> None:
        self.registry = get_prompt_registry()

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def classify(
        self,
        *,
        user_text: str,
        thread_context: Optional[Dict[str, Any]] = None,
        application_context: Optional[Dict[str, Any]] = None,
        context_resolution: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        resolution = context_resolution if isinstance(context_resolution, dict) else {}
        text = self._trim(resolution.get("resolved_question")) or self._trim(user_text)
        if not text:
            raise AssistantInteractionPreprocessError("顶层意图识别失败：本轮自然语言输入为空")
        available_agents = self._build_available_agents(application_context)
        application_background = self._build_application_background(application_context)
        active_context = self._build_active_context(
            thread_context=thread_context,
            application_context=application_context,
        )
        try:
            messages = self.registry.render_messages(
                "system.assistant.interaction_preprocess",
                {
                    "resolved_turn": {
                        "ori_question": self._trim(resolution.get("ori_question")) or self._trim(user_text),
                        "resolved_question": text,
                        "context_refs": resolution.get("context_refs") if isinstance(resolution.get("context_refs"), list) else [],
                    },
                    "available_agents": available_agents,
                    "application_background": application_background,
                    "active_context": active_context if active_context is not None else "null",
                },
            )
            payload, usage = chat_qwen_flash_json(messages, enable_think=False)
        except Exception as exc:
            raise AssistantInteractionPreprocessError(f"顶层意图识别失败：{exc}") from exc
        if not isinstance(payload, dict):
            raise AssistantInteractionPreprocessError("顶层意图识别失败：模型没有返回 JSON 对象")
        normalized = self._normalize_payload(payload, available_agents=available_agents)
        return {
            **normalized,
            "messages": messages,
            "source": "llm",
            "llm_usage": usage if isinstance(usage, dict) else {
                "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
                "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
            },
        }

    def _normalize_payload(
        self,
        payload: Dict[str, Any],
        *,
        available_agents: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        rows = available_agents or []
        allowed_names = {self._trim(item.get("agent_name")) for item in rows if self._trim(item.get("agent_name"))}
        agent_name = self._trim(payload.get("agent_name"))
        if not agent_name:
            raise AssistantInteractionPreprocessError("顶层意图协议错误：agent_name 不能为空")
        if allowed_names and agent_name not in allowed_names:
            raise AssistantInteractionPreprocessError(f"顶层意图协议错误：未知 agent_name={agent_name}")
        turn_mode = self._trim(payload.get("turn_mode"))
        if turn_mode not in self.TURN_MODES:
            raise AssistantInteractionPreprocessError(f"顶层意图协议错误：未知 turn_mode={turn_mode or '-'}")
        return self._with_compatibility(
            agent_name=agent_name,
            turn_mode=turn_mode,
            source="",
            analize=self._trim(payload.get("analize")),
            needs_reference_resolution=self._normalize_bool(payload.get("needs_reference_resolution")),
            info_ready=(
                self._normalize_bool(payload.get("info_ready"))
                if "info_ready" in payload
                else True
            ),
        )

    def _with_compatibility(
        self,
        *,
        agent_name: str,
        turn_mode: str,
        source: str,
        analize: str = "",
        needs_reference_resolution: bool = False,
        info_ready: bool = True,
    ) -> Dict[str, Any]:
        return {
            "agent_name": self._trim(agent_name),
            "turn_mode": turn_mode,
            # Temporary aliases for downstream services that still consume the v2 shape.
            "analize": self._trim(analize),
            "domain_hint": "system" if turn_mode == "system_operation" else "business",
            "agent_hint": self._trim(agent_name),
            "needs_reference_resolution": bool(needs_reference_resolution),
            "info_ready": bool(info_ready),
            "source": source,
        }

    def _build_available_agents(
        self,
        application_context: Optional[Dict[str, Any]],
    ) -> List[Dict[str, str]]:
        app = application_context if isinstance(application_context, dict) else {}
        rows = app.get("available_agents") if isinstance(app.get("available_agents"), list) else []
        normalized: List[Dict[str, str]] = []
        seen: set[str] = set()
        for item in rows:
            if not isinstance(item, dict):
                continue
            agent_name = self._trim(item.get("agent_name") or item.get("name"))
            if not agent_name or agent_name in seen:
                continue
            seen.add(agent_name)
            config = item.get("config") if isinstance(item.get("config"), dict) else {}
            responsibilities = (
                item.get("responsibilities")
                if isinstance(item.get("responsibilities"), list)
                else config.get("responsibilities")
                if isinstance(config.get("responsibilities"), list)
                else []
            )
            normalized.append(
                {
                    "agent_name": agent_name,
                    "display_name": self._trim(item.get("display_name")) or agent_name,
                    "role": self._trim(item.get("role")),
                    "responsibility": "；".join(
                        self._trim(value)
                        for value in responsibilities[:3]
                        if self._trim(value)
                    ),
                }
            )
        if normalized:
            return normalized
        for key in ("default_agent",):
            item = app.get(key) if isinstance(app.get(key), dict) else {}
            agent_name = self._trim(item.get("agent_name") or item.get("name"))
            if not agent_name or agent_name in seen:
                continue
            seen.add(agent_name)
            normalized.append(
                {
                    "agent_name": agent_name,
                    "display_name": self._trim(item.get("display_name")) or agent_name,
                    "role": self._trim(item.get("role")),
                    "responsibility": "",
                }
            )
        return normalized

    def _build_application_background(
        self,
        application_context: Optional[Dict[str, Any]],
    ) -> Dict[str, str]:
        app = application_context if isinstance(application_context, dict) else {}
        config = app.get("application_config") if isinstance(app.get("application_config"), dict) else {}
        default_agent = app.get("default_agent") if isinstance(app.get("default_agent"), dict) else {}
        return {
            "application_name": self._trim(app.get("application_name") or config.get("name")),
            "display_name": self._trim(app.get("display_name") or config.get("display_name")),
            "domain": self._trim(config.get("domain") or app.get("domain")),
            "preferred_agent": self._trim(default_agent.get("agent_name") or default_agent.get("name")),
        }

    def _build_active_context(
        self,
        *,
        thread_context: Optional[Dict[str, Any]],
        application_context: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        thread = thread_context if isinstance(thread_context, dict) else {}
        state = thread.get("custom_tool_state") if isinstance(thread.get("custom_tool_state"), dict) else {}
        if not state:
            return None
        app = application_context if isinstance(application_context, dict) else {}
        default_agent = app.get("default_agent") if isinstance(app.get("default_agent"), dict) else {}
        tool_name = self._trim(state.get("tool_name"))
        design = (
            state.get("design_contract")
            if isinstance(state.get("design_contract"), dict)
            else state.get("partial_design")
            if isinstance(state.get("partial_design"), dict)
            else {}
        )
        display_name = self._trim(design.get("display_name"))
        context_name = tool_name or display_name or "current"
        has_design = bool(design)
        has_implementation = bool(int(state.get("implementation_revision") or 0))
        has_failed_test = isinstance(state.get("test_feedback"), dict)
        available_assets: List[str] = []
        if has_design:
            available_assets.append("design")
            if self._trim(design.get("mermaid")) or (
                isinstance(design.get("flow"), dict) and design.get("flow")
            ):
                available_assets.append("flow")
        if has_implementation:
            available_assets.extend(["code", "tests"])
        active_context: Dict[str, Any] = {
            "type": "custom_tool",
            "context_ref": f"custom_tool:{context_name}",
            "owner_agent": self._trim(state.get("owner_agent") or default_agent.get("agent_name")),
            "has_design": has_design,
            "has_implementation": has_implementation,
            "has_failed_test": has_failed_test,
            "available_assets": available_assets,
        }
        ui_action = thread.get("_current_ui_action") if isinstance(thread.get("_current_ui_action"), dict) else {}
        if ui_action:
            active_context["ui_action"] = {
                "interaction_id": self._trim(ui_action.get("interaction_id")),
                "action_id": self._trim(ui_action.get("action_id")),
            }
        return active_context

    def _normalize_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        normalized = self._trim(value).lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no", ""}:
            return False
        return bool(value)
