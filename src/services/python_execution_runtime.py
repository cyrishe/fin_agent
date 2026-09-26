from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from typing import Any, Callable, Dict, List, Optional


DEFAULT_RUNTIME_PROFILE: Dict[str, Any] = {
    "name": "analysis_python_v1",
    "language": "python",
    "backend": "auto",
    "backend_candidates": ["bwrap", "nsjail", "docker", "podman"],
    "network": "none",
    "workspace_access": "none",
    "limits": {
        "timeout_ms": 10000,
        "stdout_chars": 20000,
        "stderr_chars": 20000,
        "output_json_bytes": 2097152,
        "artifact_total_mb": 50,
    },
}


class PythonExecutionRuntime:
    FORMAL_BACKENDS = {"bwrap", "nsjail", "docker", "podman"}
    UNSAFE_DEV_BACKENDS = {"local_dev", "strict_fake"}

    def __init__(self, *, default_profile: Optional[Dict[str, Any]] = None, allow_unsafe_backends: bool = False) -> None:
        self.default_profile = self._merge_profile(default_profile or {})
        self.allow_unsafe_backends = bool(allow_unsafe_backends)

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _limit_int(value: Any, fallback: int) -> int:
        try:
            parsed = int(value)
        except Exception:
            return fallback
        return parsed if parsed > 0 else fallback

    @staticmethod
    def _shorten(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"

    def _merge_profile(self, profile: Dict[str, Any]) -> Dict[str, Any]:
        merged = json.loads(json.dumps(DEFAULT_RUNTIME_PROFILE, ensure_ascii=False))
        for key, value in (profile or {}).items():
            if key == "limits" and isinstance(value, dict):
                merged["limits"].update(value)
            else:
                merged[key] = value
        return merged

    def resolve_backend(self, profile: Optional[Dict[str, Any]] = None) -> str:
        """Resolve the effective backend without starting user code."""

        backend = self._select_backend(self._merge_profile(profile or {}))
        if backend in self.FORMAL_BACKENDS and not shutil.which(backend):
            return "sandbox_unavailable"
        return backend

    def execute(
        self,
        *,
        code: str,
        input_dir: str,
        output_dir: str,
        profile: Optional[Dict[str, Any]] = None,
        attempt: int = 1,
    ) -> Dict[str, Any]:
        runtime_profile = self._merge_profile(profile or {})
        code_text = str(code or "")
        code_hash = hashlib.sha256(code_text.encode("utf-8")).hexdigest()
        backend = self._select_backend(runtime_profile)
        limits = runtime_profile.get("limits") if isinstance(runtime_profile.get("limits"), dict) else {}
        if backend in self.UNSAFE_DEV_BACKENDS and not self.allow_unsafe_backends:
            return self._result(
                code_hash=code_hash,
                backend=backend,
                attempt=attempt,
                stdout="",
                stderr=f"backend `{backend}` is not allowed for untrusted code runtime",
                duration_ms=0,
                exit_code=None,
                failure_kind="sandbox_unavailable",
                profile=runtime_profile,
                limits=limits,
            )
        if backend == "sandbox_unavailable":
            return self._result(
                code_hash=code_hash,
                backend="auto",
                attempt=attempt,
                stdout="",
                stderr="no formal sandbox backend is available",
                duration_ms=0,
                exit_code=None,
                failure_kind="sandbox_unavailable",
                profile=runtime_profile,
                limits=limits,
            )
        if backend == "strict_fake" and self._violates_strict_fake_policy(code_text):
            return self._result(
                code_hash=code_hash,
                backend=backend,
                attempt=attempt,
                stdout="",
                stderr="permission denied by strict_fake backend",
                duration_ms=0,
                exit_code=None,
                failure_kind="permission_denied",
                profile=runtime_profile,
                limits=limits,
            )
        if backend == "bwrap":
            executable = shutil.which(backend)
            if not executable:
                return self._result(
                    code_hash=code_hash,
                    backend=backend,
                    attempt=attempt,
                    stdout="",
                    stderr=f"backend `{backend}` is not available",
                    duration_ms=0,
                    exit_code=None,
                    failure_kind="sandbox_unavailable",
                    profile=runtime_profile,
                    limits=limits,
                )
            return self._execute_bwrap(
                code=code_text,
                input_dir=input_dir,
                output_dir=output_dir,
                runtime_profile=runtime_profile,
                backend=backend,
                executable=executable,
                code_hash=code_hash,
                attempt=attempt,
            )
        if backend == "nsjail":
            executable = shutil.which(backend)
            message = f"backend `{backend}` is not available"
            if executable:
                message = f"backend `{backend}` is not implemented for this MVP"
            return self._result(
                code_hash=code_hash,
                backend=backend,
                attempt=attempt,
                stdout="",
                stderr=message,
                duration_ms=0,
                exit_code=None,
                failure_kind="sandbox_unavailable",
                profile=runtime_profile,
                limits=limits,
            )
        if backend in {"docker", "podman"}:
            executable = shutil.which(backend)
            if not executable:
                return self._result(
                    code_hash=code_hash,
                    backend=backend,
                    attempt=attempt,
                    stdout="",
                    stderr=f"backend `{backend}` is not available",
                    duration_ms=0,
                    exit_code=None,
                    failure_kind="sandbox_unavailable",
                    profile=runtime_profile,
                    limits=limits,
                )
            return self._execute_container(
                code=code_text,
                input_dir=input_dir,
                output_dir=output_dir,
                runtime_profile=runtime_profile,
                backend=backend,
                executable=executable,
                code_hash=code_hash,
                attempt=attempt,
            )
        if backend not in self.UNSAFE_DEV_BACKENDS:
            return self._result(
                code_hash=code_hash,
                backend=backend,
                attempt=attempt,
                stdout="",
                stderr=f"backend `{backend}` is not supported",
                duration_ms=0,
                exit_code=None,
                failure_kind="sandbox_unavailable",
                profile=runtime_profile,
                limits=limits,
            )
        return self._execute_local_dev(
            code=code_text,
            input_dir=input_dir,
            output_dir=output_dir,
            runtime_profile=runtime_profile,
            backend=backend,
            code_hash=code_hash,
            attempt=attempt,
        )

    def _select_backend(self, profile: Dict[str, Any]) -> str:
        backend = self._trim(profile.get("backend")) or "auto"
        if backend != "auto":
            return backend
        candidates = [self._trim(item) for item in (profile.get("backend_candidates") or []) if self._trim(item)]
        for candidate in candidates:
            if candidate in self.UNSAFE_DEV_BACKENDS and not self.allow_unsafe_backends:
                continue
            if candidate in self.FORMAL_BACKENDS and shutil.which(candidate):
                return candidate
        return "sandbox_unavailable"

    def _violates_strict_fake_policy(self, code: str) -> bool:
        forbidden = [
            "socket",
            "requests",
            "urllib",
            "http://",
            "https://",
            "subprocess",
            "os.environ",
            "Path.home",
            "expanduser",
            "/Users/",
            "/Volumes/",
            "/home/",
            "/etc/",
            "../",
        ]
        return any(token in code for token in forbidden)

    def _execute_local_dev(
        self,
        *,
        code: str,
        input_dir: str,
        output_dir: str,
        runtime_profile: Dict[str, Any],
        backend: str,
        code_hash: str,
        attempt: int,
    ) -> Dict[str, Any]:
        limits = runtime_profile.get("limits") if isinstance(runtime_profile.get("limits"), dict) else {}
        timeout_ms = self._limit_int(limits.get("timeout_ms"), 10000)
        stdout_chars = self._limit_int(limits.get("stdout_chars"), 20000)
        stderr_chars = self._limit_int(limits.get("stderr_chars"), 20000)
        run_dir = Path(tempfile.mkdtemp(prefix="stock_agent_code_run_"))
        script_path = run_dir / "main.py"
        script_path.write_text(code, encoding="utf-8")
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        env = {
            "CODE_INPUT_DIR": str(Path(input_dir).resolve()),
            "CODE_OUTPUT_DIR": str(Path(output_dir).resolve()),
            "CODE_INPUT_JSON": str((Path(input_dir) / "input.json").resolve()),
            "PYTHONIOENCODING": "utf-8",
            "MPLCONFIGDIR": str(run_dir / "mpl"),
        }
        return self._execute_subprocess(
            command=[sys.executable, str(script_path)],
            cwd=str(Path(output_dir).resolve()),
            env=env,
            timeout_ms=timeout_ms,
            stdout_chars=stdout_chars,
            stderr_chars=stderr_chars,
            code_hash=code_hash,
            backend=backend,
            attempt=attempt,
            runtime_profile=runtime_profile,
            limits=limits,
        )

    def _execute_bwrap(
        self,
        *,
        code: str,
        input_dir: str,
        output_dir: str,
        runtime_profile: Dict[str, Any],
        backend: str,
        executable: str,
        code_hash: str,
        attempt: int,
    ) -> Dict[str, Any]:
        limits = runtime_profile.get("limits") if isinstance(runtime_profile.get("limits"), dict) else {}
        timeout_ms = self._limit_int(limits.get("timeout_ms"), 10000)
        stdout_chars = self._limit_int(limits.get("stdout_chars"), 20000)
        stderr_chars = self._limit_int(limits.get("stderr_chars"), 20000)
        run_dir = Path(tempfile.mkdtemp(prefix="stock_agent_code_bwrap_"))
        script_path = run_dir / "main.py"
        script_path.write_text(code, encoding="utf-8")
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        command = [
            executable,
            "--die-with-parent",
            "--new-session",
            "--unshare-all",
            "--unshare-net",
            "--clearenv",
            "--setenv",
            "CODE_INPUT_DIR",
            "/runtime/input",
            "--setenv",
            "CODE_OUTPUT_DIR",
            "/runtime/output",
            "--setenv",
            "CODE_INPUT_JSON",
            "/runtime/input/input.json",
            "--setenv",
            "PYTHONIOENCODING",
            "utf-8",
            "--setenv",
            "MPLCONFIGDIR",
            "/tmp/mpl",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--dir",
            "/runtime",
            "--ro-bind",
            str(Path(input_dir).resolve()),
            "/runtime/input",
            "--bind",
            str(Path(output_dir).resolve()),
            "/runtime/output",
            "--ro-bind",
            str(run_dir.resolve()),
            "/runtime/code",
            "--chdir",
            "/runtime/output",
        ]
        for mount in self._bwrap_python_mounts():
            command.extend(["--ro-bind", mount, mount])
        command.extend([sys.executable, "/runtime/code/main.py"])

        return self._execute_subprocess(
            command=command,
            cwd=str(Path(output_dir).resolve()),
            env={},
            timeout_ms=timeout_ms,
            stdout_chars=stdout_chars,
            stderr_chars=stderr_chars,
            code_hash=code_hash,
            backend=backend,
            attempt=attempt,
            runtime_profile=runtime_profile,
            limits=limits,
        )

    def _bwrap_python_mounts(self) -> List[str]:
        candidates = [
            "/bin",
            "/lib",
            "/lib64",
            "/usr/bin",
            "/usr/lib",
            "/usr/lib64",
        ]
        mounts: List[str] = []
        seen = set()
        for candidate in candidates:
            path = Path(candidate)
            if path.exists() and candidate not in seen:
                mounts.append(candidate)
                seen.add(candidate)
        return mounts

    def _execute_container(
        self,
        *,
        code: str,
        input_dir: str,
        output_dir: str,
        runtime_profile: Dict[str, Any],
        backend: str,
        executable: str,
        code_hash: str,
        attempt: int,
    ) -> Dict[str, Any]:
        limits = runtime_profile.get("limits") if isinstance(runtime_profile.get("limits"), dict) else {}
        timeout_ms = self._limit_int(limits.get("timeout_ms"), 10000)
        stdout_chars = self._limit_int(limits.get("stdout_chars"), 20000)
        stderr_chars = self._limit_int(limits.get("stderr_chars"), 20000)
        run_dir = Path(tempfile.mkdtemp(prefix=f"stock_agent_code_{backend}_"))
        script_path = run_dir / "main.py"
        script_path.write_text(code, encoding="utf-8")
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        image = self._trim(runtime_profile.get("container_image")) or "python:3.11-slim"
        container_name = f"stock-agent-code-{uuid.uuid4().hex}"
        cidfile = run_dir / "container.cid"

        command = [
            executable,
            "run",
            "--rm",
            "--name",
            container_name,
            "--cidfile",
            str(cidfile),
            "--network",
            "none",
            "-e",
            "CODE_INPUT_DIR=/runtime/input",
            "-e",
            "CODE_OUTPUT_DIR=/runtime/output",
            "-e",
            "CODE_INPUT_JSON=/runtime/input/input.json",
            "-e",
            "PYTHONIOENCODING=utf-8",
            "-e",
            "MPLCONFIGDIR=/tmp/mpl",
            "-v",
            f"{Path(input_dir).resolve()}:/runtime/input:ro",
            "-v",
            f"{Path(output_dir).resolve()}:/runtime/output",
            "-v",
            f"{run_dir.resolve()}:/runtime/code:ro",
            "-w",
            "/runtime/output",
            image,
            "python",
            "/runtime/code/main.py",
        ]
        timeout_cleanup = self._container_timeout_cleanup(
            executable=executable,
            cidfile=cidfile,
            container_name=container_name,
        )
        return self._execute_subprocess(
            command=command,
            cwd=str(Path(output_dir).resolve()),
            env={},
            timeout_ms=timeout_ms,
            stdout_chars=stdout_chars,
            stderr_chars=stderr_chars,
            code_hash=code_hash,
            backend=backend,
            attempt=attempt,
            runtime_profile=runtime_profile,
            limits=limits,
            timeout_cleanup=timeout_cleanup,
        )

    def _execute_subprocess(
        self,
        *,
        command: List[str],
        cwd: str,
        env: Dict[str, str],
        timeout_ms: int,
        stdout_chars: int,
        stderr_chars: int,
        code_hash: str,
        backend: str,
        attempt: int,
        runtime_profile: Dict[str, Any],
        limits: Dict[str, Any],
        timeout_cleanup: Optional[Callable[[], None]] = None,
    ) -> Dict[str, Any]:
        started = time.monotonic()
        proc = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout_ms / 1000.0)
            duration_ms = int((time.monotonic() - started) * 1000)
            return self._result(
                code_hash=code_hash,
                backend=backend,
                attempt=attempt,
                stdout=self._shorten(stdout or "", stdout_chars),
                stderr=self._shorten(stderr or "", stderr_chars),
                duration_ms=duration_ms,
                exit_code=int(proc.returncode),
                failure_kind="" if proc.returncode == 0 else "runtime_error",
                profile=runtime_profile,
                limits=limits,
            )
        except subprocess.TimeoutExpired:
            self._kill_process_group(proc)
            if timeout_cleanup:
                timeout_cleanup()
            stdout, stderr = proc.communicate()
            duration_ms = int((time.monotonic() - started) * 1000)
            return self._result(
                code_hash=code_hash,
                backend=backend,
                attempt=attempt,
                stdout=self._shorten(stdout or "", stdout_chars),
                stderr=self._shorten(stderr or "", stderr_chars),
                duration_ms=duration_ms,
                exit_code=None,
                failure_kind="timeout",
                profile=runtime_profile,
                limits=limits,
            )

    def _container_timeout_cleanup(self, *, executable: str, cidfile: Path, container_name: str) -> Callable[[], None]:
        def cleanup() -> None:
            container_id = ""
            try:
                if cidfile.exists():
                    container_id = cidfile.read_text(encoding="utf-8").strip()
            except Exception:
                container_id = ""
            target = container_id or container_name
            if not target:
                return
            try:
                subprocess.run(
                    [executable, "rm", "-f", target],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    timeout=10,
                    check=False,
                )
            except Exception:
                pass

        return cleanup

    def _kill_process_group(self, proc: subprocess.Popen) -> None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _result(
        self,
        *,
        code_hash: str,
        backend: str,
        attempt: int,
        stdout: str,
        stderr: str,
        duration_ms: int,
        exit_code: Optional[int],
        failure_kind: str,
        profile: Dict[str, Any],
        limits: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "ok": not bool(failure_kind),
            "failure_kind": failure_kind,
            "diagnostics": {
                "code_hash": code_hash,
                "backend": backend,
                "attempt": int(attempt or 1),
                "stdout": stdout,
                "stderr": stderr,
                "duration_ms": int(duration_ms or 0),
                "exit_code": exit_code,
                "profile_name": self._trim(profile.get("name")) or "analysis_python_v1",
                "limits": dict(limits or {}),
                "backend_is_formal_sandbox": backend in self.FORMAL_BACKENDS,
            },
        }
