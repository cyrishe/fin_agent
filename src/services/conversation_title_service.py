from __future__ import annotations

import re
from typing import Any, Callable, Dict, Optional

from src.utils.ai_service import DEFAULT_FLASH_MODEL, chat_qwen_flash_json


class ConversationTitleService:
    """Generate a concise, stable title from the first user message."""

    MAX_TITLE_LENGTH = 20

    def __init__(
        self,
        *,
        llm_call: Optional[Callable[..., tuple[Any, Any]]] = None,
    ) -> None:
        self.llm_call = llm_call or chat_qwen_flash_json

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def generate(self, *, user_text: str) -> Dict[str, Any]:
        text = self._trim(user_text)
        fallback = self._fallback_title(text)
        if not text:
            return {"title": fallback, "source": "fallback", "model_name": DEFAULT_FLASH_MODEL}
        messages = [
            {
                "role": "system",
                "content": (
                    "你负责给金融助手的对话命名。根据用户首条消息生成一个简洁中文标题。"
                    "只返回 JSON：{\"title\":\"...\"}。标题不超过20个字符，不加引号、句号、编号或解释；"
                    "忽略 /custom_tool create 等系统命令，只概括用户真实目标。"
                ),
            },
            {"role": "user", "content": text[:1200]},
        ]
        try:
            payload, usage = self.llm_call(messages, enable_think=False)
            title = self._normalize_title(payload.get("title") if isinstance(payload, dict) else "")
            if title:
                return {
                    "title": title,
                    "source": "llm",
                    "model_name": DEFAULT_FLASH_MODEL,
                    "llm_usage": usage,
                }
        except Exception:
            pass
        return {"title": fallback, "source": "fallback", "model_name": DEFAULT_FLASH_MODEL}

    def _normalize_title(self, value: Any) -> str:
        title = re.sub(r"\s+", " ", self._trim(value)).strip(" \t\r\n\"'`《》【】[]，。！？!?：:；;")
        if not title:
            return ""
        return title[: self.MAX_TITLE_LENGTH].rstrip("，。！？!?：:；;")

    def _fallback_title(self, user_text: str) -> str:
        text = re.sub(r"^/custom_tool\s+(?:create|edit)\s+", "", self._trim(user_text), flags=re.IGNORECASE)
        text = re.sub(r"^/[a-z0-9_-]+\s+", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+", " ", text).strip(" \t\r\n\"'`《》【】[]，。！？!?：:；;")
        if not text:
            return "新对话"
        first_clause = re.split(r"[，。！？!?；;\n]", text, maxsplit=1)[0].strip()
        candidate = first_clause if len(first_clause) >= 4 else text
        return candidate[: self.MAX_TITLE_LENGTH].rstrip("，。！？!?：:；;") or "新对话"
