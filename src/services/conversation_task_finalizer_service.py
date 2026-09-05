from __future__ import annotations

import os
from typing import Any, Dict, Optional

from src.prompting.prompt_registry import get_prompt_registry
from src.utils.ai_service import chat_qwen_flash_json


class ConversationTaskFinalizerError(RuntimeError):
    pass


class ConversationTaskFinalizerService:
    def __init__(self) -> None:
        self.registry = get_prompt_registry()
        self.prompt_key = str(
            os.getenv("CONVERSATION_TASK_FINALIZER_PROMPT_KEY", "system.assistant.conversation_task_finalizer")
        ).strip() or "system.assistant.conversation_task_finalizer"

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def finalize(
        self,
        *,
        raw_user_text: str,
        interaction_result: Optional[Dict[str, Any]] = None,
        context_resolution: Optional[Dict[str, Any]] = None,
        preprocessing_signals: Optional[Dict[str, Any]] = None,
        context_relation: str = "",
        focus: Optional[Dict[str, Any]] = None,
        target_asset: Optional[Dict[str, Any]] = None,
        domain: str = "",
    ) -> Dict[str, Any]:
        text = self._trim(raw_user_text)
        if not text:
            return self._empty_result(text)
        try:
            messages = self.registry.render_messages(
                self.prompt_key,
                {
                    "raw_user_text": text,
                    "interaction_result": interaction_result or {},
                    "context_resolution": context_resolution or {},
                    "preprocessing_signals": preprocessing_signals or {},
                    "context_relation": context_relation,
                    "focus": focus or {},
                    "target_asset": target_asset or {},
                    "domain": domain,
                },
            )
            payload, usage = chat_qwen_flash_json(messages, enable_think=False)
        except Exception as exc:
            raise ConversationTaskFinalizerError(f"本轮任务语义整理失败：{exc}") from exc
        if not isinstance(payload, dict):
            raise ConversationTaskFinalizerError("本轮任务语义整理失败：模型没有返回 JSON 对象")
        normalized = self._normalize_payload(payload, raw_user_text=text)
        normalized["source"] = "llm"
        normalized["llm_usage"] = usage if isinstance(usage, dict) else {
            "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
        }
        normalized["messages"] = messages
        return normalized

    def _empty_result(self, raw_user_text: str, *, source: str = "empty") -> Dict[str, Any]:
        return {
            "raw_user_text": self._trim(raw_user_text),
            "analize": "",
            "round_task_desc": self._trim(raw_user_text),
            "task_splitd": [],
            "source": source,
        }

    def _normalize_payload(self, payload: Dict[str, Any], *, raw_user_text: str) -> Dict[str, Any]:
        return {
            "raw_user_text": self._trim(raw_user_text),
            "analize": self._trim(payload.get("analize")),
            "round_task_desc": self._trim(payload.get("round_task_desc")) or self._trim(raw_user_text),
            "task_splitd": [],
        }
