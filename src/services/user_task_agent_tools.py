"""Expose the same task operations to authenticated conversational agents."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
from pathlib import Path

from src.tools import user_task_tools


def _receipt_snapshot(operation, payload):
    """Keep the observed display facts, separate from authoritative task state."""
    if not payload.get("ok") or operation == "task_list":
        return None
    task = payload.get("task") or {}
    run = payload.get("run") or {}
    if not run:
        runs = payload.get("runs") or []
        run = max(runs, key=lambda item: str(item.get("created_at") or ""), default={})
    task_id = task.get("task_id") or task.get("schedule_id") or payload.get("task_id") or run.get("task_id") or run.get("schedule_id")
    if not task_id:
        return None
    observed_task = {key: task[key] for key in ("requirement_brief", "trigger", "enabled", "next_run_at") if key in task}
    observed_task["task_id"] = task_id
    for key in ("requirement_brief", "trigger"):
        if key not in observed_task and key in payload:
            observed_task[key] = payload[key]
    if not observed_task.get("requirement_brief") and run.get("requirement_brief"):
        observed_task["requirement_brief"] = run["requirement_brief"]
    # No result bodies, model files or internal execution metadata in chat cards.
    observed_run = {key: run[key] for key in (
        "run_id", "task_id", "schedule_id", "status", "summary", "progress",
        "error_text", "cancel_requested_at", "scheduled_for", "created_at",
    ) if key in run}
    return {"operation": operation, "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "task": observed_task, "run": observed_run or None,
            "run_id": payload.get("run_id") or run.get("run_id")}


def build_user_task_agent_tools(runtime):
    from claude_agent_sdk import tool

    actor = runtime.tool_context.get("_task_actor") or {}
    if (actor.get("user_type") not in {"member", "admin", "service"}
            or actor.get("user_id") not in runtime.owner_ids):
        return []
    handlers = {"task_submit": user_task_tools.run_submit, "task_get": user_task_tools.run_get,
                "task_list": user_task_tools.run_list, "task_cancel": user_task_tools.run_cancel}
    result = []
    root = Path(__file__).resolve().parents[1] / "tools" / "definitions"
    for name, handler in handlers.items():
        definition = json.loads((root / f"{name}.tool.json").read_text())

        async def invoke(args, *, _name=name, _handler=handler):
            # A long-lived agent can switch turns/owners. Recheck the live context.
            current = runtime.tool_context.get("_task_actor") or {}
            call = {"tool": _name}
            runtime.tracker["calls"].append(call)
            try:
                if (current.get("user_type") not in {"member", "admin", "service"}
                        or current.get("user_id") not in runtime.owner_ids):
                    raise PermissionError("当前会话未授权管理后台任务")
                payload = await asyncio.to_thread(_handler, dict(args or {}), runtime_ctx={
                    "owner_user_id": current["user_id"], "user_type": current["user_type"],
                    "conversation_id": runtime.tool_context.get("_task_conversation_id") or "",
                    "turn_id": runtime.tool_context.get("_task_turn_id") or "",
                })
                call.update({key: payload[key] for key in ("task_id", "run_id", "task_url") if key in payload})
                receipt = _receipt_snapshot(_name, payload)
                if receipt:
                    call["task_receipt"] = receipt
            except (ValueError, LookupError, PermissionError) as exc:
                payload = {"ok": False, "error": str(exc)}
                call["error"] = str(exc)
            except Exception as exc:
                # Driver errors can include connection information.
                payload = {"ok": False, "error": "任务服务暂时不可用，请稍后重试。"}
                call["error"] = type(exc).__name__
            return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, default=str)}]}

        result.append(tool(name, definition["identity"]["description"], definition["schemas"]["input"])(invoke))
    return result
