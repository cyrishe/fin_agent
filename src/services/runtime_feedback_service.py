from __future__ import annotations

from typing import Any, Dict, List, Optional


class RuntimeFeedbackService:
    """Build concise, generic feedback envelopes for runtime loop handoff.

    The service does not retry or replan. It only describes current state in a
    stable shape so an orchestration layer can decide what to do next.
    """

    STATUSES = ["completed", "failed", "blocked", "partial", "needs_user_input", "skipped", "pending"]
    REASON_CODES = [
        "tool_failed",
        "data_unavailable",
        "tool_schema_mismatch",
        "coverage_insufficient",
        "goal_misaligned",
        "invalid_input",
        "execution_failed",
        "upstream_state_not_actionable",
    ]
    SUGGESTED_ACTIONS = [
        "continue",
        "retry_step",
        "add_step",
        "replan_stage",
        "restart_task",
        "ask_user",
    ]

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _shorten(value: Any, limit: int = 180) -> str:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return text[: limit - 1].rstrip() + "..."

    def protocol(self) -> Dict[str, Any]:
        return {
            "version": "runtime_feedback.v2",
            "statuses": self.STATUSES,
            "reason_codes": self.REASON_CODES,
            "suggested_actions": self.SUGGESTED_ACTIONS,
            "required_fields": ["status", "message", "scope", "reason_code", "suggested_action", "evidence"],
        }

    def build_step_feedback(self, run_record: Dict[str, Any], *, objective: str = "") -> Dict[str, Any]:
        status = self._trim(run_record.get("status")) or "pending"
        reason = self._trim(run_record.get("reason"))
        tool_name = self._trim(run_record.get("tool_name") or run_record.get("name")) or "step"
        error = self._trim(run_record.get("error"))
        failure_kind = self._trim(run_record.get("failure_kind"))
        result = run_record.get("result") if isinstance(run_record.get("result"), dict) else {}

        if status == "completed":
            row_count = self._result_row_count(result)
            evidence = [f"step_id={self._trim(run_record.get('step_id'))}", f"name={tool_name}", f"status=completed"]
            if row_count is not None:
                evidence.append(f"row_count={row_count}")
            return self._envelope(
                status="completed",
                message=f"{tool_name} 已完成。",
                scope="step",
                reason_code="",
                suggested_action="continue",
                evidence=evidence,
            )

        if status == "skipped":
            action = "replan_stage" if reason in {
                "missing_required_by_schema",
                "missing_upstream_binding",
                "invalid_argument_enum",
                "invalid_argument_range",
            } else "continue"
            reason_code = "tool_schema_mismatch" if action == "replan_stage" else "upstream_state_not_actionable"
            return self._envelope(
                status="blocked",
                message=self._shorten(f"{tool_name} 未执行：{reason or '前置条件未满足'}。"),
                scope="step",
                reason_code=reason_code,
                suggested_action=action,
                evidence=self._step_evidence(run_record),
            )

        reason_code = self._reason_code(reason=reason, error=error, failure_kind=failure_kind)
        suggested_action = self._suggested_action(reason_code=reason_code, reason=reason, error=error)
        message_reason = error or reason or "执行失败"
        return self._envelope(
            status="failed",
            message=self._shorten(f"{tool_name} 失败：{message_reason}。"),
            scope="step",
            reason_code=reason_code,
            suggested_action=suggested_action,
            evidence=self._step_evidence(run_record),
        )

    def build_task_feedback(
        self,
        *,
        execution_plan: Dict[str, Any],
        tool_runs: List[Dict[str, Any]],
        final_output: Dict[str, Any],
        step_feedback: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        objective = self._trim(execution_plan.get("objective"))
        step_items = step_feedback if isinstance(step_feedback, list) else [
            self.build_step_feedback(item, objective=objective)
            for item in tool_runs
            if isinstance(item, dict)
        ]
        actionable = [
            item for item in step_items
            if self._trim(item.get("status")) not in {"completed", "pending"}
            or self._trim(item.get("suggested_action")) not in {"", "continue"}
        ]

        coverage_feedback = self._coverage_feedback(final_output=final_output)
        if coverage_feedback:
            actionable.append(coverage_feedback)

        if not actionable:
            return self._envelope(
                status="completed",
                message="任务执行完成，当前没有需要 loop 的反馈。",
                scope="task",
                reason_code="",
                suggested_action="continue",
                evidence=[f"tool_count={len(tool_runs)}"],
                step_feedback=step_items,
            )

        primary = self._primary_actionable(actionable)
        failed_count = len([item for item in tool_runs if self._trim(item.get("status")) == "failed"])
        completed_count = len([item for item in tool_runs if self._trim(item.get("status")) == "completed"])
        task_status = "failed" if failed_count and completed_count == 0 else "partial"
        if self._trim(primary.get("suggested_action")) == "ask_user":
            task_status = "needs_user_input"
        elif self._trim(primary.get("status")) == "blocked" and not completed_count:
            task_status = "blocked"

        return self._envelope(
            status=task_status,
            message=self._trim(primary.get("message")) or "任务未完全满足，需要继续处理。",
            scope="task",
            reason_code=self._trim(primary.get("reason_code")),
            suggested_action=self._trim(primary.get("suggested_action")) or "replan_stage",
            evidence=primary.get("evidence") if isinstance(primary.get("evidence"), list) else [],
            step_feedback=step_items,
            actionable_feedback=actionable,
        )

    def _coverage_feedback(self, *, final_output: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        risks = final_output.get("risks") if isinstance(final_output.get("risks"), list) else []
        if not risks:
            return None
        first = next((item for item in risks if isinstance(item, dict) and self._trim(item.get("description"))), None)
        if not first:
            return None
        risk_type = self._trim(first.get("type")) or "coverage"
        description = self._trim(first.get("description"))
        if self._looks_like_invalid_input(description):
            return self._envelope(
                status="needs_user_input",
                message=self._shorten(description),
                scope="task",
                reason_code="invalid_input",
                suggested_action="ask_user",
                evidence=[f"risk_type={risk_type}"],
            )
        return self._envelope(
            status="partial",
            message=self._shorten(description),
            scope="task",
            reason_code="coverage_insufficient",
            suggested_action="add_step",
            evidence=[f"risk_type={risk_type}"],
        )

    def _reason_code(self, *, reason: str, error: str, failure_kind: str) -> str:
        if failure_kind in {"missing_upstream_binding", "tool_schema_mismatch"}:
            return "tool_schema_mismatch"
        if failure_kind in {
            "runtime_error",
            "timeout",
            "output_missing",
            "output_json_error",
            "finance_query_round_limit",
        }:
            return "execution_failed"
        if reason in {"missing_required_by_schema", "missing_upstream_binding"}:
            return "tool_schema_mismatch"
        lowered = error.lower()
        if "unsupported" in lowered and any(marker in lowered for marker in ("field", "argument", "parameter", "sort_by", "sort")):
            return "tool_schema_mismatch"
        if self._looks_like_invalid_input(error):
            return "invalid_input"
        if reason in {"tool_failed", "empty_iteration"}:
            return "data_unavailable"
        if reason in {"runtime_error", "result_retention_error"}:
            return "execution_failed"
        return "tool_failed"

    def _suggested_action(self, *, reason_code: str, reason: str, error: str) -> str:
        if reason_code == "tool_schema_mismatch":
            return "replan_stage"
        if reason_code == "invalid_input":
            return "ask_user"
        if reason_code == "data_unavailable":
            return "add_step"
        if reason_code == "execution_failed":
            return "retry_step"
        return "retry_step"

    def _step_evidence(self, run_record: Dict[str, Any]) -> List[str]:
        evidence = [
            f"step_id={self._trim(run_record.get('step_id'))}",
            f"name={self._trim(run_record.get('tool_name') or run_record.get('name'))}",
            f"reason={self._trim(run_record.get('reason') or run_record.get('error'))}",
        ]
        plan = run_record.get("plan") if isinstance(run_record.get("plan"), dict) else {}
        arguments = plan.get("arguments") if isinstance(plan.get("arguments"), dict) else {}
        if arguments:
            evidence.append("arguments=" + self._shorten(arguments, limit=120))
        return [item for item in evidence if item and item != "reason="]

    def _result_row_count(self, result: Dict[str, Any]) -> Optional[int]:
        data = result.get("data") if isinstance(result, dict) else None
        if isinstance(data, list):
            return len(data)
        if isinstance(data, dict):
            coverage = data.get("coverage") if isinstance(data.get("coverage"), dict) else {}
            for key in ("returned_rows", "row_count", "count"):
                value = coverage.get(key)
                if isinstance(value, int):
                    return value
            for key in ("rows", "items", "history", "series"):
                value = data.get(key)
                if isinstance(value, list):
                    return len(value)
        return None

    def _primary_actionable(self, actionable: List[Dict[str, Any]]) -> Dict[str, Any]:
        priority = {
            "ask_user": 0,
            "replan_stage": 1,
            "restart_task": 2,
            "retry_step": 3,
            "add_step": 4,
            "continue": 5,
        }
        return sorted(
            actionable,
            key=lambda item: priority.get(self._trim(item.get("suggested_action")), 9),
        )[0]

    def _looks_like_invalid_input(self, text: str) -> bool:
        value = self._trim(text)
        lowered = value.lower()
        return any(
            marker in value
            for marker in ("无法识别", "不存在", "未匹配", "无效")
        ) or any(
            marker in lowered
            for marker in ("not found", "invalid input", "unknown symbol", "unknown code")
        )

    def _envelope(
        self,
        *,
        status: str,
        message: str,
        scope: str,
        reason_code: str,
        suggested_action: str,
        evidence: List[str],
        step_feedback: Optional[List[Dict[str, Any]]] = None,
        actionable_feedback: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "version": "runtime_feedback.v2",
            "status": self._trim(status) or "pending",
            "message": self._shorten(message, limit=240),
            "scope": self._trim(scope) or "step",
            "reason_code": self._trim(reason_code),
            "suggested_action": self._trim(suggested_action) or "continue",
            "evidence": [self._shorten(item, limit=180) for item in (evidence or []) if self._trim(item)][:6],
        }
        if step_feedback is not None:
            payload["step_feedback"] = step_feedback
        if actionable_feedback is not None:
            payload["actionable_feedback"] = actionable_feedback
        return payload
