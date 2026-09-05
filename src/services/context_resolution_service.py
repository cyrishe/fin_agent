from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from src.prompting.prompt_registry import get_prompt_registry
from src.utils.ai_service import chat_qwen_flash_json_with_raw


class ContextResolutionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        user_message: str = "",
        technical_detail: str = "",
        raw_response: str = "",
    ) -> None:
        super().__init__(message)
        self.user_message = user_message or "我暂时没能整理好这轮对话的上下文，当前内容没有改变。请重新提交一次。"
        self.technical_detail = technical_detail
        self.raw_response = raw_response


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
        prompt_context_window = self._build_prompt_context_window(context_window or [])
        prompt_context_objects = self._build_prompt_context_objects(state, signals)
        if not self._has_resolution_context(
            prompt_context_window=prompt_context_window,
            prompt_context_objects=prompt_context_objects,
            thread_state=state,
            preprocessing_signals=signals,
            interaction_result=interaction_result,
        ):
            # With no prior semantic asset there is nothing for a context model
            # to resolve.  Preserve the user's text and let the top-level LLM
            # make the Agent/turn-mode decision once.
            return {
                "ori_question": text,
                "resolved_question": text,
                "context_refs": [],
                "analize": "",
                "resolved_items": [],
                "resolution_summary": "",
                "source": "no_context",
                "llm_usage": {},
            }
        try:
            base_messages = self.registry.render_messages(
                "system.assistant.context_resolution",
                {
                    "raw_user_text_json": json.dumps(text, ensure_ascii=False),
                    "context_window": prompt_context_window,
                    "context_objects": prompt_context_objects,
                },
            )
        except Exception as exc:
            raise ContextResolutionError(f"上下文语义解析失败：{exc}") from exc

        messages = list(base_messages)
        usage: Any = {}
        last_detail = ""
        last_raw = ""
        normalized: Dict[str, Any] | None = None
        for attempt in range(2):
            try:
                payload, usage, raw_response = chat_qwen_flash_json_with_raw(
                    messages,
                    enable_think=False,
                    temperature=0.0,
                )
            except Exception as exc:
                last_detail = str(exc)
                if attempt == 0:
                    messages = self._repair_messages(base_messages, "", last_detail)
                    continue
                raise ContextResolutionError(
                    f"上下文语义解析失败：{exc}",
                    technical_detail=last_detail,
                ) from exc
            last_raw = self._trim(raw_response)
            try:
                if not isinstance(payload, dict):
                    raise ContextResolutionError("模型没有返回 JSON 对象")
                normalized = self._normalize_payload(payload, original_question=text)
                break
            except ContextResolutionError as exc:
                last_detail = str(exc)
                if attempt == 0:
                    messages = self._repair_messages(base_messages, last_raw, last_detail)

        if normalized is None:
            raise ContextResolutionError(
                "上下文语义解析失败：模型连续两次没有返回可用协议",
                user_message="我在整理这轮上下文时连续两次没有得到可用结果，因此还没有进入下一步。当前需求和选择都已保留，请重新提交一次。",
                technical_detail=last_detail,
                raw_response=last_raw,
            )
        normalized["source"] = "llm"
        normalized["llm_usage"] = usage if isinstance(usage, dict) else {
            "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
        }
        normalized["messages"] = base_messages
        return normalized

    def _has_resolution_context(
        self,
        *,
        prompt_context_window: List[Dict[str, Any]],
        prompt_context_objects: List[Dict[str, str]],
        thread_state: Dict[str, Any],
        preprocessing_signals: Dict[str, Any],
        interaction_result: Optional[Dict[str, Any]],
    ) -> bool:
        if prompt_context_window or prompt_context_objects or interaction_result:
            return True
        for key in (
            "reference_memory",
            "recent_attachments",
            "active_workflow",
        ):
            if thread_state.get(key):
                return True
        for key in ("recent_result_subject", "thread_summary"):
            if self._trim(thread_state.get(key)):
                return True
        if bool(preprocessing_signals.get("needs_reference_resolution")):
            return True
        if preprocessing_signals.get("resolved_references"):
            return True
        if self._trim(preprocessing_signals.get("recent_result_subject")):
            return True
        return False

    @staticmethod
    def _repair_messages(
        base_messages: List[Dict[str, Any]],
        raw_response: str,
        error: str,
    ) -> List[Dict[str, Any]]:
        messages = [dict(item) for item in base_messages]
        if raw_response:
            messages.append({"role": "assistant", "content": raw_response})
        messages.append(
            {
                "role": "user",
                "content": (
                    f"上一条响应无法按输出协议解析：{error}。"
                    "请根据原始输入重新输出一个严格 JSON 对象，只包含 resolved_question 和 context_refs。"
                ),
            }
        )
        return messages

    def _normalize_payload(self, payload: Dict[str, Any], *, original_question: str = "") -> Dict[str, Any]:
        resolved_question = self._trim(payload.get("resolved_question"))
        if not resolved_question:
            raise ContextResolutionError("上下文语义协议错误：resolved_question 不能为空")
        if not isinstance(payload.get("context_refs"), list):
            raise ContextResolutionError("上下文语义协议错误：context_refs 必须是数组")
        context_refs = self._normalize_context_refs(payload.get("context_refs"))
        normalized_items = [self._legacy_item_from_ref(item) for item in context_refs]
        resolution_summary = self._build_resolution_summary(normalized_items)
        return {
            "ori_question": self._trim(original_question),
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
