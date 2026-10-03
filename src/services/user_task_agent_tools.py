"""Expose the same task operations to authenticated conversational agents."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from src.tools import user_task_tools


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
