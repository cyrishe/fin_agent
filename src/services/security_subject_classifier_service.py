from __future__ import annotations

from typing import Any, Dict, List

from src.prompting.prompt_registry import get_prompt_registry
from src.services.security_data_request_protocol import SUBJECTS
from src.utils.ai_service import chat_qwen_flash_json


class SecuritySubjectClassifierService:
    """Classify the main security subject before tool retrieval.

    The classifier is intentionally thin: it only narrows the tool candidate
    pool when tools have explicit subject tags. Retrieval and LLM rerank remain
    the default selector.
    """

    VALID_SUBJECTS = set(SUBJECTS) | {"general"}

    def __init__(self) -> None:
        self.registry = get_prompt_registry()

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def classify(self, *, task_desc: str, enable_llm: bool = True) -> Dict[str, Any]:
        objective = self._trim(task_desc)
        if not objective or not enable_llm:
            return self._fallback()
        try:
            messages = self.registry.render_messages(
                "system.agent_runtime.security_subject_classifier",
                {"task_desc": objective},
            )
            payload, usage = chat_qwen_flash_json(messages, enable_think=False)
        except Exception:
            return self._fallback()
        result = self._normalize_payload(payload)
        result["llm_usage"] = usage if isinstance(usage, dict) else {}
        return result

    def _normalize_payload(self, payload: Any) -> Dict[str, Any]:
        data = payload if isinstance(payload, dict) else {}
        subjects = self._normalize_subjects(payload if isinstance(payload, list) else data.get("subjects"))
        if not subjects and isinstance(data, dict):
            subjects = self._normalize_subjects(data.get("subject"))
        if not subjects:
            subjects = ["general"]
        return {
            "subject": subjects[0],
            "subjects": subjects,
            "reason": self._trim(data.get("reason")) if isinstance(data, dict) else "",
            "source": "llm",
        }

    def _normalize_subjects(self, value: Any) -> List[str]:
        if isinstance(value, str):
            values = [value]
        elif isinstance(value, dict):
            values = [value]
        else:
            values = value or []
        result: List[str] = []
        for item in values:
            if isinstance(item, dict):
                candidate = item.get("subject") or item.get("value") or item.get("name")
            else:
                candidate = item
            normalized = self._trim(candidate).lower()
            if normalized in self.VALID_SUBJECTS and normalized not in result:
                result.append(normalized)
        return result

    def _fallback(self) -> Dict[str, Any]:
        return {
            "subject": "general",
            "subjects": ["general"],
            "reason": "subject_not_classified",
            "source": "fallback",
            "llm_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
