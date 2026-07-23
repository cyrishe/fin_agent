from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.prompting.prompt_registry import get_prompt_registry
from src.utils.ai_service import chat_qwen_flash_json


class ContextResolutionError(RuntimeError):
    pass


class ContextResolutionService:
    def __init__(self) -> None:
        self.registry = get_prompt_registry()

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def resolve(
        self,
        *,
        user_text: str,
        context_window: Optional[List[Dict[str, Any]]] = None,
        thread_state: Optional[Dict[str, Any]] = None,
        preprocessing_signals: Optional[Dict[str, Any]] = None,
        interaction_result: Optional[Dict[str, Any]] = None,
        enable_llm: bool = False,
    ) -> Dict[str, Any]:
        text = self._trim(user_text)
        if not enable_llm:
            context_refs = self._structured_attachment_refs(preprocessing_signals)
            normalized_items = [self._legacy_item_from_ref(item) for item in context_refs]
            return {
                "ori_question": text,
                "resolved_question": text,
                "context_refs": context_refs,
                "analize": "",
                "resolved_items": normalized_items,
                "resolution_summary": self._build_resolution_summary(normalized_items),
                "source": "explicit_command",
                "llm_usage": {},
            }
        signals = preprocessing_signals if isinstance(preprocessing_signals, dict) else {}
        state = thread_state if isinstance(thread_state, dict) else {}
        try:
            messages = self.registry.render_messages(
                "system.assistant.context_resolution",
                {
                    "raw_user_text": text,
                    "context_window": self._build_prompt_context_window(context_window or []),
                    "context_objects": self._build_prompt_context_objects(state, signals),
                },
            )
            payload, usage = chat_qwen_flash_json(messages, enable_think=False)
        except Exception as exc:
            raise ContextResolutionError(f"上下文语义解析失败：{exc}") from exc
        if not isinstance(payload, dict):
            raise ContextResolutionError("上下文语义解析失败：模型没有返回 JSON 对象")
        normalized = self._normalize_payload(payload, original_question=text)
        normalized["source"] = "llm"
        normalized["llm_usage"] = usage if isinstance(usage, dict) else {
            "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
        }
        normalized["messages"] = messages
        return normalized

    def _normalize_payload(self, payload: Dict[str, Any], *, original_question: str = "") -> Dict[str, Any]:
        ori_question = self._trim(payload.get("ori_question"))
        resolved_question = self._trim(payload.get("resolved_question"))
        if not ori_question:
            raise ContextResolutionError("上下文语义协议错误：ori_question 不能为空")
        if not resolved_question:
            raise ContextResolutionError("上下文语义协议错误：resolved_question 不能为空")
        if not isinstance(payload.get("context_refs"), list):
            raise ContextResolutionError("上下文语义协议错误：context_refs 必须是数组")
        context_refs = self._normalize_context_refs(payload.get("context_refs"))
        normalized_items = [self._legacy_item_from_ref(item) for item in context_refs]
        resolution_summary = self._build_resolution_summary(normalized_items)
        return {
            "ori_question": ori_question,
            "resolved_question": resolved_question,
            "context_refs": context_refs,
            # Compatibility fields for the existing tracing and context UI.
            "analize": "",
            "resolved_items": normalized_items,
            "resolution_summary": resolution_summary,
        }

    def _build_prompt_context_window(self, context_window: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        compact: List[Dict[str, Any]] = []
        for item in context_window[:10]:
            if not isinstance(item, dict):
                continue
            attachments: List[Dict[str, str]] = []
            for att in (item.get("attachments") or [])[:3]:
                if not isinstance(att, dict):
                    continue
                attachment_id = self._trim(att.get("attachment_id"))
                if not attachment_id:
                    continue
                attachments.append(
                    {
                        "context_ref": f"attachment:{attachment_id}",
                        "attachment_id": attachment_id,
                        "attachment_summary": self._trim(att.get("attachment_summary")),
                    }
                )
            compact.append(
                {
                    "round": int(item.get("round") or 0),
                    "context_ref": f"turn:{int(item.get('round') or 0)}:{self._trim(item.get('role')) or 'unknown'}",
                    "role": self._trim(item.get("role")) or "unknown",
                    "text": self._trim(item.get("text")),
                    "attachments": attachments,
                }
            )
        return compact

    def _build_prompt_context_objects(
        self,
        thread_state: Dict[str, Any],
        preprocessing_signals: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, str]]:
        rows = thread_state.get("context_objects") if isinstance(thread_state.get("context_objects"), list) else []
        normalized: List[Dict[str, str]] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            context_ref = self._trim(item.get("context_ref"))
            if not context_ref:
                continue
            normalized.append(
                {
                    "context_ref": context_ref,
                    "summary": self._trim(item.get("summary")),
                }
            )
        signals = preprocessing_signals if isinstance(preprocessing_signals, dict) else {}
        for item in signals.get("attachment_signals") or []:
            if not isinstance(item, dict):
                continue
            attachment_ids = item.get("attachment_ids") if isinstance(item.get("attachment_ids"), list) else []
            for attachment_id in attachment_ids:
                normalized_id = self._trim(attachment_id)
                if not normalized_id:
                    continue
                normalized.append(
                    {
                        "context_ref": f"attachment:{normalized_id}",
                        "summary": self._trim(item.get("summary")) or self._trim(item.get("kind")) or "attachment",
                    }
                )
        return normalized[:8]

    def _normalize_context_refs(self, value: Any) -> List[str]:
        rows = value if isinstance(value, list) else []
        normalized: List[str] = []
        for item in rows:
            if isinstance(item, dict):
                context_ref = self._trim(item.get("context_ref") or item.get("ref_id") or item.get("id"))
            else:
                context_ref = self._trim(item)
            if context_ref and context_ref not in normalized:
                normalized.append(context_ref)
        return normalized

    def _structured_attachment_refs(
        self,
        preprocessing_signals: Optional[Dict[str, Any]],
    ) -> List[str]:
        signals = preprocessing_signals if isinstance(preprocessing_signals, dict) else {}
        refs: List[str] = []
        for item in signals.get("attachment_signals") or []:
            if not isinstance(item, dict):
                continue
            attachment_ids = item.get("attachment_ids") if isinstance(item.get("attachment_ids"), list) else []
            for attachment_id in attachment_ids:
                normalized_id = self._trim(attachment_id)
                context_ref = f"attachment:{normalized_id}" if normalized_id else ""
                if context_ref and context_ref not in refs:
                    refs.append(context_ref)
        return refs

    def _legacy_item_from_ref(self, context_ref: str) -> Dict[str, str]:
        source_type = "attachment" if context_ref.startswith("attachment:") else "context_object"
        return {
            "source_type": source_type,
            "summary": context_ref,
            "source_round": "",
            "ori_ref_text": context_ref,
        }

    def _build_resolution_summary(self, resolved_items: List[Dict[str, str]]) -> str:
        summaries = [self._trim(item.get("summary")) for item in resolved_items if isinstance(item, dict) and self._trim(item.get("summary"))]
        if not summaries:
            return ""
        joined = "；".join(summaries[:3])
        return f"当前轮依赖的前文信息包括：{joined}。"
