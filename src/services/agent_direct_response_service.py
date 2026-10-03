from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from src.prompting.prompt_registry import get_prompt_registry
from src.utils.ai_service import chat_qwen_flash


class AgentDirectResponseError(RuntimeError):
    pass


class AgentDirectResponseService:
    def __init__(self, *, llm_call: Optional[Callable[..., Any]] = None) -> None:
        self.registry = get_prompt_registry()
        self.llm_call = llm_call or chat_qwen_flash

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def answer(self, *, user_text: str, agent: Dict[str, Any]) -> Dict[str, Any]:
        text = self._trim(user_text)
        if not text:
            raise AgentDirectResponseError("Agent 直接回答失败：用户问题为空")
        profile = agent.get("runtime_profile") if isinstance(agent.get("runtime_profile"), dict) else {}
        messages = self.registry.render_messages(
            "system.assistant.agent_direct_response",
            {
                "agent_name": self._trim(agent.get("agent_name")),
                "display_name": self._trim(agent.get("display_name") or agent.get("agent_name")),
                "agent_system_prompt": self._trim(profile.get("system_prompt_text")),
                "user_text": text,
            },
        )
        try:
            answer, usage = self.llm_call(messages, enable_think=False)
        except Exception as exc:
            raise AgentDirectResponseError(f"Agent 直接回答失败：{exc}") from exc
        normalized_answer = self._trim(answer)
        if not normalized_answer:
            raise AgentDirectResponseError("Agent 直接回答失败：模型返回为空")
        return {
            "message": normalized_answer,
            "messages": messages,
            "llm_usage": usage if isinstance(usage, dict) else {
                "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
                "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
            },
        }
