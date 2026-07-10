from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any, Callable, Dict, List, Optional

from src.services.file_artifact_service import FileArtifactError, FileArtifactService
from src.services.python_execution_runtime import DEFAULT_RUNTIME_PROFILE, PythonExecutionRuntime


RuntimeEventSink = Callable[..., None]


class CodeWorkItemRunner:
    def __init__(
        self,
        *,
        python_runtime: Optional[PythonExecutionRuntime] = None,
        runtime_root: Optional[str] = None,
        file_artifact_service: Optional[FileArtifactService] = None,
    ) -> None:
        self.python_runtime = python_runtime or PythonExecutionRuntime()
        self.runtime_root = Path(runtime_root or tempfile.gettempdir()) / "stock_agent_code_runtime"
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.file_artifact_service = file_artifact_service or FileArtifactService()

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def run(
        self,
        *,
        step: Dict[str, Any],
        resolved_inputs: Dict[str, Any],
        runtime_trace: Optional[Dict[str, Any]] = None,
        append_event: Optional[RuntimeEventSink] = None,
    ) -> Dict[str, Any]:
        step_id = self._trim(step.get("step_id"))
        step_name = self._trim(step.get("name") or step.get("item_action")) or "analysis_python"
        code = self._extract_code(step)
        code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest() if code else ""
        code_task_spec = step.get("code_task_spec") if isinstance(step.get("code_task_spec"), dict) else {}
        runtime_profile = self._resolve_runtime_profile(step)
        max_attempts = self._max_attempts(step=step, runtime_profile=runtime_profile)
        run_dir = Path(tempfile.mkdtemp(prefix=f"{step_id or 'step'}_", dir=str(self.runtime_root)))
        input_dir = run_dir / "inputs"
        output_dir = run_dir / "outputs"
        input_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        input_payload = {
            "step_id": step_id,
            "intent": self._trim(step.get("intent")),
            "code_task_spec": code_task_spec,
            "inputs": dict(resolved_inputs or {}),
        }
        materialization_error = ""
        materialization_failure_kind = ""
        try:
            artifact_inputs = self._materialize_input_artifacts(resolved_inputs or {}, input_dir=input_dir)
            if artifact_inputs:
                input_payload["artifact_inputs"] = artifact_inputs
                (input_dir / "artifact_manifest.json").write_text(
                    json.dumps({"artifact_inputs": artifact_inputs}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        except FileArtifactError as exc:
            materialization_failure_kind = exc.failure_kind
            materialization_error = exc.message
        (input_dir / "input.json").write_text(json.dumps(input_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        attempts: List[Dict[str, Any]] = []
        final_runtime_result: Dict[str, Any] = {}
        final_envelope: Dict[str, Any] = {}
        failure_kind = ""
        error_text = ""

        if materialization_failure_kind:
            failure_kind = materialization_failure_kind
            error_text = materialization_error or materialization_failure_kind
            final_runtime_result = {
                "ok": False,
                "failure_kind": failure_kind,
                "diagnostics": {"attempt": 1, "stdout": "", "stderr": error_text, "duration_ms": 0},
            }
            attempts.append(final_runtime_result.get("diagnostics") or {})
        elif not code:
            failure_kind = "schema_invalid"
            error_text = "code step is missing inline python code"
            final_runtime_result = {
                "ok": False,
                "failure_kind": failure_kind,
                "diagnostics": {"attempt": 1, "stdout": "", "stderr": error_text, "duration_ms": 0},
            }
            attempts.append(final_runtime_result.get("diagnostics") or {})
        else:
            for attempt in range(1, max_attempts + 1):
                self._emit(
                    append_event=append_event,
                    runtime_trace=runtime_trace,
                    event_type="code_call",
                    actor_id=step_name,
                    payload={
                        "step_id": step_id,
                        "code_hash": code_hash,
                        "profile": self._trim(runtime_profile.get("name")) or "analysis_python_v1",
                        "attempt": attempt,
                        "input_artifact": self._artifact_ref(input_dir),
                        "output_artifact": self._artifact_ref(output_dir),
                    },
                )
                runtime_result = self.python_runtime.execute(
                    code=code,
                    input_dir=str(input_dir),
                    output_dir=str(output_dir),
                    profile=runtime_profile,
                    attempt=attempt,
                )
                final_runtime_result = runtime_result
                diagnostics = dict(runtime_result.get("diagnostics") or {})
                attempts.append(diagnostics)
                failure_kind = self._trim(runtime_result.get("failure_kind"))
                if failure_kind:
                    error_text = self._trim(diagnostics.get("stderr")) or failure_kind
                else:
                    output_limit = self._artifact_total_bytes_limit(runtime_profile)
                    output_size = self._artifact_total_bytes(output_dir)
                    if output_limit is not None and output_size > output_limit:
                        failure_kind = "output_limit_exceeded"
                        error_text = f"output artifacts exceed limit: {output_size} > {output_limit} bytes"
                    else:
                        diagnostics["artifact_total_bytes"] = output_size
                        diagnostics["artifact_total_limit_bytes"] = output_limit
                if not failure_kind:
                    final_envelope, failure_kind, error_text = self._read_and_validate_envelope(
                        output_dir=output_dir,
                        output_json_bytes=self._output_json_bytes(runtime_profile),
                    )
                if not failure_kind:
                    break
                if attempt < max_attempts:
                    self._emit(
                        append_event=append_event,
                        runtime_trace=runtime_trace,
                        event_type="code_retry",
                        actor_id=step_name,
                        payload={"step_id": step_id, "attempt": attempt, "failure_kind": failure_kind},
                    )

        artifacts = self._collect_artifacts(output_dir)
        diagnostics = dict(final_runtime_result.get("diagnostics") or {})
        diagnostics["attempts"] = attempts
        diagnostics["input_artifact"] = self._artifact_ref(input_dir)
        diagnostics["output_artifact"] = self._artifact_ref(output_dir)
        diagnostics["artifacts"] = artifacts
        diagnostics["max_attempts"] = max_attempts
        output_limit = self._artifact_total_bytes_limit(runtime_profile)
        diagnostics["artifact_total_bytes"] = self._artifact_total_bytes(output_dir)
        diagnostics["artifact_total_limit_bytes"] = output_limit

        if failure_kind:
            result = {
                "tool": step_name,
                "name": step_name,
                "ok": False,
                "data": {"structured_data": {}, "render_blocks": [], "artifacts": artifacts},
                "error": error_text or failure_kind,
                "meta": {"diagnostics": diagnostics, "failure_kind": failure_kind},
            }
            self._emit(
                append_event=append_event,
                runtime_trace=runtime_trace,
                event_type="code_runtime_error",
                actor_id=step_name,
                payload={
                    "step_id": step_id,
                    "status": "failed",
                    "failure_kind": failure_kind,
                    "attempt": len(attempts) or 1,
                    "diagnostics": self._diagnostics_summary(diagnostics),
                },
            )
            return self._run_record(
                step=step,
                step_name=step_name,
                status="failed",
                reason=failure_kind,
                error=error_text or failure_kind,
                result=result,
                diagnostics=diagnostics,
            )

        result = self._complete_envelope(final_envelope, step_name=step_name, artifacts=artifacts, diagnostics=diagnostics)
        self._emit(
            append_event=append_event,
            runtime_trace=runtime_trace,
            event_type="code_result",
            actor_id=step_name,
            payload={
                "step_id": step_id,
                "status": "completed",
                "ok": True,
                "attempt": len(attempts) or 1,
                "diagnostics": self._diagnostics_summary(diagnostics),
                "output_artifact": diagnostics.get("output_artifact"),
            },
        )
        return self._run_record(
            step=step,
            step_name=step_name,
            status="completed",
            reason="ok",
            error="",
            result=result,
            diagnostics=diagnostics,
        )

    def _materialize_input_artifacts(self, resolved_inputs: Dict[str, Any], *, input_dir: Path) -> Dict[str, Any]:
        artifact_inputs: Dict[str, Any] = {}
        for input_key, artifact_ref in self._iter_artifact_refs(resolved_inputs):
            artifact_inputs[input_key] = self.file_artifact_service.materialize(artifact_ref, input_dir=input_dir)
        return artifact_inputs

    def _iter_artifact_refs(self, value: Any, *, prefix: str = "") -> List[tuple[str, str]]:
        refs: List[tuple[str, str]] = []
        if isinstance(value, str) and FileArtifactService.is_artifact_ref(value):
            refs.append((prefix or "artifact", value))
            return refs
        if isinstance(value, dict):
            artifact_ref = value.get("artifact_ref")
            if isinstance(artifact_ref, str) and FileArtifactService.is_artifact_ref(artifact_ref):
                refs.append((prefix or "artifact", artifact_ref))
            for key, child in value.items():
                if key == "artifact_ref":
                    continue
                child_prefix = f"{prefix}.{key}" if prefix else str(key)
                refs.extend(self._iter_artifact_refs(child, prefix=child_prefix))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                child_prefix = f"{prefix}.{index}" if prefix else str(index)
                refs.extend(self._iter_artifact_refs(child, prefix=child_prefix))
        return refs

    def _extract_code(self, step: Dict[str, Any]) -> str:
        code_task_spec = step.get("code_task_spec") if isinstance(step.get("code_task_spec"), dict) else {}
        for source in (step, code_task_spec):
            for key in ("code", "source", "python_code", "inline_code"):
                value = source.get(key) if isinstance(source, dict) else ""
                if self._trim(value):
                    return str(value)
        return ""

    def _resolve_runtime_profile(self, step: Dict[str, Any]) -> Dict[str, Any]:
        profile = json.loads(json.dumps(DEFAULT_RUNTIME_PROFILE, ensure_ascii=False))
        raw_profile = step.get("runtime_profile")
        if isinstance(raw_profile, dict):
            for key, value in raw_profile.items():
                if key == "limits" and isinstance(value, dict):
                    profile["limits"].update(value)
                else:
                    profile[key] = value
        elif self._trim(raw_profile):
            profile["name"] = self._trim(raw_profile)
        code_task_spec = step.get("code_task_spec") if isinstance(step.get("code_task_spec"), dict) else {}
        if isinstance(code_task_spec.get("runtime_profile"), dict):
            for key, value in code_task_spec["runtime_profile"].items():
                if key == "limits" and isinstance(value, dict):
                    profile["limits"].update(value)
                else:
                    profile[key] = value
        return profile

    def _max_attempts(self, *, step: Dict[str, Any], runtime_profile: Dict[str, Any]) -> int:
        limits = runtime_profile.get("limits") if isinstance(runtime_profile.get("limits"), dict) else {}
        raw = step.get("max_attempts") or limits.get("max_attempts") or 1
        try:
            value = int(raw)
        except Exception:
            value = 1
        return min(max(value, 1), 3)

    def _output_json_bytes(self, runtime_profile: Dict[str, Any]) -> int:
        limits = runtime_profile.get("limits") if isinstance(runtime_profile.get("limits"), dict) else {}
        try:
            return int(limits.get("output_json_bytes") or 2097152)
        except Exception:
            return 2097152

    def _artifact_total_bytes_limit(self, runtime_profile: Dict[str, Any]) -> Optional[int]:
        limits = runtime_profile.get("limits") if isinstance(runtime_profile.get("limits"), dict) else {}
        raw = limits.get("artifact_total_bytes")
        if raw is None:
            raw = limits.get("artifact_total_mb")
            multiplier = 1024 * 1024
        else:
            multiplier = 1
        try:
            value = int(raw)
        except Exception:
            return None
        if value <= 0:
            return None
        return value * multiplier

    def _artifact_total_bytes(self, output_dir: Path) -> int:
        if not output_dir.exists():
            return 0
        total = 0
        for path in output_dir.rglob("*"):
            if path.is_file():
                total += int(path.stat().st_size)
        return total

    def _read_and_validate_envelope(self, *, output_dir: Path, output_json_bytes: int) -> tuple[Dict[str, Any], str, str]:
        output_path = output_dir / "output.json"
        if not output_path.exists():
            return {}, "schema_invalid", "output.json is missing"
        if output_path.stat().st_size > output_json_bytes:
            return {}, "schema_invalid", "output.json exceeds size limit"
        try:
            payload = json.loads(output_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {}, "schema_invalid", f"output.json is not valid JSON: {exc}"
        if not isinstance(payload, dict):
            return {}, "schema_invalid", "output envelope must be a JSON object"
        if not isinstance(payload.get("ok"), bool) or not isinstance(payload.get("data"), dict):
            return {}, "schema_invalid", "output envelope requires boolean ok and object data"
        if payload.get("ok") is False:
            return payload, self._trim(payload.get("failure_kind")) or "runtime_error", self._trim(payload.get("error")) or "code returned ok=false"
        return payload, "", ""

    def _complete_envelope(
        self,
        envelope: Dict[str, Any],
        *,
        step_name: str,
        artifacts: List[Dict[str, Any]],
        diagnostics: Dict[str, Any],
    ) -> Dict[str, Any]:
        result = dict(envelope or {})
        result["tool"] = self._trim(result.get("tool")) or step_name
        result["name"] = self._trim(result.get("name")) or step_name
        result["ok"] = True
        result["error"] = self._trim(result.get("error"))
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        data.setdefault("structured_data", {})
        data.setdefault("render_blocks", [])
        data["artifacts"] = artifacts
        result["data"] = data
        meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
        meta["diagnostics"] = diagnostics
        result["meta"] = meta
        return result

    def _collect_artifacts(self, output_dir: Path) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        if not output_dir.exists():
            return artifacts
        for path in sorted(output_dir.iterdir()):
            if not path.is_file() or path.name == "output.json":
                continue
            artifacts.append(self._artifact_ref(path))
        return artifacts

    def _artifact_ref(self, path: Path) -> Dict[str, Any]:
        ref = {
            "path": str(path),
            "name": path.name,
            "kind": "directory" if path.is_dir() else "file",
        }
        if path.exists() and path.is_file():
            ref["size_bytes"] = int(path.stat().st_size)
        return ref

    def _diagnostics_summary(self, diagnostics: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "code_hash": self._trim(diagnostics.get("code_hash")),
            "backend": self._trim(diagnostics.get("backend")),
            "attempt": int(diagnostics.get("attempt") or len(diagnostics.get("attempts") or []) or 1),
            "duration_ms": int(diagnostics.get("duration_ms") or 0),
            "exit_code": diagnostics.get("exit_code"),
            "stdout": self._trim(diagnostics.get("stdout"))[:500],
            "stderr": self._trim(diagnostics.get("stderr"))[:500],
        }

    def _run_record(
        self,
        *,
        step: Dict[str, Any],
        step_name: str,
        status: str,
        reason: str,
        error: str,
        result: Dict[str, Any],
        diagnostics: Dict[str, Any],
    ) -> Dict[str, Any]:
        depends_on = [self._trim(item) for item in (step.get("depends_on") or []) if self._trim(item)]
        return {
            "step_id": self._trim(step.get("step_id")),
            "depends_on": depends_on,
            "tool_name": step_name,
            "status": status,
            "reason": reason,
            "error": error,
            "plan": {
                "arguments": {},
                "runtime_profile": self._trim(diagnostics.get("profile_name")),
                "attempts": len(diagnostics.get("attempts") or []) or 1,
            },
            "result": result,
            "retention": {"prompt_context": {"code_result": result.get("data") if isinstance(result.get("data"), dict) else {}}},
            "named_outputs": {},
            "failure_kind": reason if status == "failed" else "",
            "diagnostics": diagnostics,
        }

    def _emit(
        self,
        *,
        append_event: Optional[RuntimeEventSink],
        runtime_trace: Optional[Dict[str, Any]],
        event_type: str,
        actor_id: str,
        payload: Dict[str, Any],
    ) -> None:
        if not append_event:
            return
        append_event(
            runtime_trace=runtime_trace or {},
            event_type=event_type,
            actor_type="code_runtime",
            actor_id=actor_id,
            payload=payload,
        )
