import datetime
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional

from src.skill_runtime import SkillRunner
from src.skill_runtime.intent_router import IntentRouter
from src.skill_runtime.skill_bundle_compiler import SkillBundleCompiler
from src.services.runtime_execution_service import RuntimeExecutionService
from src.utils.mysql_utils import MySQLUtils


class TaskCapacityError(RuntimeError):
    pass


class AsyncTaskService:
    HEARTBEAT_INTERVAL_SECONDS = 8.0
    MAX_SOURCE_TYPE_LEN = 32
    SOURCE_TYPE_ALIASES = {
        "assistant_business_dialog_skill_run": "assistant_skill_run",
        "assistant_run_skill": "assistant_skill_run",
    }

    def __init__(
        self,
        *,
        mysql_factory=MySQLUtils,
        runner: Optional[SkillRunner] = None,
        bundle_compiler: Optional[SkillBundleCompiler] = None,
        runtime_execution_service: Optional[RuntimeExecutionService] = None,
        executor: Optional[ThreadPoolExecutor] = None,
        max_workers: Optional[int] = None,
        max_active_jobs: Optional[int] = None,
    ) -> None:
        self.mysql_factory = mysql_factory
        self.runner = runner or SkillRunner()
        self.bundle_compiler = bundle_compiler or SkillBundleCompiler()
        self.runtime_execution_service = runtime_execution_service or RuntimeExecutionService()
        self.intent_router = IntentRouter()
        self.max_workers = self._coerce_positive_int(
            max_workers if max_workers is not None else os.getenv("ASYNC_TASK_MAX_WORKERS"),
            default=8,
            upper=50,
        )
        self.max_active_jobs = self._coerce_positive_int(
            max_active_jobs if max_active_jobs is not None else os.getenv("ASYNC_TASK_MAX_ACTIVE_JOBS"),
            default=max(32, self.max_workers * 4),
            upper=500,
        )
        self.executor = executor or ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="skill-task",
        )

    def submit_skill_job(
        self,
        *,
        skill_name: str,
        input_payload: Dict[str, Any],
        max_steps: Optional[int],
        enable_think: bool,
        execution_profile: str = "real",
        source_type: str = "api",
        conversation_id: str = "",
        thread_id: Optional[int] = None,
        turn_id: Optional[int] = None,
        trigger_message_id: str = "",
        route_snapshot: Optional[Dict[str, Any]] = None,
        application_name: str = "",
        agent_name: str = "",
        agent_runtime_profile: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        normalized_skill = str(skill_name or "").strip()
        if not normalized_skill:
            raise ValueError("skill_name 不能为空")
        normalized_source_type = self._normalize_source_type(source_type)
        job_id = f"job_{uuid.uuid4().hex[:24]}"
        runtime_trace: Dict[str, Any] = {}
        runtime_config = {
            "skill_name": normalized_skill,
            "max_steps": int(max_steps) if max_steps is not None else None,
            "enable_think": bool(enable_think),
            "default_execution_profile": str(execution_profile or "real").strip() or "real",
            "application_name": str(application_name or "").strip(),
            "agent_name": str(agent_name or "").strip(),
            "agent_runtime_profile": agent_runtime_profile if isinstance(agent_runtime_profile, dict) else {},
        }
        db = self.mysql_factory()
        try:
            db.check_async_task_tables_ready()
            inflight_count = db.count_task_jobs_by_status(("queued", "running"))
            if inflight_count >= self.max_active_jobs:
                raise TaskCapacityError(
                    f"异步任务容量已满: inflight={inflight_count}, limit={self.max_active_jobs}"
                )
            execution_plan = self.bundle_compiler.build_execution_plan(
                skill_name=normalized_skill,
                input_payload=input_payload,
                tool_mode="",
            )
            normalized_route_snapshot = route_snapshot if isinstance(route_snapshot, dict) and route_snapshot else self.intent_router.build_explicit_skill_route_snapshot(
                skill_name=normalized_skill,
                input_payload=input_payload,
                source="task_submit",
            )
            runtime_limits = execution_plan.get("runtime_limits") if isinstance(execution_plan.get("runtime_limits"), dict) else {}
            runtime_config["skill_version"] = str(execution_plan.get("skill_version") or "v1")
            route_object = normalized_route_snapshot.get("route") if isinstance(normalized_route_snapshot.get("route"), dict) else {}
            runtime_config["route_summary"] = {
                "route_type": str(route_object.get("route_type") or "").strip(),
                "selected_skill": str(route_object.get("selected_skill") or "").strip(),
                "candidate_skills": route_object.get("candidate_skills") or [],
            }
            runtime_config["execution_plan_summary"] = {
                "selected_tools": execution_plan.get("selected_tools") or [],
                "selected_sections": execution_plan.get("selected_sections") or [],
                "required_evidence_types": execution_plan.get("required_evidence_types") or [],
            }
            if max_steps is None and runtime_limits.get("max_steps"):
                runtime_config["max_steps"] = int(runtime_limits.get("max_steps") or 0) or None
            if not enable_think and runtime_limits.get("enable_think"):
                runtime_config["enable_think"] = bool(runtime_limits.get("enable_think"))
            try:
                runtime_trace = self.runtime_execution_service.begin_artifact_run(
                    artifact_type="skill",
                    artifact_name=normalized_skill,
                    input_payload=input_payload,
                    runtime_ctx={
                        "thread_id": int(thread_id) if thread_id else None,
                        "thread_type": "skill_run",
                        "thread_title": f"skill:{normalized_skill}",
                        "owner_type": "system",
                        "owner_id": "task_service",
                        "source_type": normalized_source_type,
                        "context_summary": f"执行 skill {normalized_skill}",
                        "task_type": "skill_run",
                        "goal": f"执行 skill {normalized_skill}",
                        "assigned_agent": "skill_runtime",
                        "turn_id": int(turn_id) if turn_id else None,
                    },
                )
            except Exception:
                runtime_trace = {}
            if runtime_trace.get("thread_id"):
                runtime_config["runtime_thread_id"] = int(runtime_trace["thread_id"])
            if runtime_trace.get("task_id"):
                runtime_config["runtime_task_id"] = int(runtime_trace["task_id"])
            if turn_id:
                runtime_config["runtime_turn_id"] = int(turn_id)
            normalized_conversation_id = str(conversation_id or "").strip() or (
                f"thread:{runtime_trace['thread_id']}" if runtime_trace.get("thread_id") else ""
            )
            job = db.create_task_job(
                {
                    "job_id": job_id,
                    "task_type": normalized_skill,
                    "source_type": normalized_source_type,
                    "status": "queued",
                    "conversation_id": normalized_conversation_id or None,
                    "trigger_message_id": trigger_message_id or None,
                    "input_payload": input_payload,
                    "runtime_config": runtime_config,
                    "current_stage": "queued",
                    "progress": 0.0,
                    "result_status": "pending",
                }
            )
            db.insert_task_step(
                {
                    "job_id": job_id,
                    "seq": 1,
                    "stage": "queued",
                    "step_type": "task",
                    "title": "任务已创建",
                    "status": "completed",
                    "message": "已进入异步队列，等待执行。",
                }
            )
            db.upsert_task_result(job_id, "route_snapshot", normalized_route_snapshot)
            db.upsert_task_result(job_id, "execution_plan", execution_plan)
            if runtime_trace.get("thread_id"):
                db.upsert_task_result(
                    job_id,
                    "runtime_trace",
                    {
                        "thread_id": runtime_trace.get("thread_id"),
                        "task_id": runtime_trace.get("task_id"),
                    },
                )
        finally:
            db.close_db()
        self.executor.submit(
            self._run_skill_job,
            job_id,
            input_payload,
            runtime_config,
        )
        return job

    def submit_stock_deep_dive(
        self,
        *,
        input_payload: Dict[str, Any],
        max_steps: Optional[int],
        enable_think: bool,
        execution_profile: str = "real",
        source_type: str = "api",
        conversation_id: str = "",
        thread_id: Optional[int] = None,
        turn_id: Optional[int] = None,
        trigger_message_id: str = "",
        route_snapshot: Optional[Dict[str, Any]] = None,
        application_name: str = "",
        agent_name: str = "",
        agent_runtime_profile: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self.submit_skill_job(
            skill_name="stock_deep_dive",
            input_payload=input_payload,
            max_steps=max_steps,
            enable_think=enable_think,
            execution_profile=execution_profile,
            source_type=source_type,
            conversation_id=conversation_id,
            thread_id=thread_id,
            turn_id=turn_id,
            trigger_message_id=trigger_message_id,
            route_snapshot=route_snapshot,
            application_name=application_name,
            agent_name=agent_name,
            agent_runtime_profile=agent_runtime_profile,
        )

    def get_job(self, job_id: str) -> Dict[str, Any] | None:
        db = self.mysql_factory()
        try:
            db.check_async_task_tables_ready()
            return db.get_task_job(job_id)
        finally:
            db.close_db()

    def get_steps(self, job_id: str) -> list[Dict[str, Any]]:
        db = self.mysql_factory()
        try:
            db.check_async_task_tables_ready()
            return db.list_task_steps(job_id)
        finally:
            db.close_db()

    def get_result_map(self, job_id: str) -> Dict[str, Any]:
        db = self.mysql_factory()
        try:
            db.check_async_task_tables_ready()
            return db.get_task_result_map(job_id)
        finally:
            db.close_db()

    def list_recent_jobs(self, limit: int = 20) -> list[Dict[str, Any]]:
        db = self.mysql_factory()
        try:
            db.check_async_task_tables_ready()
            normalized_limit = max(1, min(int(limit or 20), 100))
            with db.conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT job_id, task_type, source_type, status, current_stage, progress,
                           result_status, conversation_id, created_at, updated_at
                    FROM task_job
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (normalized_limit,),
                )
                rows = cur.fetchall() or []
            items: list[Dict[str, Any]] = []
            for row in rows:
                items.append(
                    {
                        "job_id": row[0],
                        "task_type": row[1],
                        "source_type": row[2],
                        "status": row[3],
                        "current_stage": row[4],
                        "progress": float(row[5] or 0),
                        "result_status": row[6],
                        "conversation_id": row[7],
                        "created_at": row[8],
                        "updated_at": row[9],
                    }
                )
            return items
        finally:
            db.close_db()

    def _run_skill_job(
        self,
        job_id: str,
        input_payload: Dict[str, Any],
        runtime_config: Dict[str, Any],
    ) -> None:
        db = self.mysql_factory()
        heartbeat_state = self._build_initial_runtime_state(job_id=job_id)
        heartbeat_stop = threading.Event()
        heartbeat_thread = self._start_heartbeat_thread(
            job_id=job_id,
            runtime_config=runtime_config,
            stop_event=heartbeat_stop,
            state=heartbeat_state,
        )
        try:
            db.check_async_task_tables_ready()
            db.update_task_job(
                job_id,
                status="running",
                current_stage="collecting_data",
                progress=5.0,
                started_at=datetime.datetime.now(),
                error_message="",
            )
            self._append_runtime_trace_event(
                runtime_config=runtime_config,
                event_type="skill_run_started",
                payload={
                    "job_id": job_id,
                    "skill_name": str(runtime_config.get("skill_name") or "").strip(),
                    "input_summary": {
                        "code": input_payload.get("code"),
                        "name": input_payload.get("name"),
                        "question": input_payload.get("question"),
                    },
                },
            )
            db.insert_task_step(
                {
                    "job_id": job_id,
                    "seq": 2,
                    "stage": "collecting_data",
                    "step_type": "system",
                    "title": "开始执行任务",
                    "status": "running",
                    "input_summary": {
                        "code": input_payload.get("code"),
                        "name": input_payload.get("name"),
                        "question": input_payload.get("question"),
                    },
                    "message": "任务已开始，正在执行 skill_runtime。",
                }
            )
            result = self.runner.run(
                skill_name=str(runtime_config.get("skill_name") or "").strip(),
                input_payload=input_payload,
                max_steps=runtime_config.get("max_steps"),
                enable_think=bool(runtime_config.get("enable_think")),
                default_execution_profile=str(runtime_config.get("default_execution_profile") or "real").strip() or "real",
                runtime_context=self._build_tool_runtime_context(runtime_config),
                event_handler=lambda event: self._record_runtime_event(
                    db=db,
                    job_id=job_id,
                    runtime_config=runtime_config,
                    event=event,
                    state=heartbeat_state,
                ),
            )
            payload = result.to_dict()
            result_steps = payload.get("steps") or []
            token_usage = self._summarize_task_token_usage(payload)
            seq = self._next_runtime_seq(db, job_id)

            db.upsert_task_result(
                job_id,
                "skill_run_result",
                payload,
                meta={
                    "ok": bool(payload.get("ok")),
                    "token_usage": token_usage,
                },
            )
            final_output = payload.get("final_output") or {}
            if final_output:
                db.upsert_task_result(job_id, "final_output", final_output)
            render_payload = payload.get("render_payload") or final_output.get("render_payload") or {}
            if render_payload:
                db.upsert_task_result(job_id, "render_payload", render_payload)
            db.upsert_task_result(
                job_id,
                "task_summary",
                {
                    "ok": bool(payload.get("ok")),
                    "error": str(payload.get("error") or ""),
                    "step_count": len(result_steps),
                    "token_usage": token_usage,
                    "result_types": [name for name in ("final_output", "render_payload") if name in self.get_result_map(job_id)],
                },
            )

            if payload.get("ok"):
                db.insert_task_step(
                    {
                        "job_id": job_id,
                        "seq": 2,
                        "stage": "collecting_data",
                        "step_type": "system",
                        "title": "开始执行任务",
                        "status": "completed",
                        "input_summary": {
                            "code": input_payload.get("code"),
                            "name": input_payload.get("name"),
                            "question": input_payload.get("question"),
                        },
                        "message": "任务执行完成。",
                    }
                )
                db.update_task_job(
                    job_id,
                    status="succeeded",
                    current_stage="completed",
                    progress=100.0,
                    result_status="ok",
                    finished_at=datetime.datetime.now(),
                    error_message="",
                )
                db.insert_task_step(
                    {
                        "job_id": job_id,
                        "seq": seq,
                        "stage": "completed",
                        "step_type": "summary",
                        "title": "任务完成",
                        "status": "completed",
                        "output_summary": {
                            "step_count": len(result_steps),
                            "token_usage": token_usage,
                            "result_types": [name for name in ("final_output", "render_payload") if name in self.get_result_map(job_id)],
                        },
                        "message": "任务执行成功。",
                    }
                )
                self._append_runtime_trace_event(
                    runtime_config=runtime_config,
                    event_type="skill_run_completed",
                    payload={
                        "job_id": job_id,
                        "skill_name": str(runtime_config.get("skill_name") or "").strip(),
                        "ok": True,
                        "step_count": len(result_steps),
                        "token_usage": token_usage,
                    },
                )
                self.runtime_execution_service.finish_task(
                    thread_id=runtime_config.get("runtime_thread_id"),
                    task_id=runtime_config.get("runtime_task_id"),
                    status="completed",
                    result_summary="skill run completed",
                )
            else:
                db.insert_task_step(
                    {
                        "job_id": job_id,
                        "seq": 2,
                        "stage": "collecting_data",
                        "step_type": "system",
                        "title": "开始执行任务",
                        "status": "failed",
                        "input_summary": {
                            "code": input_payload.get("code"),
                            "name": input_payload.get("name"),
                            "question": input_payload.get("question"),
                        },
                        "message": str(payload.get("error") or "任务执行失败。"),
                    }
                )
                db.update_task_job(
                    job_id,
                    status="failed",
                    current_stage="failed",
                    progress=100.0,
                    result_status="failed",
                    finished_at=datetime.datetime.now(),
                    error_message=str(payload.get("error") or ""),
                )
                db.insert_task_step(
                    {
                        "job_id": job_id,
                        "seq": seq,
                        "stage": "failed",
                        "step_type": "summary",
                        "title": "任务失败",
                        "status": "failed",
                        "output_summary": {
                            "step_count": len(result_steps),
                            "token_usage": token_usage,
                        },
                        "message": str(payload.get("error") or ""),
                    }
                )
                self._append_runtime_trace_event(
                    runtime_config=runtime_config,
                    event_type="skill_run_failed",
                    payload={
                        "job_id": job_id,
                        "skill_name": str(runtime_config.get("skill_name") or "").strip(),
                        "ok": False,
                        "error": str(payload.get("error") or ""),
                    },
                )
                self.runtime_execution_service.finish_task(
                    thread_id=runtime_config.get("runtime_thread_id"),
                    task_id=runtime_config.get("runtime_task_id"),
                    status="failed",
                    result_summary="skill run failed",
                    error_text=str(payload.get("error") or ""),
                )
        except Exception as exc:
            db.insert_task_step(
                {
                    "job_id": job_id,
                    "seq": 2,
                    "stage": "collecting_data",
                    "step_type": "system",
                    "title": "开始执行任务",
                    "status": "failed",
                    "input_summary": {
                        "code": input_payload.get("code"),
                        "name": input_payload.get("name"),
                        "question": input_payload.get("question"),
                    },
                    "message": str(exc),
                }
            )
            db.update_task_job(
                job_id,
                status="failed",
                current_stage="failed",
                progress=100.0,
                result_status="failed",
                finished_at=datetime.datetime.now(),
                error_message=str(exc),
            )
            db.insert_task_step(
                {
                    "job_id": job_id,
                    "seq": 999999,
                    "stage": "failed",
                    "step_type": "error",
                    "title": "任务异常退出",
                    "status": "failed",
                    "message": str(exc),
                }
            )
            self._append_runtime_trace_event(
                runtime_config=runtime_config,
                event_type="skill_run_failed",
                payload={
                    "job_id": job_id,
                    "skill_name": str(runtime_config.get("skill_name") or "").strip(),
                    "ok": False,
                    "error": str(exc),
                },
            )
            self.runtime_execution_service.finish_task(
                thread_id=runtime_config.get("runtime_thread_id"),
                task_id=runtime_config.get("runtime_task_id"),
                status="failed",
                result_summary="skill run exception",
                error_text=str(exc),
            )
        finally:
            heartbeat_stop.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join(timeout=0.5)
            db.close_db()

    @staticmethod
    def _build_tool_runtime_context(runtime_config: Dict[str, Any]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if runtime_config.get("runtime_thread_id"):
            payload["thread_id"] = int(runtime_config["runtime_thread_id"])
        if runtime_config.get("runtime_task_id"):
            payload["task_id"] = int(runtime_config["runtime_task_id"])
        if runtime_config.get("runtime_turn_id"):
            payload["turn_id"] = int(runtime_config["runtime_turn_id"])
        if runtime_config.get("application_name"):
            payload["application_name"] = str(runtime_config["application_name"]).strip()
        if runtime_config.get("agent_name"):
            payload["agent_name"] = str(runtime_config["agent_name"]).strip()
        if isinstance(runtime_config.get("agent_runtime_profile"), dict) and runtime_config.get("agent_runtime_profile"):
            payload["agent_runtime_profile"] = dict(runtime_config["agent_runtime_profile"])
        return payload

    def _append_runtime_trace_event(
        self,
        *,
        runtime_config: Dict[str, Any],
        event_type: str,
        payload: Dict[str, Any],
    ) -> None:
        thread_id = runtime_config.get("runtime_thread_id")
        task_id = runtime_config.get("runtime_task_id")
        if not thread_id:
            return
        try:
            self.runtime_execution_service.append_event(
                thread_id=int(thread_id),
                task_id=int(task_id) if task_id else None,
                turn_id=int(runtime_config.get("runtime_turn_id")) if runtime_config.get("runtime_turn_id") else None,
                event_type=event_type,
                actor_type="skill",
                actor_id=str(runtime_config.get("skill_name") or "skill_runtime"),
                payload=payload,
            )
        except Exception:
            return

    @staticmethod
    def _infer_stage(raw_step: Dict[str, Any]) -> str:
        if raw_step.get("final_output"):
            return "rendering"
        if raw_step.get("tool_call"):
            return "collecting_data"
        return "synthesizing"

    @staticmethod
    def _infer_title(raw_step: Dict[str, Any]) -> str:
        tool_call = raw_step.get("tool_call") or {}
        if tool_call.get("name"):
            return f"调用 {tool_call['name']}"
        if raw_step.get("final_output"):
            return "生成最终结果"
        return "执行分析步骤"

    @staticmethod
    def _build_step_output_summary(raw_step: Dict[str, Any]) -> Dict[str, Any]:
        summary: Dict[str, Any] = {}
        tool_call = raw_step.get("tool_call")
        if isinstance(tool_call, dict):
            summary["tool_call_record"] = {
                "tool_name": str(tool_call.get("name") or "").strip(),
                "execution_profile": str(tool_call.get("execution_profile") or "real").strip() or "real",
                "call_id": str(tool_call.get("call_id") or "").strip(),
                "argument_keys": list((tool_call.get("arguments") or {}).keys())[:12] if isinstance(tool_call.get("arguments"), dict) else [],
            }
        tool_result = raw_step.get("tool_result")
        if isinstance(tool_result, dict):
            summary["tool_result_meta"] = {
                "ok": bool(tool_result.get("ok")),
                "error": tool_result.get("error") or "",
                "data_keys": list((tool_result.get("data") or {}).keys())[:12] if isinstance(tool_result.get("data"), dict) else [],
            }
        prompt_context = raw_step.get("prompt_context")
        if isinstance(prompt_context, dict):
            summary["prompt_context_keys"] = list(prompt_context.keys())
        final_output = raw_step.get("final_output")
        if isinstance(final_output, dict):
            summary["final_output_keys"] = list(final_output.keys())
        return summary

    @staticmethod
    def _summarize_task_token_usage(payload: Dict[str, Any]) -> Dict[str, Any]:
        summary = {
            "step_count": 0,
            "llm_call_count": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        for step in payload.get("steps") or []:
            if not isinstance(step, dict):
                continue
            summary["step_count"] += 1
            llm_usage = step.get("llm_usage") or {}
            if isinstance(llm_usage, dict):
                summary["llm_call_count"] += int(llm_usage.get("call_count", 0) or 0)
                summary["prompt_tokens"] += int(llm_usage.get("prompt_tokens", 0) or 0)
                summary["completion_tokens"] += int(llm_usage.get("completion_tokens", 0) or 0)
                summary["total_tokens"] += int(llm_usage.get("total_tokens", 0) or 0)
        return summary

    @staticmethod
    def _coerce_positive_int(value: Any, *, default: int, upper: int) -> int:
        try:
            resolved = int(value)
        except (TypeError, ValueError):
            resolved = default
        return max(1, min(int(upper), resolved))

    def _normalize_source_type(self, source_type: Any) -> str:
        raw = str(source_type or "").strip().lower() or "api"
        aliased = self.SOURCE_TYPE_ALIASES.get(raw, raw)
        collapsed = re.sub(r"[^a-z0-9_]+", "_", aliased)
        collapsed = re.sub(r"_+", "_", collapsed).strip("_") or "api"
        if len(collapsed) <= self.MAX_SOURCE_TYPE_LEN:
            return collapsed
        return collapsed[: self.MAX_SOURCE_TYPE_LEN].rstrip("_") or "api"

    def _record_runtime_event(
        self,
        *,
        db: MySQLUtils,
        job_id: str,
        runtime_config: Dict[str, Any],
        event: Dict[str, Any],
        state: Dict[str, Any] | None = None,
    ) -> None:
        if state is not None:
            self._update_runtime_state(state, event)
        seq = self._next_runtime_seq(db, job_id)
        step_type, title, message, output_summary = self._map_runtime_event(event)
        stage = str(event.get("stage") or "collecting_data")
        status = "running" if str(event.get("event_type") or "").strip() in {"step_started", "tool_call_started"} else "completed"
        if str(event.get("event_type") or "").strip() == "validation_retry":
            status = "warning"
        db.insert_task_step(
            {
                "job_id": job_id,
                "seq": seq,
                "stage": stage,
                "step_type": step_type,
                "title": title,
                "status": status,
                "tool_name": event.get("tool_name"),
                "input_summary": {
                    **((event.get("arguments") or {}) if isinstance(event.get("arguments"), dict) else {}),
                    **({
                        "_execution_profile": str(event.get("execution_profile") or "").strip(),
                        "_call_id": str(event.get("call_id") or "").strip(),
                    } if str(event.get("tool_name") or "").strip() else {}),
                },
                "output_summary": output_summary,
                "message": message,
            }
        )
        if str(event.get("event_type") or "").strip() == "tool_call_finished":
            self._upsert_intermediate_render_payload(
                db=db,
                job_id=job_id,
                runtime_config=runtime_config,
                event=event,
            )
        db.update_task_job(
            job_id,
            current_stage=stage,
            progress=self._estimate_progress(runtime_config, event),
        )
        self._append_runtime_trace_event(
            runtime_config=runtime_config,
            event_type=str(event.get("event_type") or "skill_runtime_event"),
            payload={
                "job_id": job_id,
                "stage": stage,
                "event": event,
            },
        )

    def _start_heartbeat_thread(
        self,
        *,
        job_id: str,
        runtime_config: Dict[str, Any],
        stop_event: threading.Event,
        state: Dict[str, Any],
    ) -> threading.Thread:
        thread = threading.Thread(
            target=self._heartbeat_loop,
            kwargs={
                "job_id": job_id,
                "runtime_config": runtime_config,
                "stop_event": stop_event,
                "state": state,
            },
            daemon=True,
            name=f"task-heartbeat-{job_id[-6:]}",
        )
        thread.start()
        return thread

    def _heartbeat_loop(
        self,
        *,
        job_id: str,
        runtime_config: Dict[str, Any],
        stop_event: threading.Event,
        state: Dict[str, Any],
    ) -> None:
        while not stop_event.wait(self.HEARTBEAT_INTERVAL_SECONDS):
            try:
                now = time.time()
                with state["lock"]:
                    last_event_at = float(state.get("last_event_at") or 0.0)
                    if now - last_event_at < self.HEARTBEAT_INTERVAL_SECONDS:
                        continue
                    stage = str(state.get("stage") or "collecting_data")
                    title = str(state.get("title") or "任务仍在执行")
                    message = str(state.get("message") or "任务仍在执行，请稍候。")
                    tool_name = str(state.get("tool_name") or "").strip()
                    step_index = int(state.get("step_index") or 0)
                    state["last_heartbeat_at"] = now
                hb_db = self.mysql_factory()
                try:
                    hb_db.check_async_task_tables_ready()
                    seq = self._next_runtime_seq(hb_db, job_id)
                    hb_db.insert_task_step(
                        {
                            "job_id": job_id,
                            "seq": seq,
                            "stage": stage,
                            "step_type": "heartbeat",
                            "title": f"{title} · 仍在执行",
                            "status": "running",
                            "tool_name": tool_name or None,
                            "output_summary": {
                                "step_index": step_index,
                                "heartbeat": True,
                            },
                            "message": message,
                        }
                    )
                    hb_db.update_task_job(
                        job_id,
                        current_stage=stage,
                        progress=self._estimate_progress(
                            runtime_config,
                            {
                                "event_type": "heartbeat",
                                "step_index": step_index,
                            },
                        ),
                    )
                finally:
                    hb_db.close_db()
            except Exception:
                continue

    @staticmethod
    def _build_initial_runtime_state(*, job_id: str) -> Dict[str, Any]:
        return {
            "job_id": job_id,
            "stage": "queued",
            "title": "任务已创建",
            "message": "已进入异步队列，等待执行。",
            "tool_name": "",
            "step_index": 0,
            "last_event_at": time.time(),
            "last_heartbeat_at": 0.0,
            "lock": threading.Lock(),
        }

    def _update_runtime_state(self, state: Dict[str, Any], event: Dict[str, Any]) -> None:
        step_type, title, message, _ = self._map_runtime_event(event)
        del step_type
        with state["lock"]:
            state["stage"] = str(event.get("stage") or state.get("stage") or "collecting_data")
            state["title"] = title
            state["message"] = message
            state["tool_name"] = str(event.get("tool_name") or state.get("tool_name") or "")
            state["step_index"] = int(event.get("step_index") or state.get("step_index") or 0)
            state["last_event_at"] = time.time()

    def _upsert_intermediate_render_payload(
        self,
        *,
        db: MySQLUtils,
        job_id: str,
        runtime_config: Dict[str, Any],
        event: Dict[str, Any],
    ) -> None:
        skill_name = str(runtime_config.get("skill_name") or "").strip()
        if skill_name != "stock_deep_dive":
            return
        tool_name = str(event.get("tool_name") or "").strip()
        tool_result = event.get("tool_result") or {}
        if not isinstance(tool_result, dict):
            return
        section = self._build_intermediate_section_for_stock_deep_dive(tool_name, tool_result)
        if not section:
            return
        result_map = db.get_task_result_map(job_id)
        payload = result_map.get("intermediate_render_payload")
        if not isinstance(payload, dict):
            payload = {
                "version": "1.0",
                "page_id": f"{job_id}_intermediate",
                "page_type": "stock_deep_dive_intermediate",
                "title": "执行中结果预览",
                "subtitle": "以下模块会随着工具执行逐步补齐。",
                "sections": [],
            }
        sections = [item for item in (payload.get("sections") or []) if isinstance(item, dict)]
        replaced = False
        for idx, existing in enumerate(sections):
            if str(existing.get("section_id") or "") == str(section.get("section_id") or ""):
                sections[idx] = section
                replaced = True
                break
        if not replaced:
            sections.append(section)
        payload["sections"] = sections
        db.upsert_task_result(job_id, "intermediate_render_payload", payload)

    def _build_intermediate_section_for_stock_deep_dive(
        self,
        tool_name: str,
        tool_result: Dict[str, Any],
    ) -> Dict[str, Any] | None:
        data = (tool_result or {}).get("data") or {}
        if tool_name == "stock_quote":
            quote = data.get("realtime_quote") or {}
            daily_kline = (data.get("daily_kline") or {}).get("kline") or []
            return {
                "section_id": "market_overview",
                "section_kind": "analysis",
                "title": "行情预览",
                "description": "基于实时行情和 K 线的执行中预览。",
                "layout": {},
                "blocks": [
                    {
                        "block_id": "market_metrics_preview",
                        "type": "key_metrics",
                        "title": "行情核心指标",
                        "span": {"desktop": 12},
                        "height": "compact",
                        "data": {
                            "price": quote.get("close"),
                            "change_pct": quote.get("pct_chg"),
                            "turnover": quote.get("amount"),
                            "volume": quote.get("volume"),
                        },
                    },
                    {
                        "block_id": "market_kline_preview",
                        "type": "kline",
                        "title": "K线预览",
                        "span": {"desktop": 12},
                        "height": "tall",
                        "data": {
                            "kline": daily_kline[-60:] if isinstance(daily_kline, list) else [],
                            "period": "1d",
                            "indicators": ["MA5", "MA10", "MA20"],
                        },
                    },
                ],
            }
        if tool_name == "stock_funds":
            snapshot = data.get("snapshot") or {}
            today_funds = snapshot.get("today_funds") or []
            historical_table = snapshot.get("historical_table") or []
            rows = historical_table[:12] if isinstance(historical_table, list) else []
            headers = []
            normalized_rows = []
            if rows and isinstance(rows[0], dict):
                headers = list(rows[0].keys())[:6]
                normalized_rows = [[str(item.get(col, "")) for col in headers] for item in rows]
            return {
                "section_id": "capital_flow",
                "section_kind": "data",
                "title": "资金面预览",
                "description": "主力资金与历史资金表的执行中预览。",
                "layout": {},
                "blocks": [
                    {
                        "block_id": "funds_cards_preview",
                        "type": "insight_cards",
                        "title": "资金摘要",
                        "span": {"desktop": 12},
                        "height": "compact",
                        "data": {
                            "cards": [
                                {
                                    "title": str(item.get("name") or item.get("label") or "指标"),
                                    "value": str(item.get("value") or item.get("main_value") or "-"),
                                    "desc": str(item.get("desc") or item.get("sub_value") or ""),
                                    "status": str(item.get("trend") or ""),
                                }
                                for item in (today_funds[:4] if isinstance(today_funds, list) else [])
                                if isinstance(item, dict)
                            ]
                        },
                    },
                    {
                        "block_id": "funds_table_preview",
                        "type": "table",
                        "title": "资金历史表",
                        "span": {"desktop": 12},
                        "height": "tall",
                        "data": {
                            "headers": headers,
                            "rows": normalized_rows,
                        },
                    },
                ],
            }
        if tool_name in {"stock_reports", "equity_research_search"}:
            items = data if isinstance(data, list) else (data.get("items") or [])
            return {
                "section_id": "research_prediction",
                "section_kind": "analysis",
                "title": "研报预览",
                "description": "执行中已收集的研报与机构观点。",
                "layout": {},
                "blocks": [
                    {
                        "block_id": "reports_preview",
                        "type": "text_list",
                        "title": "已收集研报",
                        "span": {"desktop": 12},
                        "height": "compact",
                        "data": {
                            "items": [
                                {
                                    "title": str(item.get("title") or item.get("report_title") or "研报"),
                                    "desc": str(item.get("summary") or item.get("opinion") or item.get("broker") or ""),
                                    "time": str(item.get("time") or item.get("publish_time") or ""),
                                }
                                for item in (items[:6] if isinstance(items, list) else [])
                                if isinstance(item, dict)
                            ]
                        },
                    }
                ],
            }
        if tool_name in {"company_news", "financial_news_search"}:
            items = data if isinstance(data, list) else (data.get("items") or [])
            return {
                "section_id": "news_catalyst",
                "section_kind": "timeline",
                "title": "新闻预览",
                "description": "执行中已收集的新闻与催化。",
                "layout": {},
                "blocks": [
                    {
                        "block_id": "news_preview",
                        "type": "text_list",
                        "title": "已收集新闻",
                        "span": {"desktop": 12},
                        "height": "compact",
                        "data": {
                            "items": [
                                {
                                    "title": str(item.get("title") or "新闻"),
                                    "desc": str(item.get("summary") or item.get("content") or item.get("source") or ""),
                                    "time": str(item.get("time") or item.get("publish_time") or ""),
                                }
                                for item in (items[:8] if isinstance(items, list) else [])
                                if isinstance(item, dict)
                            ]
                        },
                    }
                ],
            }
        return None

    @staticmethod
    def _next_runtime_seq(db: MySQLUtils, job_id: str) -> int:
        steps = db.list_task_steps(job_id)
        if not steps:
            return 10
        max_seq = max(int(step.get("seq") or 0) for step in steps)
        return max_seq + 10

    def _map_runtime_event(self, event: Dict[str, Any]) -> tuple[str, str, str, Dict[str, Any]]:
        event_type = str(event.get("event_type") or "").strip()
        step_index = int(event.get("step_index") or 0)
        tool_name = str(event.get("tool_name") or "").strip()
        if event_type == "step_started":
            return (
                "analysis",
                f"第 {step_index} 步开始",
                str(event.get("message") or f"开始第 {step_index} 步分析。"),
                {},
            )
        if event_type == "tool_call_started":
            return (
                "tool_call",
                self._tool_title(tool_name),
                self._tool_start_message(tool_name),
                {
                    "tool_call_record": self._build_tool_call_record(
                        event=event,
                        status="started",
                    ),
                },
            )
        if event_type == "tool_call_finished":
            return (
                "tool_result",
                f"{self._tool_title(tool_name)}完成",
                self._tool_finish_message(tool_name, event.get("tool_result") or {}),
                {
                    **self._tool_finish_summary(event.get("tool_result") or {}, event.get("prompt_context") or {}),
                    "tool_call_record": self._build_tool_call_record(
                        event=event,
                        status="finished",
                    ),
                },
            )
        if event_type == "final_ready":
            final_output = event.get("final_output") or {}
            render_payload = final_output.get("render_payload") or {}
            return (
                "final",
                "最终结果已生成",
                "结构化结果已通过校验，正在准备页面渲染。",
                {
                    "final_output_keys": list(final_output.keys()) if isinstance(final_output, dict) else [],
                    "section_ids": [str(item.get("section_id") or "") for item in (render_payload.get("sections") or []) if isinstance(item, dict)],
                },
            )
        if event_type == "constraint_warning":
            return (
                "warning",
                "约束提醒",
                str(event.get("message") or "当前结果未完全满足推荐约束。"),
                {},
            )
        return (
            "log",
            "格式修正",
            str(event.get("message") or "模型输出正在修正格式。"),
            {},
        )

    @staticmethod
    def _build_tool_call_record(event: Dict[str, Any], *, status: str) -> Dict[str, Any]:
        arguments = event.get("arguments") if isinstance(event.get("arguments"), dict) else {}
        return {
            "call_id": str(event.get("call_id") or "").strip(),
            "tool_name": str(event.get("tool_name") or "").strip(),
            "execution_profile": str(event.get("execution_profile") or "real").strip() or "real",
            "status": status,
            "argument_keys": list(arguments.keys())[:12],
        }

    @staticmethod
    def _tool_title(tool_name: str) -> str:
        mapping = {
            "stock_quote": "查询行情与K线",
            "stock_realtime_quote": "查询实时行情",
            "stock_history_kline": "查询历史日K",
            "stock_intraday_kline": "查询日内分钟K",
            "stock_funds": "查询资金面",
            "stock_reports": "查询研报",
            "equity_research_search": "查询研报",
            "financial_news_search": "查询新闻",
            "company_news": "查询新闻",
        }
        return mapping.get(str(tool_name or "").strip(), f"调用 {tool_name}")

    @staticmethod
    def _tool_start_message(tool_name: str) -> str:
        mapping = {
            "stock_quote": "正在拉取实时行情、日线与分时K线。",
            "stock_realtime_quote": "正在拉取实时行情快照。",
            "stock_history_kline": "正在拉取历史日K线。",
            "stock_intraday_kline": "正在拉取日内分钟K线。",
            "stock_funds": "正在拉取主力资金、行业资金和资金图表。",
            "stock_reports": "正在抓取研报与机构观点。",
            "equity_research_search": "正在抓取研报与机构观点。",
            "financial_news_search": "正在抓取相关新闻与催化事件。",
            "company_news": "正在抓取公司相关新闻与催化事件。",
        }
        return mapping.get(str(tool_name or "").strip(), "正在调用工具。")

    def _tool_finish_message(self, tool_name: str, tool_result: Dict[str, Any]) -> str:
        data = (tool_result or {}).get("data") or {}
        if tool_name == "stock_quote":
            daily = ((data.get("daily_kline") or {}).get("kline") or [])
            intraday = ((data.get("intraday_kline") or {}).get("kline") or [])
            return f"已获取 {len(daily)} 根日K 和 {len(intraday)} 个分时点。"
        if tool_name == "stock_realtime_quote":
            quote = data.get("quote") or {}
            current = quote.get("current")
            return f"已获取实时行情快照，当前价格 {current}。" if current not in (None, "") else "已获取实时行情快照。"
        if tool_name == "stock_history_kline":
            daily = ((data.get("daily_kline") or {}).get("candles") or [])
            return f"已获取 {len(daily)} 根历史日K。"
        if tool_name == "stock_intraday_kline":
            intraday = ((data.get("intraday_kline") or {}).get("candles") or [])
            return f"已获取 {len(intraday)} 个分钟K点。"
        if tool_name == "stock_funds":
            snapshot = data.get("snapshot") or {}
            hist = snapshot.get("historical_table") or []
            news_total = len(data.get("capital_flow_news_items") or [])
            return f"已获取资金表 {len(hist)} 行，资金相关新闻 {news_total} 条。"
        if tool_name in {"stock_reports", "equity_research_search"}:
            total = len(data if isinstance(data, list) else (data.get("items") or []))
            return f"已收集研报 {total} 篇。"
        if tool_name in {"company_news", "financial_news_search"}:
            total = len(data if isinstance(data, list) else (data.get("items") or []))
            return f"已收集新闻 {total} 条。"
        return "工具已返回结果。"

    @staticmethod
    def _tool_finish_summary(tool_result: Dict[str, Any], prompt_context: Dict[str, Any]) -> Dict[str, Any]:
        data = (tool_result or {}).get("data") or {}
        summary: Dict[str, Any] = {
            "ok": bool((tool_result or {}).get("ok")),
            "data_keys": list(data.keys())[:12] if isinstance(data, dict) else [],
            "prompt_context_keys": list((prompt_context or {}).keys())[:12] if isinstance(prompt_context, dict) else [],
        }
        if isinstance(data, list):
            summary["items_count"] = len(data)
        if isinstance(data, dict):
            if isinstance(data.get("items"), list):
                summary["items_count"] = len(data.get("items") or [])
            snapshot = data.get("snapshot")
            if isinstance(snapshot, dict):
                hist = snapshot.get("historical_table")
                if isinstance(hist, list):
                    summary["historical_rows"] = len(hist)
        return summary

    @staticmethod
    def _estimate_progress(runtime_config: Dict[str, Any], event: Dict[str, Any]) -> float:
        max_steps = max(1, int(runtime_config.get("max_steps") or 6))
        step_index = max(1, int(event.get("step_index") or 1))
        event_type = str(event.get("event_type") or "").strip()
        if event_type == "run_completed":
            return 98.0
        if event_type == "final_ready":
            return 92.0
        if event_type == "heartbeat":
            return min(90.0, 10.0 + ((step_index - 0.25) / max_steps) * 62.0)
        if event_type == "tool_call_finished":
            return min(88.0, 12.0 + (step_index / max_steps) * 64.0)
        if event_type == "tool_call_started":
            return min(82.0, 8.0 + ((step_index - 0.5) / max_steps) * 60.0)
        if event_type == "step_started":
            return min(78.0, 6.0 + ((step_index - 1) / max_steps) * 56.0)
        return 10.0
