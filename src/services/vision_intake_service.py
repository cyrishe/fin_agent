from typing import Any

from src.prompting.prompt_registry import get_prompt_registry
from src.services.attachment_service import AttachmentService
from src.utils.ai_service import (
    DEFAULT_CHAT_MODEL,
    build_multimodal_user_message,
    chat_qwen_multimodal,
    chat_qwen_multimodal_json,
)


class VisionIntakeServiceError(ValueError):
    pass


class VisionIntakeService:
    def __init__(self, *, attachment_service: AttachmentService | None = None) -> None:
        self.attachment_service = attachment_service or AttachmentService()

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def analyze_for_assistant(
        self,
        *,
        attachments: list[dict] | None,
        user_text: str = "",
        application_context: dict | None = None,
        thread_context: dict | None = None,
    ) -> dict[str, Any]:
        image_attachments = [
            item for item in (attachments or [])
            if isinstance(item, dict) and self._trim(item.get("kind")) == "image"
        ]
        if not image_attachments:
            raise VisionIntakeServiceError("缺少可分析的图片附件")

        image_paths = [self.attachment_service.resolve_absolute_path(item) for item in image_attachments]
        prompt_messages = self._build_prompt_messages(
            user_text=user_text,
            application_context=application_context,
            thread_context=thread_context,
            image_paths=image_paths,
        )
        llm_payload = None
        llm_enabled = False
        llm_usage = None
        model_name = DEFAULT_CHAT_MODEL
        try:
            llm_payload, llm_usage = chat_qwen_multimodal_json(
                message_body=prompt_messages,
                enable_think=False,
            )
            llm_enabled = isinstance(llm_payload, dict)
        except Exception:
            llm_payload = None

        if not isinstance(llm_payload, dict):
            text_answer, llm_usage = chat_qwen_multimodal(
                message_body=prompt_messages,
                enable_think=False,
            )
            llm_payload = self._fallback_payload(
                text_answer=text_answer,
                user_text=user_text,
                attachment_count=len(image_attachments),
            )

        normalized = self._normalize_llm_payload(llm_payload)
        attachment_ids = [
            self._trim(item.get("attachment_id"))
            for item in image_attachments
            if isinstance(item, dict) and self._trim(item.get("attachment_id"))
        ]
        return {
            "mode": "vision_analysis",
            "message": "已结合图片完成分析。",
            "items": [
                {
                    "name": "multimodal_intake",
                    "display_name": "图片分析结果",
                    "status": "completed",
                    "llm_enabled": llm_enabled,
                    "attachment_count": len(image_attachments),
                    "image_type": normalized.get("image_type") or "unknown",
                    "suggested_next_action": normalized.get("suggested_next_action") or "",
                }
            ],
            "thread_context_patch": {
                "last_image_attachment_ids": attachment_ids,
                "last_image_type": normalized.get("image_type") or "unknown",
                "last_image_summary": str(((normalized.get("final_output") or {}) if isinstance(normalized.get("final_output"), dict) else {}).get("summary") or "").strip(),
                "last_visual_subjects": (
                    ((normalized.get("final_output") or {}) if isinstance(normalized.get("final_output"), dict) else {}).get("suggested_subjects")
                    if isinstance(((normalized.get("final_output") or {}) if isinstance(normalized.get("final_output"), dict) else {}).get("suggested_subjects"), list)
                    else []
                ),
            },
            "task_result": {
                "final_output": normalized.get("final_output"),
                "render_payload": normalized.get("render_payload"),
            },
            "llm_usage": self._normalize_usage(llm_usage),
            "model_name": model_name,
        }

    def _build_prompt_messages(
        self,
        *,
        user_text: str,
        application_context: dict | None,
        thread_context: dict | None,
        image_paths: list[str],
    ) -> list[dict[str, Any]]:
        registry = get_prompt_registry()
        app_ctx = application_context if isinstance(application_context, dict) else {}
        thread_ctx = thread_context if isinstance(thread_context, dict) else {}
        rendered = registry.render_messages(
            "system.assistant.vision_intake",
            {
                "application_name": self._trim(app_ctx.get("application_name") or "investment_workbench"),
                "agent_name": self._trim(((app_ctx.get("assistant_agent") or {}) if isinstance(app_ctx.get("assistant_agent"), dict) else {}).get("agent_name")),
                "thread_context": thread_ctx,
                "user_text": self._trim(user_text) or "请分析这张图片",
            },
        )
        if not rendered:
            raise VisionIntakeServiceError("vision intake prompt missing")
        if len(rendered) == 1:
            return [
                build_multimodal_user_message(
                    text=str(rendered[0].get("content") or "").strip(),
                    image_paths=image_paths,
                )
            ]
        system_messages = rendered[:-1]
        user_message = rendered[-1]
        return system_messages + [
            build_multimodal_user_message(
                text=str(user_message.get("content") or "").strip(),
                image_paths=image_paths,
            )
        ]

    def _fallback_payload(self, *, text_answer: str, user_text: str, attachment_count: int) -> dict[str, Any]:
        summary = self._trim(text_answer) or "已收到图片，但模型没有返回结构化结果。"
        return {
            "summary": summary,
            "image_type": "unknown",
            "facts": [
                {"category": "observation", "detail": f"已接收 {attachment_count} 张图片。"},
                {"category": "question", "detail": self._trim(user_text) or "用户未提供额外文字问题。"},
            ],
            "risks": [
                {"type": "visual_limit", "description": "当前返回为非结构化回答，建议补充更明确的问题或使用专项图像工具。"},
            ],
            "suggested_next_action": "answer_directly",
            "suggested_subjects": [],
            "render_sections": [
                {
                    "section_id": "overview",
                    "title": "图片概览",
                    "block_type": "structured_text",
                    "items": [{"text": summary}],
                }
            ],
        }

    def _normalize_llm_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        summary = self._trim(payload.get("summary")) or "已完成图片分析。"
        image_type = self._trim(payload.get("image_type")) or "unknown"
        facts = self._normalize_fact_items(payload.get("facts"))
        risks = self._normalize_risk_items(payload.get("risks"))
        next_action = self._trim(payload.get("suggested_next_action")) or "answer_directly"
        render_sections = self._normalize_render_sections(payload.get("render_sections"), summary=summary, facts=facts, risks=risks)
        return {
            "image_type": image_type,
            "suggested_next_action": next_action,
            "final_output": {
                "summary": summary,
                "facts": facts,
                "risks": risks,
                "image_type": image_type,
                "suggested_next_action": next_action,
                "suggested_subjects": payload.get("suggested_subjects") if isinstance(payload.get("suggested_subjects"), list) else [],
            },
            "render_payload": {
                "page_type": "vision_intake",
                "sections": render_sections,
            },
        }

    def _normalize_usage(self, usage: Any) -> dict[str, int]:
        if usage is None:
            return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        source = usage if isinstance(usage, dict) else {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0),
            "completion_tokens": getattr(usage, "completion_tokens", 0),
            "total_tokens": getattr(usage, "total_tokens", 0),
        }
        return {
            "prompt_tokens": int(source.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(source.get("completion_tokens", 0) or 0),
            "total_tokens": int(source.get("total_tokens", 0) or 0),
        }

    def _normalize_fact_items(self, items: Any) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for item in items or []:
            if isinstance(item, dict):
                detail = self._trim(item.get("detail") or item.get("text"))
                if not detail:
                    continue
                rows.append({
                    "category": self._trim(item.get("category") or "observation"),
                    "detail": detail,
                })
            elif self._trim(item):
                rows.append({"category": "observation", "detail": self._trim(item)})
        return rows

    def _normalize_risk_items(self, items: Any) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for item in items or []:
            if isinstance(item, dict):
                description = self._trim(item.get("description") or item.get("detail") or item.get("text"))
                if not description:
                    continue
                rows.append({
                    "type": self._trim(item.get("type") or "ambiguity"),
                    "description": description,
                })
            elif self._trim(item):
                rows.append({"type": "ambiguity", "description": self._trim(item)})
        return rows

    def _normalize_render_sections(
        self,
        raw_sections: Any,
        *,
        summary: str,
        facts: list[dict[str, str]],
        risks: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        sections: list[dict[str, Any]] = []
        for section in raw_sections or []:
            if not isinstance(section, dict):
                continue
            section_id = self._trim(section.get("section_id")) or f"section_{len(sections) + 1}"
            title = self._trim(section.get("title")) or section_id
            block_type = self._trim(section.get("block_type")) or "structured_text"
            items = section.get("items")
            blocks: list[dict[str, Any]] = []
            if block_type == "table" and isinstance(items, list) and items:
                headers: list[str] = []
                for row in items:
                    if isinstance(row, dict):
                        for key in row.keys():
                            if key not in headers:
                                headers.append(str(key))
                if headers:
                    blocks.append({
                        "block_id": f"{section_id}_table",
                        "type": "table",
                        "title": title,
                        "data": {
                            "headers": headers,
                            "rows": items,
                        },
                    })
            elif block_type == "text_list" and isinstance(items, list) and items:
                blocks.append({
                    "block_id": f"{section_id}_list",
                    "type": "text_list",
                    "title": title,
                    "data": {
                        "items": [
                            item if isinstance(item, dict) else {"text": self._trim(item)}
                            for item in items
                            if (isinstance(item, dict) and item) or self._trim(item)
                        ],
                    },
                })
            elif block_type == "metric_strip" and isinstance(items, list) and items:
                blocks.append({
                    "block_id": f"{section_id}_metrics",
                    "type": "metric_strip",
                    "title": title,
                    "data": {"items": items},
                })
            else:
                text_items = []
                for item in items or []:
                    if isinstance(item, dict):
                        text_items.append(item.get("text") or item.get("detail") or item.get("description") or "")
                    else:
                        text_items.append(self._trim(item))
                text = "\n".join([self._trim(x) for x in text_items if self._trim(x)])
                if text:
                    blocks.append({
                        "block_id": f"{section_id}_text",
                        "type": "structured_text",
                        "title": title,
                        "data": {
                            "lead": text,
                            "bullets": [],
                        },
                    })
            if blocks:
                sections.append({
                    "section_id": section_id,
                    "title": title,
                    "blocks": blocks,
                })

        if not sections:
            sections = [
                {
                    "section_id": "overview",
                    "title": "图片概览",
                    "blocks": [
                        {
                            "block_id": "overview_text",
                            "type": "structured_text",
                            "title": "概览",
                            "data": {
                                "lead": summary,
                                "bullets": [],
                            },
                        }
                    ],
                }
            ]
        if facts:
            sections.append({
                "section_id": "facts",
                "title": "关键事实",
                "blocks": [
                    {
                        "block_id": "facts_table",
                        "type": "table",
                        "title": "关键事实",
                        "data": {
                            "headers": ["category", "detail"],
                            "rows": facts,
                        },
                    }
                ],
            })
        if risks:
            sections.append({
                "section_id": "risks",
                "title": "风险与限制",
                "blocks": [
                    {
                        "block_id": "risks_table",
                        "type": "table",
                        "title": "风险与限制",
                        "data": {
                            "headers": ["type", "description"],
                            "rows": risks,
                        },
                    }
                ],
            })
        return sections
