from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.prompting.prompt_registry import get_prompt_registry
from src.utils.ai_service import chat_qwen_flash_json


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
            return self._empty_result(source="empty", application_context=application_context)
        available_agents = self._build_available_agents(application_context)
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
                },
            )
            payload, usage = chat_qwen_flash_json(messages, enable_think=False)
            if isinstance(payload, dict):
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
        except Exception:
            pass
        return self._fallback_result(
            text=text,
            thread_context=thread_context,
            application_context=application_context,
            source="fallback",
        )

    def _empty_result(
        self,
        *,
        source: str,
        application_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        available_agents = self._build_available_agents(application_context)
        agent_name = self._fallback_agent_name(available_agents, application_context)
        return self._with_compatibility(
            agent_name=agent_name,
            turn_mode="normal_qa",
            source=source,
        )

    def _normalize_payload(
        self,
        payload: Dict[str, Any],
        *,
        available_agents: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        rows = available_agents or []
        allowed_names = {self._trim(item.get("agent_name")) for item in rows if self._trim(item.get("agent_name"))}
        agent_name = self._trim(payload.get("agent_name") or payload.get("agent_hint"))
        if allowed_names and agent_name not in allowed_names:
            agent_name = self._trim(rows[0].get("agent_name"))
        turn_mode = self._trim(payload.get("turn_mode"))
        if turn_mode not in self.TURN_MODES:
            turn_mode = "system_operation" if self._trim(payload.get("domain_hint")) == "system" else "normal_qa"
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
            "turn_mode": turn_mode if turn_mode in self.TURN_MODES else "normal_qa",
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
        for key in ("execution_agent", "assistant_agent"):
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

    def _fallback_result(
        self,
        *,
        text: str,
        thread_context: Optional[Dict[str, Any]],
        application_context: Optional[Dict[str, Any]],
        source: str,
    ) -> Dict[str, Any]:
        available_agents = self._build_available_agents(application_context)
        active_state = (thread_context or {}).get("custom_tool_state") if isinstance((thread_context or {}).get("custom_tool_state"), dict) else {}
        tool_followup = bool(active_state) and any(
            keyword in text
            for keyword in ("工具", "设计", "实现", "代码", "流程图", "测试", "确认", "启用", "重试", "继续")
        )
        turn_mode = "tool_development" if tool_followup else "normal_qa"
        agent_name = self._fallback_agent_name(available_agents, application_context, text=text, turn_mode=turn_mode)
        return self._with_compatibility(
            agent_name=agent_name,
            turn_mode=turn_mode,
            source=source,
        )

    def _fallback_agent_name(
        self,
        available_agents: List[Dict[str, str]],
        application_context: Optional[Dict[str, Any]],
        *,
        text: str = "",
        turn_mode: str = "normal_qa",
    ) -> str:
        names = [self._trim(item.get("agent_name")) for item in available_agents if self._trim(item.get("agent_name"))]
        lowered = text.lower()
        if any(keyword in lowered for keyword in ("股票", "股价", "行情", "资金流", "研报", "金融", "证券", "基金")) and "investment_analyst" in names:
            return "investment_analyst"
        if any(keyword in lowered for keyword in ("学生", "老师", "题目", "解题", "函数")) and "education_tutor" in names:
            return "education_tutor"
        if turn_mode == "tool_development" and "investment_analyst" in names:
            return "investment_analyst"
        app = application_context if isinstance(application_context, dict) else {}
        assistant = app.get("assistant_agent") if isinstance(app.get("assistant_agent"), dict) else {}
        assistant_name = self._trim(assistant.get("agent_name"))
        if assistant_name in names:
            return assistant_name
        execution = app.get("execution_agent") if isinstance(app.get("execution_agent"), dict) else {}
        execution_name = self._trim(execution.get("agent_name"))
        if execution_name in names:
            return execution_name
        return names[0] if names else "default_assistant"

    def _normalize_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        normalized = self._trim(value).lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no", ""}:
            return False
        return bool(value)
