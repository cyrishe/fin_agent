from __future__ import annotations

import os
from typing import Any, Dict, Optional

from src.prompting.prompt_registry import get_prompt_registry
from src.utils.ai_service import chat_qwen_flash_json


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
            if isinstance(payload, dict):
                normalized = self._normalize_payload(payload, raw_user_text=text)
                normalized["source"] = "llm"
                normalized["llm_usage"] = usage if isinstance(usage, dict) else {}
                normalized["messages"] = messages
                return normalized
        except Exception:
            pass
        return self._fallback_result(
            raw_user_text=text,
            context_relation=context_relation,
            focus=focus or {},
            target_asset=target_asset or {},
            domain=domain,
            context_resolution=context_resolution or {},
        )

    def _empty_result(self, raw_user_text: str, *, source: str = "empty") -> Dict[str, Any]:
        return {
            "raw_user_text": self._trim(raw_user_text),
            "analize": "",
            "round_task_desc": self._trim(raw_user_text),
            "task_splitd": [],
            "source": source,
        }

    def _fallback_result(
        self,
        *,
        raw_user_text: str,
        context_relation: str,
        focus: Dict[str, Any],
        target_asset: Dict[str, Any],
        domain: str,
        context_resolution: Dict[str, Any],
    ) -> Dict[str, Any]:
        text = self._trim(raw_user_text)
        focus_label = self._trim(focus.get("label"))
        target_label = self._trim(target_asset.get("label"))
        resolution_summary = self._trim(context_resolution.get("resolution_summary"))
        if domain == "system":
            desc = f"用户提出系统侧请求：{text}。"
            if target_label:
                desc = f"用户希望处理系统侧目标 `{target_label}`。当前请求是：{text}。"
        elif context_relation == "corrective":
            if focus_label:
                desc = f"用户质疑或纠正上一轮与 `{focus_label}` 相关的结果，当前要求是：{text}。"
            else:
                desc = f"用户质疑或纠正上一轮结果，当前要求是：{text}。"
        elif context_relation == "referential":
            if focus_label:
                desc = f"用户引用前文对象 `{focus_label}`，当前问题是：{text}。"
            elif resolution_summary:
                desc = f"{resolution_summary}当前问题是：{text}。"
            else:
                desc = f"用户的问题依赖前文上下文，当前输入是：{text}。"
        elif context_relation == "followup":
            if focus_label:
                desc = f"用户希望继续上一轮任务，当前仍围绕 `{focus_label}` 继续推进。"
            elif resolution_summary:
                desc = resolution_summary
            else:
                desc = f"用户希望继续上一轮任务，当前输入是：{text}。"
        else:
            desc = text
        return {
            "raw_user_text": text,
            "analize": "",
            "round_task_desc": desc,
            "task_splitd": [],
            "source": "fallback_rule",
        }

    def _normalize_payload(self, payload: Dict[str, Any], *, raw_user_text: str) -> Dict[str, Any]:
        return {
            "raw_user_text": self._trim(raw_user_text),
            "analize": self._trim(payload.get("analize")),
            "round_task_desc": self._trim(payload.get("round_task_desc")) or self._trim(raw_user_text),
            "task_splitd": [],
        }
