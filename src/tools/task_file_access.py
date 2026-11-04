"""Scope legacy file adapters to the current background run's files."""
from pathlib import Path


def task_file_payload(args, runtime_ctx=None):
    payload = dict(args or {})
    if runtime_ctx is None or not runtime_ctx.get("task_run_id"):
        return payload
    output = Path(runtime_ctx["task_output_dir"]).resolve()
    run_root = output.parent.parent
    if run_root.name != runtime_ctx["task_run_id"]:
        raise ValueError("文件工具缺少有效的任务产物范围")
    payload.pop("_data_root", None)
    payload["_runtime"] = {"data_root": str(run_root)}
    if payload.get("file_path"):
        path = Path(str(payload["file_path"])).expanduser().resolve()
        if not path.is_relative_to(run_root):
            raise ValueError("后台文件工具只能读取本次任务已生成的文件，请通过前序步骤传递文件引用")
        payload["file_path"] = str(path)
    return payload
