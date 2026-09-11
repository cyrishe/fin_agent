from __future__ import annotations

from typing import Any, Dict, Optional

from src.prompting.prompt_registry import get_prompt_registry
from src.utils.ai_service import chat_qwen_flash_json


class AnswerSummaryService:
    FALLBACK_SUMMARY_MAX_CHARS = 360

    def __init__(self) -> None:
        self.registry = get_prompt_registry()

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def summarize(
        self,
        *,
        raw_user_text: str,
        assistant_output_text: str,
        output_payload: Optional[Dict[str, Any]] = None,
        enable_llm: bool = False,
    ) -> Dict[str, Any]:
        user_text = self._trim(raw_user_text)
        assistant_text = self._trim(assistant_output_text)
        payload = output_payload if isinstance(output_payload, dict) else {}
        if enable_llm:
            try:
                messages = self.registry.render_messages(
                    "system.assistant.answer_summary",
                    {
                        "raw_user_text": user_text,
                        "assistant_output_text": assistant_text,
                        "output_payload": payload,
                    },
                )
                response, usage = chat_qwen_flash_json(messages, enable_think=False)
                if isinstance(response, dict):
                    summary = self._trim(response.get("answer_summary"))
                    if summary:
                        return {
                            "answer_summary": summary,
                            "source": "llm",
                            "llm_usage": usage if isinstance(usage, dict) else {},
                            "messages": messages,
                        }
            except Exception:
                pass
        return {
            "answer_summary": self._fallback_summary(
                raw_user_text=user_text,
                assistant_output_text=assistant_text,
                output_payload=payload,
            ),
            "source": "fallback",
        }

    def _fallback_summary(
        self,
        *,
        raw_user_text: str,
        assistant_output_text: str,
        output_payload: Dict[str, Any],
    ) -> str:
        task_result = output_payload.get("task_result") if isinstance(output_payload.get("task_result"), dict) else {}
        final_output = task_result.get("final_output") if isinstance(task_result.get("final_output"), dict) else {}
        final_summary = self._trim(final_output.get("summary"))
        if final_summary:
            return (
                f"{self._semantic_preview(final_summary)} "
                "完成度：回答已完成。"
            )
        if assistant_output_text:
            return (
                f"{self._semantic_preview(assistant_output_text)} "
                "完成度：回答已结束。"
            )
        if bool(output_payload.get("data_only")):
            data = (
                output_payload.get("data")
                if isinstance(output_payload.get("data"), dict)
                else {}
            )
            results = data.get("results") if isinstance(data.get("results"), list) else []
            result_summaries: list[str] = []
            for item in results[:4]:
                if not isinstance(item, dict):
                    continue
                label = self._trim(item.get("goal") or item.get("api")) or "数据结果"
                row_count = item.get("row_count")
                count_text = (
                    f"{row_count} 行"
                    if isinstance(row_count, int) and not isinstance(row_count, bool)
                    else ""
                )
                result_summaries.append(
                    "，".join(part for part in (label, count_text) if part)
                )
            if result_summaries:
                return self._semantic_preview(
                    f"仅取数完成：{'；'.join(result_summaries)}。完成度：数据查询已结束。"
                )
        mode = self._trim(output_payload.get("mode"))
        if mode == "tool_plan_completed":
            return "本轮已完成任务规划、工具执行与结果汇总。完成度：执行已结束。"
        planning_state = output_payload.get("planning_task_state") if isinstance(output_payload.get("planning_task_state"), dict) else {}
        if isinstance(planning_state.get("steps"), list) and planning_state.get("steps"):
            return "本轮已完成问题理解与任务整理，并生成了后续执行方案。完成度：规划已结束。"
        if raw_user_text:
            return "本轮已处理用户问题。完成度：处理已结束。"
        return ""

    @classmethod
    def _semantic_preview(cls, text: Any) -> str:
        normalized = " ".join(cls._trim(text).split())
        if len(normalized) <= cls.FALLBACK_SUMMARY_MAX_CHARS:
            return normalized
        return normalized[: cls.FALLBACK_SUMMARY_MAX_CHARS].rstrip() + "…"
