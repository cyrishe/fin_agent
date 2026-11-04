from __future__ import annotations

import json
import multiprocessing
import pickle
import os
import signal
import threading
import time
import shutil
from pathlib import Path
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
        self.supervise = tool_runner is None and skill_runner is None
        self.tool_runner = tool_runner or run_tool
        self.skill_runner = skill_runner or SkillRunner()

    def execute(
        self,
        run: Mapping[str, Any],
        *,
        before_step: Callable[[str], None] | None = None,
        on_progress: Callable[..., None] | None = None,
        check_control: Callable[[], None] | None = None,
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
        saved = run.get("result") or {}
        completed = [dict(item) for item in saved.get("steps") or [] if item.get("status") == "completed"]
        outputs: Dict[str, Dict[str, Any]] = {item["step_id"]: {"result": item["result"]} for item in completed}
        pending = {str(step.get("step_id") or ""): step for step in steps if step.get("step_id") not in outputs}
        result: Dict[str, Any] = {
            "schema_version": "scheduled_task_run_result.v1",
            "steps": completed,
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
                    self.authorizer({"steps": [step]}, owner_user_id)
                    from src.services.task_artifacts import task_step_dir
                    base = task_step_dir(str(run.get("run_id") or "local"), step_id)
                    attempt = base / str(run.get("lease_token") or "local")
                    attempt.mkdir(parents=True, exist_ok=True)
                    checkpoint = deepcopy((run.get("checkpoint") or {}).get(step_id) or {})
                    old_dir = checkpoint.pop("_attempt_dir", "")
                    if old_dir and Path(old_dir).resolve() != attempt.resolve():
                        previous = Path(old_dir).resolve()
                        if not previous.is_relative_to(base.resolve()):
                            raise ScheduledTaskExecutionError("检查点目录不属于当前任务步骤")
                        if previous.is_dir():
                            def copy_checkpoint_file(source, destination):
                                if check_control:
                                    check_control()
                                if not Path(source).resolve().is_relative_to(base.resolve()):
                                    raise ScheduledTaskExecutionError("检查点包含越界文件")
                                return shutil.copy2(source, destination)
                            shutil.copytree(previous, attempt, dirs_exist_ok=True, copy_function=copy_checkpoint_file)
                        checkpoint = _rewrite_paths(checkpoint, str(previous), str(attempt))
                    context = {
                        "scheduled_task_run_id": str(run.get("run_id") or ""),
                        "scheduled_task_id": str(run.get("schedule_id") or ""),
                        "scheduled_task_step_id": step_id,
                        "task_run_id": str(run.get("run_id") or ""),
                        "task_id": str(run.get("schedule_id") or ""), "task_step_id": step_id,
                        "scheduled_for": str(run.get("scheduled_for") or ""),
                        "task_output_dir": str(attempt.resolve()), "task_checkpoint": checkpoint,
                        "owner_user_id": owner_user_id, "owner_type": "user", "owner_id": owner_user_id,
                        "source_type": "scheduled_task", "task_type": "scheduled_task_step",
                        "_execution_tracking_owner": "user_task_runtime",
                        "custom_tool_owner_ids": [owner_user_id],
                    }
                    last_progress = {"step_id": step_id, "completed_steps": len(outputs), "total_steps": len(steps)}
                    def publish(payload=None, *, checkpoint=None):
                        if check_control:
                            check_control()
                        last_progress.update(dict(payload or {}))
                        progress = dict(last_progress)
                        if checkpoint is not None:
                            checkpoint = {**dict(checkpoint), "_attempt_dir": str(attempt.resolve())}
                        if on_progress:
                            on_progress(progress=progress, checkpoint=checkpoint, step_id=step_id, result=result)
                    publish({"message": f"正在执行 {target_name}"}, checkpoint=checkpoint)
                    if self.supervise:
                        raw_output = _supervised_step(step_type, target_name, inputs, context, publish, check_control)
                    else:
                        context.update(task_progress=publish, task_check_cancel=check_control or (lambda: None),
                                       task_save_checkpoint=lambda data: publish(checkpoint=data))
                        if step_type == "tool":
                            raw_output = self.tool_runner(target_name, inputs, runtime_ctx=context)
                        elif step_type == "skill":
                            raw_output = self.skill_runner.run(target_name, inputs, runtime_context=context)
                            raw_output = raw_output.to_dict() if hasattr(raw_output, "to_dict") else raw_output
                        else:
                            raise ScheduledTaskExecutionError(f"不支持的步骤类型：{step_type}", partial_result=result)
                    if check_control:
                        check_control()
                    if isinstance(raw_output, Mapping) and raw_output.get("ok") is False:
                        failure = raw_output.get("error") or raw_output.get("message") or "执行资产返回失败"
                        raise ScheduledTaskExecutionError(str(failure), partial_result=result)
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
                    publish({"message": f"已完成 {target_name}", "completed_steps": len(outputs)})
        except ScheduledTaskExecutionError as exc:
            exc.partial_result = deepcopy(result)
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


def _rewrite_paths(value, old, new):
    if isinstance(value, str):
        return new + value[len(old):] if value == old or value.startswith(old + os.sep) else value
    if isinstance(value, dict):
        return {k: _rewrite_paths(v, old, new) for k,v in value.items()}
    if isinstance(value, list):
        return [_rewrite_paths(v, old, new) for v in value]
    return value


def _child_step(connection, step_type, name, inputs, context, parent_pid):
    if hasattr(os, "setsid"):
        os.setsid()
    # Bound BLAS parallelism independently of ML model options.
    for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[variable] = "1"
    def watchdog():
        while True:
            if os.getppid() != parent_pid:
                # Stop descendants as well: the worker can no longer supervise them.
                if hasattr(os, "killpg"):
                    os.killpg(os.getpgrp(), signal.SIGKILL)
                os._exit(75)
            time.sleep(0.5)
    threading.Thread(target=watchdog, daemon=True).start()
    def emit(progress=None, checkpoint=None):
        connection.send(("progress", {"progress": dict(progress or {}), "checkpoint": checkpoint}))
    def check():
        if os.getppid() != parent_pid:
            raise RuntimeError("worker 已停止")
    context.update(task_progress=lambda value: emit(progress=value), task_check_cancel=check,
                   task_save_checkpoint=lambda value: emit(checkpoint=value))
    try:
        if step_type == "tool":
            value = run_tool(name, inputs, runtime_ctx=context)
        else:
            from src.skill_runtime.tool_adapter import ToolAdapter
            class OwnedTaskTools(ToolAdapter):
                # Bind authority out of band; model-supplied _runtime is never used.
                def execute(self, tool_name, arguments, *, execution_profile=""):
                    if execution_profile not in {"", "real"}:
                        raise ValueError("后台任务只执行真实工具配置")
                    ScheduledTaskCompiler().authorize_plan({"steps": [{"type": "tool", "target_ref": {"kind": "tool", "name": tool_name}}]}, owner_user_id=context["owner_user_id"])
                    clean = dict(arguments or {})
                    clean.pop("_runtime", None)
                    clean.pop("_execution_profile", None)
                    return run_tool(tool_name, clean, runtime_ctx=context)
            value = SkillRunner(tool_adapter=OwnedTaskTools()).run(name, inputs, runtime_context=context,
                event_handler=lambda event: emit(progress={key: event[key] for key in ("message", "stage", "step_index", "tool_name") if key in event}))
            value = value.to_dict() if hasattr(value, "to_dict") else value
        connection.send(("result", _json_safe(value)))
    except BaseException as exc:
        connection.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        connection.close()


def _supervised_step(step_type, name, inputs, context, publish, check_control):
    mp = multiprocessing.get_context("spawn")
    parent, child = mp.Pipe(duplex=False)
    process = mp.Process(target=_child_step, args=(child,step_type,name,inputs,context,os.getpid()))
    process.start()
    child.close()
    try:
        while True:
            if check_control:
                check_control()
            if parent.poll(0.2):
                try:
                    kind, value = pickle.loads(parent.recv_bytes(maxlength=8 * 1024 * 1024))
                except OSError as exc:
                    raise ScheduledTaskExecutionError("步骤结果超过 8 MiB；大型结果应保存为产物") from exc
                except EOFError as exc:
                    raise ScheduledTaskExecutionError(f"步骤进程退出，exit={process.exitcode}") from exc
                if kind == "progress":
                    publish(value.get("progress"), checkpoint=value.get("checkpoint"))
                elif kind == "result":
                    return value
                else:
                    raise ScheduledTaskExecutionError(value)
            elif not process.is_alive():
                raise ScheduledTaskExecutionError(f"步骤进程退出，exit={process.exitcode}")
    finally:
        # The group may outlive its leader. Always stop descendants, including
        # when a tool returned a result and exited before this parent observed it.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, AttributeError):
            if process.is_alive():
                process.terminate()
        process.join(2)
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, AttributeError):
            if process.is_alive():
                process.kill()
        process.join(2)
        parent.close()
