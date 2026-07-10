from __future__ import annotations

from typing import Any, Dict, Optional

from src.prompting.prompt_registry import get_prompt_registry
from src.utils.ai_service import chat_qwen_flash_json


class AssistantInteractionPreprocessor:
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
    ) -> Dict[str, Any]:
        text = self._trim(user_text)
        if not text:
            return self._empty_result(source="empty")
        recent_rounds_context = self._build_recent_rounds_context(thread_context=thread_context)
        try:
            messages = self.registry.render_messages(
                "system.assistant.interaction_preprocess",
                {
                    "user_text": text,
                    "recent_rounds_context": recent_rounds_context,
                },
            )
            payload, usage = chat_qwen_flash_json(messages, enable_think=False)
            if isinstance(payload, dict):
                normalized = self._normalize_payload(payload)
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
        return self._empty_result(source="fallback")

    def _build_recent_rounds_context(
        self,
        *,
        thread_context: Optional[Dict[str, Any]] = None,
    ) -> list[Dict[str, Any]]:
        ctx = thread_context if isinstance(thread_context, dict) else {}
        context_window = ctx.get("context_window") if isinstance(ctx.get("context_window"), list) else []
        rounds: Dict[int, Dict[str, Any]] = {}
        ordered_rounds: list[int] = []
        for item in context_window[-10:]:
            if not isinstance(item, dict):
                continue
            try:
                round_no = int(item.get("round", 0) or 0)
            except Exception:
                round_no = 0
            if round_no <= 0:
                continue
            bucket = rounds.get(round_no)
            if bucket is None:
                bucket = {"round": round_no, "user_question": "", "assistant_answer_summary": ""}
                rounds[round_no] = bucket
                ordered_rounds.append(round_no)
            role = self._trim(item.get("role"))
            text = self._trim(item.get("text"))
            if role == "user" and text and not bucket["user_question"]:
                bucket["user_question"] = text
            elif role == "assistant" and text and not bucket["assistant_answer_summary"]:
                bucket["assistant_answer_summary"] = text
        return [rounds[round_no] for round_no in ordered_rounds]

    def _empty_result(self, *, source: str) -> Dict[str, Any]:
        return {
            "analize": "",
            "domain_hint": "",
            "agent_hint": "",
            "needs_reference_resolution": False,
            "info_ready": False,
            "source": source,
        }

    def _normalize_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "analize": self._trim(payload.get("analize")),
            "domain_hint": self._trim(payload.get("domain_hint")),
            "agent_hint": self._trim(payload.get("agent_hint")),
            "needs_reference_resolution": self._normalize_bool(payload.get("needs_reference_resolution")),
            "info_ready": self._normalize_bool(payload.get("info_ready")),
        }

    def _normalize_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        normalized = self._trim(value).lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no", ""}:
            return False
        return bool(value)
