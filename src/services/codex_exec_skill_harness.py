from __future__ import annotations

import json
import os
from pathlib import Path
import selectors
import signal
import shutil
import subprocess
import tempfile
import time
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from src.services.custom_tool_context_bundle_service import CustomToolContextBundleService


def _trim(value: Any) -> str:
    return str(value or "").strip()


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


class CodexExecSkillHarness:
    """Thin wrapper around `codex exec` for skill-driven custom-tool stages."""

    def __init__(
        self,
        *,
        codex_bin: str = "codex",
        cwd: str = ".",
        timeout_seconds: int = 180,
        hard_timeout_seconds: int = 0,
        model: str = "",
        sandbox: str = "workspace-write",
        context_bundle_service: Optional[CustomToolContextBundleService] = None,
    ) -> None:
        self.codex_bin = codex_bin
        self.cwd = Path(cwd)
        self.timeout_seconds = int(timeout_seconds or 180)
        self.hard_timeout_seconds = int(hard_timeout_seconds or max(self.timeout_seconds * 5, 900))
        self.model = _trim(model)
        self.sandbox = _trim(sandbox) or "workspace-write"
        self.context_bundle_service = context_bundle_service or CustomToolContextBundleService()

    def available(self) -> bool:
        return bool(shutil.which(self.codex_bin))

    def run_skill(
        self,
        *,
        skill_path: str,
        output_schema_path: str = "",
        user_request: str,
        context: Optional[Mapping[str, Any]] = None,
        session_id: str = "",
        stage: str = "",
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        stage_name = stage or self._infer_stage(Path(skill_path))
        stage_events: List[Dict[str, Any]] = []
        if not self.available():
            return {
                "ok": False,
                "error": f"`{self.codex_bin}` is not available",
                "events": [],
                "final": {},
                "session_id": session_id,
            }
        skill_file = Path(skill_path)
        if not skill_file.is_absolute():
            skill_file = self.cwd / skill_file
        if not skill_file.exists():
            return {
                "ok": False,
                "error": f"skill file not found: {skill_file}",
                "events": [],
                "final": {},
                "session_id": session_id,
            }
        try:
            output_schema_file = self._resolve_output_schema_file(
                skill_file=skill_file,
                output_schema_path=output_schema_path,
            )
        except (FileNotFoundError, ValueError) as exc:
            return {
                "ok": False,
                "error": str(exc),
                "events": [],
                "final": {},
                "session_id": session_id,
            }

        run_context = dict(context or {})
        self._append_event(
            stage_events,
            {
                "source": "harness",
                "type": "stage_start",
                "content": f"codex skill stage started: {stage_name}",
                "metadata": {"stage": stage_name, "session_id": session_id},
            },
            event_sink,
        )
        bundle = self.context_bundle_service.build(
            stage=stage_name,
            user_request=user_request,
            context=run_context,
            run_id=session_id,
        )
        run_context["context_bundle"] = bundle
        self._append_event(
            stage_events,
            {
                "source": "harness",
                "type": "context_ready",
                "content": "context bundle prepared",
                "metadata": {"stage": stage_name, "bundle_dir": bundle.get("bundle_dir")},
            },
            event_sink,
        )

        prompt = self._build_prompt(
            skill_text=skill_file.read_text(encoding="utf-8"),
            user_request=user_request,
            context=run_context,
            structured_output=output_schema_file is not None,
        )
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".txt", delete=False) as output_file:
            output_path = output_file.name
        command = [
            self.codex_bin,
            "exec",
            "--json",
            "--cd",
            str(self.cwd),
            "--sandbox",
            self.sandbox,
            "--output-last-message",
            output_path,
        ]
        if self.model:
            command.extend(["--model", self.model])
        if output_schema_file is not None:
            command.extend(["--output-schema", str(output_schema_file)])
        command.append("-")

        started_at = time.time()
        self._append_event(
            stage_events,
            {
                "source": "harness",
                "type": "tool_call",
                "content": "codex exec process started",
                "metadata": {
                    "stage": stage_name,
                    "codex_bin": self.codex_bin,
                    "sandbox": self.sandbox,
                    "idle_timeout_seconds": self.timeout_seconds,
                    "hard_timeout_seconds": self.hard_timeout_seconds,
                },
            },
            event_sink,
        )
        run_result = self._run_process(command, prompt, started_at=started_at, event_sink=event_sink)
        self._append_event(
            stage_events,
            {
                "source": "harness",
                "type": "tool_result",
                "content": "codex exec process finished",
                "metadata": {
                    "stage": stage_name,
                    "returncode": run_result.get("returncode"),
                    "timeout": bool(run_result.get("timeout")),
                    "duration_ms": int((time.time() - started_at) * 1000),
                },
            },
            event_sink,
        )
        process_events = list(run_result.get("events") or [])
        if run_result.get("timeout"):
            timeout_kind = _trim(run_result.get("timeout_kind")) or "timeout"
            return {
                "ok": False,
                "error": f"codex exec {timeout_kind} after {run_result.get('timeout_after_seconds') or self.timeout_seconds}s",
                "events": stage_events + process_events,
                "final": {},
                "session_id": session_id,
                "raw_stdout": run_result.get("stdout") or "",
                "raw_stderr": run_result.get("stderr") or "",
                "context_bundle": bundle,
            }

        last_message = ""
        try:
            last_message = Path(output_path).read_text(encoding="utf-8")
        except Exception:
            last_message = ""
        finally:
            try:
                os.unlink(output_path)
            except OSError:
                pass

        events = stage_events + process_events
        events.extend(self._extract_model_events(last_message))
        final = self._final_from_text(last_message) or self._find_final(events)
        if final and not self._find_final(events):
            self._append_event(
                events,
                {**final, "metadata": {"stage": stage_name}},
                event_sink,
            )
        self._append_event(
            events,
            {
                "source": "harness",
                "type": "stage_result",
                "content": "codex skill stage parsed",
                "metadata": {
                    "stage": stage_name,
                    "ok": run_result.get("returncode") == 0 and bool(final),
                    "final_status": final.get("status"),
                    "duration_ms": int((time.time() - started_at) * 1000),
                },
            },
            event_sink,
        )
        return {
            "ok": run_result.get("returncode") == 0 and bool(final),
            "error": "" if run_result.get("returncode") == 0 else _trim(run_result.get("stderr")) or f"codex exec exited {run_result.get('returncode')}",
            "events": events,
            "final": final,
            "session_id": session_id,
            "raw_stdout": run_result.get("stdout") or "",
            "raw_stderr": run_result.get("stderr") or "",
            "last_message": last_message,
            "context_bundle": bundle,
            "duration_ms": int((time.time() - started_at) * 1000),
        }

    def _run_process(
        self,
        command: List[str],
        prompt: str,
        *,
        started_at: float,
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(self.cwd),
            bufsize=1,
            start_new_session=True,
        )
        assert proc.stdin is not None
        assert proc.stdout is not None
        assert proc.stderr is not None
        proc.stdin.write(prompt)
        proc.stdin.close()

        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
        selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
        stdout_lines: List[str] = []
        stderr_lines: List[str] = []
        events: List[Dict[str, Any]] = []
        timeout = False
        timeout_kind = ""
        timeout_after_seconds = 0
        last_output_at = started_at

        while selector.get_map():
            now = time.time()
            if now - started_at > self.hard_timeout_seconds:
                timeout = True
                timeout_kind = "hard timeout"
                timeout_after_seconds = self.hard_timeout_seconds
                timeout_event = {
                    "source": "harness",
                    "type": "tool_result",
                    "content": "codex exec hard timeout",
                    "metadata": {"status": "error", "duration_ms": int((now - started_at) * 1000), "timeout_kind": timeout_kind},
                }
                events.append(timeout_event)
                self._send_event(timeout_event, event_sink)
                self._kill_process_tree(proc)
                break
            if now - last_output_at > self.timeout_seconds:
                timeout = True
                timeout_kind = "idle timeout"
                timeout_after_seconds = self.timeout_seconds
                timeout_event = {
                    "source": "harness",
                    "type": "tool_result",
                    "content": "codex exec idle timeout",
                    "metadata": {"status": "error", "duration_ms": int((now - started_at) * 1000), "timeout_kind": timeout_kind},
                }
                events.append(timeout_event)
                self._send_event(timeout_event, event_sink)
                self._kill_process_tree(proc)
                break
            ready = selector.select(timeout=0.5)
            if not ready and proc.poll() is not None:
                break
            for key, _ in ready:
                line = key.fileobj.readline()
                if not line:
                    selector.unregister(key.fileobj)
                    continue
                last_output_at = time.time()
                if key.data == "stdout":
                    stdout_lines.append(line)
                    new_events = self._normalize_codex_json_events(line)
                    events.extend(new_events)
                    for event in new_events:
                        self._send_event(event, event_sink)
                else:
                    stderr_lines.append(line)

        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._kill_process_tree(proc)
            proc.wait(timeout=2)
        remaining_stdout = ""
        remaining_stderr = ""
        try:
            remaining_stdout = proc.stdout.read()
        except Exception:
            remaining_stdout = ""
        try:
            remaining_stderr = proc.stderr.read()
        except Exception:
            remaining_stderr = ""
        if remaining_stdout:
            stdout_lines.append(remaining_stdout)
            new_events = self._normalize_codex_json_events(remaining_stdout)
            events.extend(new_events)
            for event in new_events:
                self._send_event(event, event_sink)
        if remaining_stderr:
            stderr_lines.append(remaining_stderr)

        return {
            "returncode": proc.returncode,
            "timeout": timeout,
            "timeout_kind": timeout_kind,
            "timeout_after_seconds": timeout_after_seconds,
            "stdout": "".join(stdout_lines),
            "stderr": "".join(stderr_lines),
            "events": events,
        }

    @staticmethod
    def _send_event(event: Dict[str, Any], event_sink: Optional[Callable[[Dict[str, Any]], None]]) -> None:
        if event_sink is None:
            return
        try:
            event_sink(event)
        except Exception:
            return

    @staticmethod
    def _kill_process_tree(proc: subprocess.Popen[str]) -> None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                return

    @classmethod
    def _append_event(
        cls,
        events: List[Dict[str, Any]],
        event: Dict[str, Any],
        event_sink: Optional[Callable[[Dict[str, Any]], None]],
    ) -> None:
        events.append(event)
        cls._send_event(event, event_sink)

    def _build_prompt(
        self,
        *,
        skill_text: str,
        user_request: str,
        context: Mapping[str, Any],
        structured_output: bool = False,
    ) -> str:
        bundle = context.get("context_bundle") if isinstance(context.get("context_bundle"), Mapping) else {}
        bundle_dir = _trim(bundle.get("bundle_dir"))
        output_instruction = (
            ""
            if structured_output
            else (
                "只输出该 SKILL 要求的 NDJSON 事件；不要修改工作区文件。\n"
                "最后一行必须是 source=model,type=final 的 JSON 对象。\n"
            )
        )
        return (
            "请严格按照下面的 SKILL 执行任务。\n"
            "不要修改工作区文件。\n"
            f"{output_instruction}\n"
            "# AVAILABLE FILE CONTEXT\n"
            f"资料包目录：{bundle_dir}\n"
            "如果任务需要金融数据工具能力，先读取资料包中的 api_catalog/index.json；再只读取相关 subject 文件。\n"
            "如果任务需要在生成代码中使用金融数据或搜索能力，参考 custom_tool_sdk.md。\n\n"
            "# SKILL\n"
            f"{skill_text}\n\n"
            "# CONTEXT\n"
            f"{_json_text(dict(context))}\n\n"
            "# USER REQUEST\n"
            f"{user_request}\n"
        )

    def _resolve_output_schema_file(self, *, skill_file: Path, output_schema_path: str = "") -> Optional[Path]:
        explicit_path = _trim(output_schema_path)
        if explicit_path:
            schema_file = Path(explicit_path)
            if not schema_file.is_absolute():
                schema_file = self.cwd / schema_file
            if not schema_file.exists():
                raise FileNotFoundError(f"output schema file not found: {schema_file}")
            self._load_output_schema(schema_file)
            return schema_file

        for filename in ("schema.json", "output_schema.json"):
            candidate = skill_file.parent / filename
            if candidate.exists():
                self._load_output_schema(candidate)
                return candidate
        return None

    @staticmethod
    def _load_output_schema(schema_file: Path) -> Dict[str, Any]:
        try:
            payload = json.loads(schema_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid output schema JSON: {schema_file}: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise ValueError(f"output schema must be a JSON object: {schema_file}")
        return dict(payload)

    @staticmethod
    def _infer_stage(skill_file: Path) -> str:
        name = skill_file.name.lower()
        if "coding" in name:
            return "coding"
        return "design"

    def _normalize_codex_json_events(self, stdout: str) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        for line in str(stdout or "").splitlines():
            raw = line.strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except Exception:
                continue
            events.extend(self._normalize_one_codex_event(payload))
            events.extend(self._extract_model_events_from_obj(payload))
        return events

    def _normalize_one_codex_event(self, payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
        event_type = _trim(payload.get("type") or payload.get("event") or payload.get("kind"))
        text = self._event_text(payload)
        lower_type = event_type.lower()
        if "tool_call" in lower_type or "function_call" in lower_type or "exec_command" in lower_type:
            return [{
                "source": "harness",
                "type": "tool_call",
                "content": text or event_type,
                "metadata": {"raw_type": event_type},
            }]
        if "tool_result" in lower_type or "function_call_output" in lower_type or "command_output" in lower_type:
            return [{
                "source": "harness",
                "type": "tool_result",
                "content": text or event_type,
                "metadata": {"raw_type": event_type},
            }]
        if text:
            return [{
                "source": "codex",
                "type": "agent_update",
                "content": text,
                "metadata": {"raw_type": event_type},
            }]
        return []

    def _extract_model_events_from_obj(self, payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
        texts: List[str] = []
        for key in ("message", "content", "text", "delta", "output", "last_message"):
            value = payload.get(key)
            if isinstance(value, str):
                texts.append(value)
        item = payload.get("item")
        if isinstance(item, Mapping):
            for key in ("content", "text", "message"):
                value = item.get(key)
                if isinstance(value, str):
                    texts.append(value)
        events: List[Dict[str, Any]] = []
        for text in texts:
            events.extend(self._extract_model_events(text))
        return events

    def _extract_model_events(self, text: str) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        for line in str(text or "").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("```"):
                continue
            if not (stripped.startswith("{") and stripped.endswith("}")):
                continue
            try:
                payload = json.loads(stripped)
            except Exception:
                continue
            if _trim(payload.get("source")) and _trim(payload.get("type")):
                events.append(dict(payload))
        return events

    @staticmethod
    def _find_final(events: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
        final: Dict[str, Any] = {}
        for event in events:
            if _trim(event.get("source")) == "model" and _trim(event.get("type")) == "final":
                final = dict(event)
        return final

    @staticmethod
    def _final_from_text(text: str) -> Dict[str, Any]:
        stripped = _trim(text)
        if not stripped:
            return {}
        try:
            payload = json.loads(stripped)
        except Exception:
            return {}
        if not isinstance(payload, Mapping):
            return {}
        final = dict(payload)
        final.setdefault("source", "model")
        final.setdefault("type", "final")
        return final if _trim(final.get("source")) == "model" and _trim(final.get("type")) == "final" else {}

    @staticmethod
    def _event_text(payload: Mapping[str, Any]) -> str:
        for key in ("message", "content", "text", "delta"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        item = payload.get("item")
        if isinstance(item, Mapping):
            for key in ("message", "content", "text"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""


class CodexSdkSkillHarness(CodexExecSkillHarness):
    """SDK-backed Codex runner with stream events and structured final output."""

    def __init__(
        self,
        *,
        cwd: str = ".",
        timeout_seconds: int = 180,
        hard_timeout_seconds: int = 0,
        model: str = "",
        sandbox: str = "workspace-write",
        auth_mode: str = "",
        context_bundle_service: Optional[CustomToolContextBundleService] = None,
    ) -> None:
        super().__init__(
            codex_bin="codex",
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            hard_timeout_seconds=hard_timeout_seconds,
            model=model,
            sandbox=sandbox,
            context_bundle_service=context_bundle_service,
        )
        self.auth_mode = _trim(auth_mode or os.environ.get("STOCK_AGENT_CODEX_AUTH_MODE") or "auto")

    def available(self) -> bool:
        try:
            import openai_codex  # noqa: F401
        except Exception:
            return False
        return True

    def run_skill(
        self,
        *,
        skill_path: str,
        output_schema_path: str = "",
        user_request: str,
        context: Optional[Mapping[str, Any]] = None,
        session_id: str = "",
        stage: str = "",
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        started_at = time.time()
        stage_name = stage or self._infer_stage(Path(skill_path))
        stage_events: List[Dict[str, Any]] = []
        if not self.available():
            return {
                "ok": False,
                "error": "`openai-codex` is not available",
                "events": [],
                "final": {},
                "session_id": session_id,
            }
        skill_file = Path(skill_path)
        if not skill_file.is_absolute():
            skill_file = self.cwd / skill_file
        if not skill_file.exists():
            return {
                "ok": False,
                "error": f"skill file not found: {skill_file}",
                "events": [],
                "final": {},
                "session_id": session_id,
            }
        try:
            output_schema_file = self._resolve_output_schema_file(
                skill_file=skill_file,
                output_schema_path=output_schema_path,
            )
            output_schema = (
                self._load_output_schema(output_schema_file)
                if output_schema_file is not None
                else self._output_schema(stage_name)
            )
        except (FileNotFoundError, ValueError) as exc:
            return {
                "ok": False,
                "error": str(exc),
                "events": [],
                "final": {},
                "session_id": session_id,
            }

        run_context = dict(context or {})
        self._append_event(
            stage_events,
            {
                "source": "harness",
                "type": "stage_start",
                "content": f"codex sdk skill stage started: {stage_name}",
                "metadata": {"stage": stage_name, "session_id": session_id},
            },
            event_sink,
        )
        bundle = self.context_bundle_service.build(
            stage=stage_name,
            user_request=user_request,
            context=run_context,
            run_id=session_id,
        )
        run_context["context_bundle"] = bundle
        self._append_event(
            stage_events,
            {
                "source": "harness",
                "type": "context_ready",
                "content": "context bundle prepared",
                "metadata": {"stage": stage_name, "bundle_dir": bundle.get("bundle_dir")},
            },
            event_sink,
        )
        prompt = self._build_sdk_prompt(
            skill_text=skill_file.read_text(encoding="utf-8"),
            user_request=user_request,
            context=run_context,
            stage=stage_name,
        )
        events = list(stage_events)
        final: Dict[str, Any] = {}
        final_response = ""
        completed_texts: List[str] = []
        agent_deltas: List[str] = []
        error = ""
        timeout = False
        timeout_kind = ""
        timeout_after_seconds = 0

        try:
            from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox
            from openai_codex.types import Personality, ReasoningSummary

            sdk_sandbox = self._sdk_sandbox(Sandbox)
            sdk_env = self._sdk_env()
            codex_bin = shutil.which(self.codex_bin)
            config = CodexConfig(
                codex_bin=codex_bin,
                cwd=str(self.cwd),
                env=sdk_env or None,
            )
            self._append_event(
                events,
                {
                    "source": "harness",
                    "type": "tool_call",
                    "content": "codex sdk turn started",
                    "metadata": {
                        "stage": stage_name,
                        "sandbox": self.sandbox,
                        "idle_timeout_seconds": self.timeout_seconds,
                        "hard_timeout_seconds": self.hard_timeout_seconds,
                        "codex_bin": codex_bin or "<sdk-pinned>",
                        "output_schema_path": str(output_schema_file or "<built-in>"),
                    },
                },
                event_sink,
            )
            with Codex(config=config) as codex:
                self._apply_auth(codex)
                thread = codex.thread_start(
                    approval_mode=ApprovalMode.deny_all,
                    cwd=str(self.cwd),
                    model=self.model or None,
                    sandbox=sdk_sandbox,
                )
                turn = thread.turn(
                    prompt,
                    approval_mode=ApprovalMode.deny_all,
                    model=self.model or None,
                    output_schema=output_schema,
                    personality=Personality.pragmatic,
                    sandbox=sdk_sandbox,
                    summary=ReasoningSummary.model_validate("concise"),
                )
                for notification in turn.stream():
                    if time.time() - started_at > self.hard_timeout_seconds:
                        timeout = True
                        timeout_kind = "hard timeout"
                        timeout_after_seconds = self.hard_timeout_seconds
                        try:
                            turn.interrupt()
                        except Exception:
                            pass
                        timeout_event = {
                            "source": "harness",
                            "type": "tool_result",
                            "content": "codex sdk hard timeout",
                            "metadata": {"stage": stage_name, "status": "error", "timeout_kind": timeout_kind, "duration_ms": int((time.time() - started_at) * 1000)},
                        }
                        events.append(timeout_event)
                        self._send_event(timeout_event, event_sink)
                        break
                    new_events = self._normalize_sdk_notification(notification)
                    method = _trim(getattr(notification, "method", ""))
                    payload = getattr(notification, "payload", None)
                    if method == "item/agentMessage/delta":
                        delta = _trim(getattr(payload, "delta", ""))
                        if delta:
                            agent_deltas.append(delta)
                    if method == "item/completed":
                        text = self._completed_item_text(payload)
                        if text:
                            completed_texts.append(text)
                    if method == "error":
                        error = self._sdk_error_text(payload) or error
                    if method == "turn/completed":
                        error = self._sdk_turn_error_text(payload) or error
                    events.extend(new_events)
                    for event in new_events:
                        self._send_event(event, event_sink)
                    if method == "turn/completed":
                        break
                final_response = _trim(completed_texts[-1] if completed_texts else "".join(agent_deltas))
        except Exception as exc:
            error = str(exc)

        events.extend(self._extract_model_events(final_response))
        final = self._final_from_text(final_response) or self._find_final(events)
        if final and not self._find_final(events):
            self._append_event(
                events,
                {**final, "metadata": {"stage": stage_name}},
                event_sink,
            )
        ok = bool(final) and not error and not timeout
        self._append_event(
            events,
            {
                "source": "harness",
                "type": "stage_result",
                "content": "codex sdk skill stage parsed",
                "metadata": {
                    "stage": stage_name,
                    "ok": ok,
                    "final_status": final.get("status"),
                    "timeout": timeout,
                    "duration_ms": int((time.time() - started_at) * 1000),
                },
            },
            event_sink,
        )
        return {
            "ok": ok,
            "error": error or (f"codex sdk {timeout_kind or 'timeout'} after {timeout_after_seconds or self.hard_timeout_seconds}s" if timeout else ""),
            "events": events,
            "final": final,
            "session_id": session_id,
            "raw_stdout": "",
            "raw_stderr": error,
            "last_message": final_response,
            "context_bundle": bundle,
            "duration_ms": int((time.time() - started_at) * 1000),
        }

    def _build_sdk_prompt(self, *, skill_text: str, user_request: str, context: Mapping[str, Any], stage: str) -> str:
        return self._build_prompt(
            skill_text=skill_text,
            user_request=user_request,
            context=context,
            structured_output=True,
        )

    @staticmethod
    def _render_block_schema() -> Dict[str, Any]:
        return {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "block_id": {"type": "string"},
                    "block_type": {"type": "string", "enum": ["markdown", "table", "bar_chart", "line_chart", "flowchart", "code", "action"]},
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                    "data_json": {"type": "string"},
                    "actions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "label": {"type": "string"},
                                "command": {"type": "string"},
                            },
                            "required": ["id", "label", "command"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["block_id", "block_type", "title", "content", "data_json", "actions"],
                "additionalProperties": False,
            },
        }

    def _sdk_env(self) -> Dict[str, str]:
        if self.auth_mode != "access_token":
            return {}
        token = _trim(os.environ.get("CODEX_ACCESS_TOKEN"))
        return {"CODEX_ACCESS_TOKEN": token} if token else {}

    def _apply_auth(self, codex: Any) -> None:
        if self.auth_mode != "api_key":
            return
        api_key = _trim(os.environ.get("CODEX_API_KEY") or os.environ.get("OPENAI_API_KEY"))
        if api_key:
            codex.login_api_key(api_key)

    def _sdk_sandbox(self, sandbox_cls: Any) -> Any:
        value = self.sandbox
        if value in {"read-only", "read_only"}:
            return sandbox_cls.read_only
        if value in {"danger-full-access", "full-access", "full_access"}:
            return sandbox_cls.full_access
        return sandbox_cls.workspace_write

    def _normalize_sdk_notification(self, notification: Any) -> List[Dict[str, Any]]:
        method = _trim(getattr(notification, "method", ""))
        payload = getattr(notification, "payload", None)
        if method == "turn/started":
            return [{"source": "harness", "type": "turn_started", "content": "turn started", "metadata": self._payload_json(payload)}]
        if method == "turn/completed":
            return [{"source": "harness", "type": "turn_completed", "content": "turn completed", "metadata": self._payload_json(payload)}]
        if method == "item/agentMessage/delta":
            return self._text_event("codex", "agent_delta", getattr(payload, "delta", ""))
        if method == "item/reasoning/summaryTextDelta":
            return self._text_event("codex", "reasoning_summary_delta", getattr(payload, "delta", ""))
        if method == "item/reasoning/textDelta":
            return self._text_event("codex", "reasoning_delta", getattr(payload, "delta", ""))
        if method == "item/plan/delta":
            return self._text_event("codex", "plan_delta", getattr(payload, "delta", ""))
        if method in {"item/commandExecution/outputDelta", "command/exec/outputDelta", "process/outputDelta"}:
            return self._text_event("tool", "command_output", getattr(payload, "delta", "") or getattr(payload, "chunk", ""))
        if method == "item/mcpToolCall/progress":
            return [{"source": "tool", "type": "mcp_progress", "content": "", "metadata": self._payload_json(payload)}]
        if method == "item/completed":
            text = self._completed_item_text(payload)
            events = self._extract_model_events(text)
            if events:
                return events
            return [{"source": "codex", "type": "item_completed", "content": text, "metadata": {"method": method}}] if text else []
        return [{"source": "codex", "type": "event", "content": method, "metadata": self._payload_json(payload)}] if method else []

    @staticmethod
    def _text_event(source: str, event_type: str, content: Any) -> List[Dict[str, Any]]:
        text = _trim(content)
        return [{"source": source, "type": event_type, "content": text}] if text else []

    @staticmethod
    def _payload_json(payload: Any) -> Dict[str, Any]:
        if payload is None:
            return {}
        if hasattr(payload, "model_dump"):
            try:
                return payload.model_dump(mode="json", by_alias=True)
            except Exception:
                return {}
        if isinstance(payload, Mapping):
            return dict(payload)
        return {}

    @staticmethod
    def _completed_item_text(payload: Any) -> str:
        try:
            item = getattr(payload, "item", None)
            root = getattr(item, "root", None)
            return _trim(getattr(root, "text", ""))
        except Exception:
            return ""

    @staticmethod
    def _sdk_error_text(payload: Any) -> str:
        data = CodexSdkSkillHarness._payload_json(payload)
        error = data.get("error") if isinstance(data.get("error"), Mapping) else {}
        return _trim(error.get("message") if isinstance(error, Mapping) else "")

    @staticmethod
    def _sdk_turn_error_text(payload: Any) -> str:
        data = CodexSdkSkillHarness._payload_json(payload)
        turn = data.get("turn") if isinstance(data.get("turn"), Mapping) else {}
        error = turn.get("error") if isinstance(turn.get("error"), Mapping) else {}
        return _trim(error.get("message") if isinstance(error, Mapping) else "")

    @staticmethod
    def _output_schema(stage: str) -> Dict[str, Any]:
        if stage == "coding":
            return {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "enum": ["model"]},
                    "type": {"type": "string", "enum": ["final"]},
                    "status": {"type": "string", "enum": ["code_ready", "need_design_fix"]},
                    "message": {"type": "string"},
                    "code_summary": {"type": "string"},
                    "files": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "role": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "role", "content"],
                            "additionalProperties": False,
                        },
                    },
                    "tests": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "status": {"type": "string"},
                                "input": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
                                "expected": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
                                "actual": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
                                "summary": {"type": "string"},
                            },
                            "required": ["name", "status", "input", "expected", "actual", "summary"],
                            "additionalProperties": False,
                        },
                    },
                    "implementation_notes": {"type": "array", "items": {"type": "string"}},
                    "need_design_fix": {"type": "string"},
                    "risks": {"type": "array", "items": {"type": "string"}},
                    "sample_input_json": {"type": "string"},
                    "render_blocks": CodexSdkSkillHarness._render_block_schema(),
                },
                "required": ["source", "type", "status", "message", "code_summary", "files", "tests", "implementation_notes", "need_design_fix", "risks", "sample_input_json", "render_blocks"],
                "additionalProperties": False,
            }
        field_schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "type": {"type": "string"},
                "required": {"type": "boolean"},
                "description": {"type": "string"},
            },
            "required": ["name", "type", "required", "description"],
            "additionalProperties": False,
        }
        output_field_schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "type": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["name", "type", "description"],
            "additionalProperties": False,
        }
        return {
            "type": "object",
            "properties": {
                "source": {"type": "string", "enum": ["model"]},
                "type": {"type": "string", "enum": ["final"]},
                "status": {"type": "string", "enum": ["need_more_info", "design_ready"]},
                "message": {"type": "string"},
                "questions": {"type": "array", "items": {"type": "string"}},
                "design": {
                    "type": "object",
                    "properties": {
                        "tool_name": {"type": "string"},
                        "display_name": {"type": "string"},
                        "description": {"type": "string"},
                        "inputs": {"type": "array", "items": field_schema},
                        "outputs": {"type": "array", "items": output_field_schema},
                        "logic": {"type": "array", "items": {"type": "string"}},
                        "diagram": {"type": "string"},
                    },
                    "required": ["tool_name", "display_name", "description", "inputs", "outputs", "logic", "diagram"],
                    "additionalProperties": False,
                },
                "render_blocks": CodexSdkSkillHarness._render_block_schema(),
            },
            "required": ["source", "type", "status", "message", "questions", "design", "render_blocks"],
            "additionalProperties": False,
        }


