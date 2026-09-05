from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Callable, Dict, Mapping

from src.services.scheduled_task_compiler import ScheduledTaskCompiler
from src.skill_runtime import SkillRunner
from src.tools.registry import run_tool


class ScheduledTaskExecutionError(RuntimeError):
    def __init__(self, message: str, *, partial_result: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.partial_result = deepcopy(dict(partial_result or {}))


class ScheduledTaskExecutor:
    """Execute an already confirmed plan; no scheduling semantics are re-inferred."""

    def __init__(
        self,
        *,
        authorizer: Callable[[Mapping[str, Any], str], Any] | None = None,
        tool_runner: Callable[..., Dict[str, Any]] | None = None,
        skill_runner: SkillRunner | None = None,
    ) -> None:
        compiler = None if authorizer is not None else ScheduledTaskCompiler()
        self.authorizer = authorizer or (
            lambda plan, owner: compiler.authorize_plan(plan, owner_user_id=owner)
        )
        self.tool_runner = tool_runner or run_tool
        self.skill_runner = skill_runner or SkillRunner()

    def execute(
        self,
        run: Mapping[str, Any],
        *,
        before_step: Callable[[str], None] | None = None,
    ) -> Dict[str, Any]:
        owner_user_id = str(run.get("owner_user_id") or "").strip()
        plan = run.get("execution_plan") if isinstance(run.get("execution_plan"), Mapping) else {}
        steps = [dict(item) for item in plan.get("steps") or [] if isinstance(item, Mapping)]
        if not owner_user_id:
            raise ScheduledTaskExecutionError("运行记录缺少 owner_user_id")
        if not steps:
            raise ScheduledTaskExecutionError("运行记录没有可执行步骤")

        # Reauthorize the complete snapshot before the first side effect.
        self.authorizer(plan, owner_user_id)
        outputs: Dict[str, Dict[str, Any]] = {}
        pending = {str(step.get("step_id") or ""): step for step in steps}
        result: Dict[str, Any] = {
            "schema_version": "scheduled_task_run_result.v1",
            "steps": [],
            "outputs": outputs,
        }
        try:
            while pending:
                ready = [
                    step
                    for step in pending.values()
                    if set(step.get("depends_on") or []) <= set(outputs.keys())
                ]
                if not ready:
                    raise ScheduledTaskExecutionError(
                        "执行计划无法继续：依赖未完成",
                        partial_result=result,
                    )
                for step in ready:
                    step_id = str(step["step_id"])
                    if before_step:
                        before_step(step_id)
                    inputs = _resolve_bindings(step.get("inputs") or {}, outputs=outputs)
                    target = dict(step.get("target_ref") or {})
                    target_name = str(target.get("name") or "").strip()
                    step_type = str(step.get("type") or target.get("kind") or "").strip()
                    if step_type == "tool":
                        raw_output = self.tool_runner(
                            target_name,
                            inputs,
                            runtime_ctx={
                                "scheduled_task_run_id": str(run.get("run_id") or ""),
                                "scheduled_task_id": str(run.get("schedule_id") or ""),
                                "owner_user_id": owner_user_id,
                                "owner_type": "user",
                                "owner_id": owner_user_id,
                                "source_type": "scheduled_task",
                                "task_type": "scheduled_task_step",
                                "custom_tool_owner_ids": [owner_user_id],
                            },
                        )
                    elif step_type == "skill":
                        raw_output = self.skill_runner.run(
                            target_name,
                            inputs,
                            runtime_context={
                                "scheduled_task_run_id": str(run.get("run_id") or ""),
                                "scheduled_task_id": str(run.get("schedule_id") or ""),
                                "owner_user_id": owner_user_id,
                                "owner_type": "user",
                                "owner_id": owner_user_id,
                                "source_type": "scheduled_task",
                                "task_type": "scheduled_task_step",
                                "custom_tool_owner_ids": [owner_user_id],
                            },
                        )
                        raw_output = (
                            raw_output.to_dict()
                            if hasattr(raw_output, "to_dict")
                            else raw_output
                        )
                    else:
                        raise ScheduledTaskExecutionError(
                            f"不支持的步骤类型：{step_type}",
                            partial_result=result,
                        )
                    output = _json_safe(raw_output)
                    outputs[step_id] = {"result": output}
                    result["steps"].append(
                        {
                            "step_id": step_id,
                            "type": step_type,
                            "target_ref": target,
                            "status": "completed",
                            "result": output,
                        }
                    )
                    pending.pop(step_id, None)
        except ScheduledTaskExecutionError:
            raise
        except Exception as exc:
            failed_step = str(step.get("step_id") or "") if "step" in locals() else ""
            result["steps"].append(
                {
                    "step_id": failed_step,
                    "status": "failed",
                    "error": str(exc),
                }
            )
            raise ScheduledTaskExecutionError(
                f"步骤 {failed_step or '-'} 执行失败：{exc}",
                partial_result=result,
            ) from exc
        return result


def _resolve_bindings(value: Any, *, outputs: Mapping[str, Any]) -> Any:
    if isinstance(value, Mapping):
        if set(value.keys()) == {"$from"}:
            path = str(value.get("$from") or "").split(".")
            current: Any = outputs
            for part in path:
                if isinstance(current, Mapping) and part in current:
                    current = current[part]
                elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
                    current = current[int(part)]
                else:
                    raise ScheduledTaskExecutionError(
                        f"找不到步骤结果引用：{value.get('$from')}"
                    )
            return deepcopy(current)
        return {
            str(key): _resolve_bindings(nested, outputs=outputs)
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [_resolve_bindings(item, outputs=outputs) for item in value]
    return deepcopy(value)


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
