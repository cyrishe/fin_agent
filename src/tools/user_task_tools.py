"""Fast user-task operations shared by chat tools and system callers.

Ownership is supplied out of band by the authenticated caller. These tools
submit work; the independent worker owns execution.
"""
from __future__ import annotations

import hashlib
from typing import Any, Mapping
from urllib.parse import urlencode


def _service():
    from src.services.scheduled_task_service import ScheduledTaskService
    return ScheduledTaskService()


def _owner(runtime_ctx: Mapping[str, Any] | None, *, write: bool = False) -> str:
    context = runtime_ctx or {}
    owner = str(context.get("owner_user_id") or "").strip()
    if not owner:
        raise ValueError("任务操作需要服务端提供当前用户身份")
    if write and context.get("user_type") not in {"member", "admin", "service"}:
        raise PermissionError("请登录后提交或管理后台任务")
    return owner


def _link(task_id: str = "", run_id: str = "") -> str:
    # A query-relative link preserves the application's deployment base path.
    return "?" + urlencode({key: value for key, value in
                             {"view": "tasks", "task": task_id, "run": run_id}.items() if value})


def run_submit(args: dict, *, runtime_ctx: dict | None = None) -> dict:
    owner = _owner(runtime_ctx, write=True)
    context = runtime_ctx or {}
    if context.get("task_run_id") or context.get("scheduled_task_run_id"):
        raise ValueError("后台任务不能递归提交另一项后台任务")
    instruction = str(args.get("instruction") or "").strip()
    if not instruction or len(instruction) > 4000:
        raise ValueError("任务说明需要 1 到 4000 个字符")
    key = str(args.get("idempotency_key") or "").strip()
    if len(key) > 128:
        raise ValueError("幂等标识过长")
    if not key and context.get("turn_id"):
        key = hashlib.sha256((str(context.get("conversation_id") or "") + "\0" +
                              str(context["turn_id"]) + "\0" + instruction).encode()).hexdigest()
    created = _service().create(owner_user_id=owner, instruction=instruction,
                                idempotency_key=key,
                                source_conversation=str(context.get("conversation_id") or ""))
    task_id = str(created.get("task_id") or created["schedule_id"])
    run_id = str(created.get("initial_run_id") or "")
    return {"ok": True, "task_id": task_id, "run_id": run_id or None,
            "summary": "任务已提交，可在任务中心查看进度和结果。",
            "requirement_brief": created["requirement_brief"],
            "trigger": created.get("trigger") or {}, "budget": created.get("budget") or {},
            "task_url": _link(task_id, run_id)}


def run_get(args: dict, *, runtime_ctx: dict | None = None) -> dict:
    owner = _owner(runtime_ctx)
    run_id, task_id = (str(args.get(key) or "").strip() for key in ("run_id", "task_id"))
    service = _service()
    if run_id:
        run = service.get_run(owner_user_id=owner, run_id=run_id)
        task_id = str(run.get("task_id") or run.get("schedule_id") or "")
        return {"ok": True, "run": run, "task_url": _link(task_id, run_id)}
    if not task_id:
        raise ValueError("请提供 task_id 或 run_id；查找任务可使用 task_list")
    task = service.get(owner_user_id=owner, schedule_id=task_id)
    runs = service.list_runs(owner_user_id=owner, schedule_id=task_id, limit=10)
    return {"ok": True, "task": task, "runs": runs, "task_url": _link(task_id)}


def run_list(args: dict, *, runtime_ctx: dict | None = None) -> dict:
    owner = _owner(runtime_ctx)
    tasks = _service().list(owner_user_id=owner)
    return {"ok": True, "tasks": [
        {key: task.get(key) for key in ("task_id", "schedule_id", "requirement_brief",
                                       "trigger", "enabled", "next_run_at", "updated_at")}
        for task in tasks[:50]], "task_url": _link()}


def run_cancel(args: dict, *, runtime_ctx: dict | None = None) -> dict:
    owner = _owner(runtime_ctx, write=True)
    run_id = str(args.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("取消运行需要 run_id")
    run = _service().cancel_run(owner_user_id=owner, run_id=run_id)
    return {"ok": True, "run": run, "summary": "已记录停止请求，已完成的证据会保留。",
            "task_url": _link(str(run.get("task_id") or run.get("schedule_id") or ""), run_id)}