class CodexCustomToolDesigner:
    def __init__(
        self,
        *,
        harness: Optional[CodexExecSkillHarness] = None,
        skill_path: str = "src/skills/financial-tool-requirement-design-v3/SKILL.md",
        output_schema_path: str = "src/skills/financial-tool-requirement-design-v3/schema.json",
    ) -> None:
        self.harness = harness or CodexExecSkillHarness(cwd=".")
        self.skill_path = skill_path
        self.output_schema_path = output_schema_path

    def design(
        self,
        requirement_text: str,
        *,
        context: Optional[Mapping[str, Any]] = None,
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        result = self.harness.run_skill(
            skill_path=self.skill_path,
            output_schema_path=self.output_schema_path,
            user_request=requirement_text,
            context=context or {},
            stage="design",
            event_sink=event_sink,
        )
        if not result.get("ok"):
            return {
                "status": "need_more_info",
                "message": result.get("error") or "Codex design skill 未能生成最终设计。",
                "events": result.get("events") or [],
                "raw": result,
            }
        final = dict(result.get("final") or {})
        status = _trim(final.get("status")) or "clarification"
        understanding = final.get("understanding") if isinstance(final.get("understanding"), Mapping) else {}
        questions = [dict(item) if isinstance(item, Mapping) else str(item) for item in final.get("questions") or []]
        has_required_question = any(
            isinstance(item, Mapping) and item.get("required") is True
            for item in questions
        )
        if status == "review" and has_required_question:
            status = "clarification"
        goal = _trim(understanding.get("goal"))
        return {
            "status": status,
            "message": (f"{goal}的设计已形成，请检查后确认。" if goal else "设计已形成，请检查后确认。") if status == "review" else "还需要确认几个会影响设计的关键条件。",
            "understanding": dict(understanding),
            "questions": questions[:5],
            "design": final.get("design") if isinstance(final.get("design"), Mapping) else {},
            "existing_analysis": dict(final.get("existing_analysis")) if isinstance(final.get("existing_analysis"), Mapping) else {},
            "events": result.get("events") or [],
            "raw": result,
        }


class CodexCustomToolCoder:
    def __init__(self, *, harness: Optional[CodexExecSkillHarness] = None, skill_path: str = "SKILL_requirement_coding.md") -> None:
        self.harness = harness or CodexExecSkillHarness(cwd=".")
        self.skill_path = skill_path

    def code(
        self,
        design: Mapping[str, Any],
        *,
        requirement_text: str = "",
        context: Optional[Mapping[str, Any]] = None,
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        request = (
            "请根据已确认的自定义工具设计稿生成代码。\n"
            f"原始需求：{requirement_text}\n"
            f"设计稿：{_json_text(dict(design))}"
        )
        run_context = dict(context or {})
        run_context["design"] = dict(design)
        result = self.harness.run_skill(
            skill_path=self.skill_path,
            user_request=request,
            context=run_context,
            stage="coding",
            event_sink=event_sink,
        )
        if not result.get("ok"):
            return {
                "status": "need_design_fix",
                "message": result.get("error") or "Codex coding skill 未能生成最终代码。",
                "events": result.get("events") or [],
                "raw": result,
            }
        final = dict(result.get("final") or {})
        status = _trim(final.get("status")) or "need_design_fix"
        return {
            "status": status,
            "message": _trim(final.get("message")) or ("代码已生成。" if status == "code_ready" else "需要回到设计阶段确认。"),
            "final": final,
            "events": result.get("events") or [],
            "raw": result,
        }
