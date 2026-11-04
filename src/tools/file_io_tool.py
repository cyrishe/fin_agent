from __future__ import annotations

from typing import Any, Dict

from src.services.file_io_tool_service import FileIoToolService
from src.services.file_artifact_service import FileArtifactService
from src.tools.task_file_access import task_file_payload


def run(args: Dict[str, Any], *, runtime_ctx=None) -> Dict[str, Any]:
    payload = task_file_payload(args, runtime_ctx)
    result = FileIoToolService().run(payload)
    if runtime_ctx and runtime_ctx.get("task_run_id") and result.get("ok"):
        data = result.get("data") or {}
        result["summary"] = "文件已生成。" if data.get("mode") == "write" else "已读取本次任务文件。"
        document = (data.get("data") or {}).get("document") or {}
        if document.get("preview_text"):
            result["report_markdown"] = str(document["preview_text"])
        reference = data.get("artifact_ref")
        if reference:
            service = FileArtifactService(data_root=payload["_runtime"]["data_root"])
            manifest = service.read_manifest(reference)
            root = service.artifact_root / manifest["artifact_id"]
            result["artifacts"] = [{"name": str((manifest.get("source_file") or {}).get("file_name") or name),
                                     "path": str(root / relative), "mime_type": manifest.get("content_type", "text/plain")}
                                    for name, relative in manifest["files"].items() if name in {"text", "data"}]
    return result
