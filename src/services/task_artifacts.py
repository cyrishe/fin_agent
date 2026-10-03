from __future__ import annotations

import hashlib
import os
from copy import deepcopy
from pathlib import Path
from typing import Mapping


def task_artifact_root() -> Path:
    return Path(os.environ.get("TASK_ARTIFACT_ROOT") or "outputs/user_tasks").resolve()


def task_step_dir(run_id: str, step_id: str) -> Path:
    root = task_artifact_root()
    path = (root / run_id / step_id).resolve()
    if not path.is_relative_to(root) or not run_id or not step_id:
        raise ValueError("无效任务产物目录")
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_artifacts(run, *, include_path=False):
    root = (task_artifact_root() / str(run["run_id"])).resolve()
    items = []
    seen = set()
    def artifact_entries(value):
        if isinstance(value, Mapping):
            for key, nested in value.items():
                if key == "artifacts" and isinstance(nested, list):
                    yield from (item for item in nested if isinstance(item, Mapping))
                else:
                    yield from artifact_entries(nested)
        elif isinstance(value, list):
            for nested in value:
                yield from artifact_entries(nested)
    for artifact in artifact_entries(run.get("result") or {}):
        if not artifact.get("path"):
            continue
        path = Path(str(artifact["path"])).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            continue
        relative = str(path.relative_to(root))
        artifact_id = hashlib.sha256(relative.encode()).hexdigest()[:24]
        if artifact_id in seen:
            continue
        seen.add(artifact_id)
        item = {"artifact_id": artifact_id, "name": str(artifact.get("name") or path.name),
                "mime_type": str(artifact.get("mime_type") or "application/octet-stream"),
                "size_bytes": path.stat().st_size,
                "url": f"/api/task-runs/{run['run_id']}/artifacts/{artifact_id}"}
        if include_path:
            item["path"] = str(path)
        items.append(item)
    return items


def public_run(run):
    result = deepcopy(dict(run))
    for private in ("lease_token", "lease_owner", "checkpoint"):
        result.pop(private, None)
    artifacts = run_artifacts(run)
    result["artifacts"] = artifacts
    summaries = []
    def clean(value):
        if isinstance(value, dict):
            return {key: clean(item) for key,item in value.items() if key not in {"path", "output_dir", "run_dir", "resume_dir"}}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value
    for step in (run.get("result") or {}).get("steps") or []:
        output = step.get("result") or {}
        if isinstance(output, Mapping):
            final = output.get("final_output") if isinstance(output.get("final_output"), Mapping) else {}
            summary = output.get("summary") or final.get("summary")
            if summary:
                summaries.append(str(summary))
    result["summary"] = "\n".join(summaries)
    result["result"] = clean(result.get("result") or {})
    return result
