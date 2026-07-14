from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.prompting.prompt_registry import get_prompt_registry
from src.utils.ai_service import chat_qwen_flash_json


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
        signals = preprocessing_signals if isinstance(preprocessing_signals, dict) else {}
        state = thread_state if isinstance(thread_state, dict) else {}
        if enable_llm:
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
                if isinstance(payload, dict):
                    normalized = self._normalize_payload(payload, original_question=text)
                    normalized["source"] = "llm"
                    normalized["llm_usage"] = usage if isinstance(usage, dict) else {}
                    normalized["messages"] = messages
                    return normalized
            except Exception:
                pass
        fallback = self._fallback_resolution(
            text=text,
            context_window=context_window or [],
            thread_state=state,
            preprocessing_signals=signals,
        )
        fallback["source"] = "fallback_rule"
        return fallback

    def _normalize_payload(self, payload: Dict[str, Any], *, original_question: str = "") -> Dict[str, Any]:
        ori_question = self._trim(payload.get("ori_question") or payload.get("original_question")) or self._trim(original_question)
        resolved_question = self._trim(payload.get("resolved_question")) or ori_question
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

    def _fallback_resolution(
        self,
        *,
        text: str,
        context_window: List[Dict[str, Any]],
        thread_state: Dict[str, Any],
        preprocessing_signals: Dict[str, Any],
    ) -> Dict[str, Any]:
        resolved_items: List[Dict[str, str]] = []
        for item in preprocessing_signals.get("attachment_signals") or []:
            if not isinstance(item, dict):
                continue
            attachment_ids = item.get("attachment_ids") if isinstance(item.get("attachment_ids"), list) else []
            for attachment_id in attachment_ids:
                normalized_id = self._trim(attachment_id)
                if not normalized_id:
                    continue
                resolved_items.append(
                    {
                        "source_type": "attachment",
                        "summary": self._trim(item.get("summary")) or normalized_id,
                        "source_round": "0",
                        "ori_ref_text": normalized_id,
                    }
                )
        resolved_references = preprocessing_signals.get("resolved_references") if isinstance(preprocessing_signals.get("resolved_references"), list) else []
        for item in resolved_references:
            if not isinstance(item, dict):
                continue
            resolved_items.append(
                {
                    "source_type": "history_answer",
                    "summary": self._trim(item.get("label")),
                    "source_round": "1",
                    "ori_ref_text": self._trim(item.get("raw")),
                }
            )
        if not resolved_items:
            recent_subject = self._trim(thread_state.get("recent_result_subject"))
            if recent_subject and self._contains_any(text, ["继续", "接着", "好的", "行", "可以", "核实", "再看看"]):
                resolved_items.append(
                    {
                        "source_type": "history_answer",
                        "summary": recent_subject,
                        "source_round": "1",
                        "ori_ref_text": recent_subject,
                    }
                )
        if not resolved_items:
            latest_assistant = self._find_latest_context_entry(context_window=context_window, role="assistant")
            if latest_assistant and self._contains_any(text, ["继续", "接着", "好的", "行", "可以"]):
                resolved_items.append(
                    {
                        "source_type": "history_answer",
                        "summary": self._trim(latest_assistant.get("text")),
                        "source_round": self._trim(latest_assistant.get("round")),
                        "ori_ref_text": self._trim(latest_assistant.get("text")),
                    }
                )
        if not resolved_items and self._contains_any(text, ["图", "图片", "附件"]):
            attachment_items = self._find_recent_attachment_items(context_window)
            for item in attachment_items[:2]:
                resolved_items.append(item)
        active_workflow = thread_state.get("active_workflow") if isinstance(thread_state.get("active_workflow"), dict) else {}
        if not resolved_items and active_workflow and self._contains_any(
            text,
            ["继续", "接着", "刚才", "这个工具", "那个工具", "流程图", "核心代码"],
        ):
            context_ref = self._trim(active_workflow.get("context_ref"))
            summary = self._trim(active_workflow.get("summary"))
            if context_ref:
                resolved_items.append(
                    {
                        "source_type": "context_object",
                        "summary": summary or "最近的进行中任务",
                        "source_round": "0",
                        "ori_ref_text": context_ref,
                    }
                )
        summary = self._build_resolution_summary(resolved_items)
        context_refs = self._refs_from_legacy_items(resolved_items)
        resolved_question = text
        if active_workflow and context_refs and self._contains_any(text, ["继续", "接着"]):
            workflow_summary = self._trim(active_workflow.get("summary")) or "最近的任务"
            resolved_question = f"继续处理{workflow_summary}。"
        return {
            "ori_question": text,
            "resolved_question": resolved_question,
            "context_refs": context_refs,
            "analize": summary,
            "resolved_items": resolved_items,
            "resolution_summary": summary,
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

    def _legacy_item_from_ref(self, context_ref: str) -> Dict[str, str]:
        source_type = "attachment" if context_ref.startswith("attachment:") else "context_object"
        return {
            "source_type": source_type,
            "summary": context_ref,
            "source_round": "",
            "ori_ref_text": context_ref,
        }

    def _refs_from_legacy_items(self, items: List[Dict[str, str]]) -> List[str]:
        refs: List[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            source_type = self._trim(item.get("source_type"))
            ori_ref_text = self._trim(item.get("ori_ref_text"))
            source_round = self._trim(item.get("source_round"))
            if source_type == "attachment" and ori_ref_text:
                ref = ori_ref_text if ori_ref_text.startswith("attachment:") else f"attachment:{ori_ref_text}"
            elif source_type == "context_object" and ori_ref_text:
                ref = ori_ref_text
            elif source_round:
                ref = f"turn:{source_round}"
            else:
                continue
            if ref not in refs:
                refs.append(ref)
        return refs

    def _build_resolution_summary(self, resolved_items: List[Dict[str, str]]) -> str:
        summaries = [self._trim(item.get("summary")) for item in resolved_items if isinstance(item, dict) and self._trim(item.get("summary"))]
        if not summaries:
            return ""
        joined = "；".join(summaries[:3])
        return f"当前轮依赖的前文信息包括：{joined}。"

    def _find_latest_context_entry(self, *, context_window: List[Dict[str, Any]], role: str) -> Dict[str, Any]:
        for item in reversed(context_window):
            if not isinstance(item, dict):
                continue
            if self._trim(item.get("role")) != self._trim(role):
                continue
            if self._trim(item.get("text")):
                return item
        return {}

    def _find_recent_attachment_items(self, context_window: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        rows: List[Dict[str, str]] = []
        for item in reversed(context_window):
            if not isinstance(item, dict):
                continue
            round_no = self._trim(item.get("round"))
            for att in (item.get("attachments") or [])[:3]:
                if not isinstance(att, dict):
                    continue
                summary = self._trim(att.get("attachment_summary"))
                attachment_id = self._trim(att.get("attachment_id"))
                rows.append(
                    {
                        "source_type": "attachment",
                        "summary": summary or attachment_id or "attachment",
                        "source_round": round_no,
                        "ori_ref_text": summary or attachment_id,
                    }
                )
            if rows:
                break
        return rows

    def _contains_any(self, text: str, keywords: List[str]) -> bool:
        normalized = self._trim(text)
        return any(keyword in normalized for keyword in keywords if keyword)
