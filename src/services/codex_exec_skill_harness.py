from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import selectors
import signal
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from src.services.agent_providers.protocol import AgentEventCoalescer, normalize_agent_run_result
from src.services.agent_providers.runtime_policy import AgentCapabilityPolicy, WebSearchPolicy
from src.services.custom_tool_context_bundle_service import CustomToolContextBundleService


_SKILL_EXECUTION_DEVELOPER_INSTRUCTIONS = (
    "Execute only the Skill task supplied in the user prompt. The SKILL.md content is already present; do not read it "
    "again. Do not inspect AGENTS.md, memory files, unrelated skills, or unrelated repository files. Read only the "
    "playbooks and references explicitly selected by the Skill, plus the minimum required context-bundle files. "
    "If the Skill routes to a playbook, load that playbook before answering; never skip that required playbook read. "
    "Do not modify workspace files."
)

_CODING_WORKSPACE_DEVELOPER_INSTRUCTIONS = (
    "Execute only the Skill task supplied in the user prompt. The SKILL.md content is already present; do not read it "
    "again. Do not inspect AGENTS.md, memory files, unrelated skills, or unrelated repository files. Read only the "
    "task, design, feedback, implementation, API catalog, and Skill reference files explicitly supplied in the "
    "context bundle. You may edit only the module files listed in CONTEXT.current_implementation.module_files, "
    "and may create temporary focused tests only under scratch/. Do not modify any other file."
)

_FIN_AGENT_CODEX_CONFIG_MARKER = "# Managed by Fin Agent Codex runtime."


def _trim(value: Any) -> str:
    return str(value or "").strip()


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _requirement_understanding(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    brief = _trim(value)
    return {"requirement_brief": brief} if brief else {}


def _is_coding_stage(value: Any) -> bool:
    return _trim(value) in {"coding", "edit_coding"}


class CodexExecSkillHarness:
    """Thin wrapper around `codex exec` for skill-driven custom-tool stages."""

    provider_name = "codex"

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
        public_bundle = self._public_bundle(bundle)
        prompt_context = self._prompt_context(bundle, run_context)
        prompt_context["context_bundle"] = public_bundle
        execution_cwd = self._execution_cwd(public_bundle)
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
            skill_root=str(skill_file.parent.resolve()),
            user_request=user_request,
            context=prompt_context,
            structured_output=output_schema_file is not None,
            stage=stage_name,
        )
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".txt", delete=False) as output_file:
            output_path = output_file.name
        schema_path = output_schema_file
        transient_schema_path = ""
        command = [
            self.codex_bin,
            "exec",
            "--json",
            "--cd",
            str(execution_cwd),
            "--sandbox",
            self.sandbox,
            "--output-last-message",
            output_path,
        ]
        if self.model:
            command.extend(["--model", self.model])
        if schema_path is not None:
            command.extend(["--output-schema", str(schema_path)])
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
        run_result = self._run_process(
            command,
            prompt,
            started_at=started_at,
            cwd=execution_cwd,
            event_sink=event_sink,
        )
        if transient_schema_path:
            try:
                os.unlink(transient_schema_path)
            except OSError:
                pass
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
                "context_bundle": public_bundle,
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
        if final and _is_coding_stage(stage_name):
            final = self._collect_coding_result(bundle, final)
        if final and not self._find_final(events):
            self._append_event(
                events,
                {**final, "metadata": {"stage": stage_name, "provider": self.provider_name}},
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
            "context_bundle": public_bundle,
            "duration_ms": int((time.time() - started_at) * 1000),
        }

    def _run_process(
        self,
        command: List[str],
        prompt: str,
        *,
        started_at: float,
        cwd: Optional[Path] = None,
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(cwd or self.cwd),
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
        skill_root: str = "",
        user_request: str,
        context: Mapping[str, Any],
        structured_output: bool = False,
        stage: str = "",
    ) -> str:
        bundle = context.get("context_bundle") if isinstance(context.get("context_bundle"), Mapping) else {}
        bundle_dir = _trim(bundle.get("bundle_dir"))
        coding_workspace = bundle.get("coding_workspace") if isinstance(bundle.get("coding_workspace"), Mapping) else {}
        editable_workspace = coding_workspace.get("editable") is True
        output_instruction = (
            ""
            if structured_output
            else (
                "只输出该 SKILL 要求的 NDJSON 事件；不要修改工作区文件。\n"
                "最后一行必须是 source=model,type=final 的 JSON 对象。\n"
            )
        )
        workspace_instruction = (
            "当前实现已放在隔离的临时 Coding 工作区。先用 rg/sed 按需定位相关函数、反馈和 API，"
            "只修改 CONTEXT.current_implementation.module_files 列出的模块文件。最终结构化输出必须反映修改后的内容；"
            "外层系统会从工作区回收源码并保存数据库 revision。\n"
            if editable_workspace
            else "不要修改工作区文件。\n"
        )
        skill_resources = ""
        if _trim(skill_root):
            skill_resources = (
                "# SKILL RESOURCES\n"
                f"Skill 目录：{_trim(skill_root)}\n"
                "SKILL 中的相对文件引用均从该目录解析；只读取当前任务明确需要的文件。\n\n"
            )
        file_context = ""
        if _trim(stage) == "design":
            file_context = (
                "如果 CONTEXT 提供 design_ref，只按需读取该设计资产；不要读取 API Catalog 或实现资料。\n"
            )
        elif _trim(stage) == "edit_plan":
            file_context = (
                "先按需读取 CONTEXT.design_ref 中的完整现有 Design，再与 existing_manifest 和 "
                "existing_schema 做语义比较；不要读取 API Catalog 或实现源码。\n"
            )
        elif _trim(stage) == "edit_coding":
            file_context = (
                "只读取 CONTEXT.design_ref、current_implementation.manifest_ref、列出的现有模块文件和 "
                "implementation_instruction；若存在 CONTEXT.api_dependency_ref，只有本次修改涉及数据读取时才读取。"
                "不要扫描完整 API Catalog 或无关资料。\n"
            )
        elif _trim(stage) == "coding":
            file_context = (
                "如果任务需要金融数据工具能力，先读取资料包中的 api_catalog/CODING_GUIDE.md 和 index.json；"
                "再通过 subject 索引只读取相关 dataview 文件。\n"
                "生成动态代码时参考 custom_tool_sdk.md。\n"
            )
        coding_api_contract = ""
        if _trim(stage) == "coding":
            contract_path = self.cwd / "src/prompts/codex/finance_api_call.system.md"
            if contract_path.is_file():
                coding_api_contract = (
                    "# REQUIRED FINANCE API CALL CONTRACT\n"
                    + contract_path.read_text(encoding="utf-8").strip()
                    + "\n\n"
                )
        return (
            "请严格按照下面的 SKILL 执行任务。\n"
            f"{workspace_instruction}"
            f"{output_instruction}\n"
            f"{coding_api_contract}"
            "# AVAILABLE FILE CONTEXT\n"
            f"资料包目录：{bundle_dir}\n"
            f"{file_context}\n"
            "# SKILL\n"
            f"{skill_text}\n\n"
            f"{skill_resources}"
            "# CONTEXT\n"
            f"{_json_text(dict(context))}\n\n"
            "# USER REQUEST\n"
            f"{user_request}\n"
        )

    def _execution_cwd(self, bundle: Mapping[str, Any]) -> Path:
        workspace = bundle.get("coding_workspace") if isinstance(bundle.get("coding_workspace"), Mapping) else {}
        bundle_dir = _trim(bundle.get("bundle_dir"))
        if workspace and bundle_dir:
            return Path(bundle_dir)
        return self.cwd

    def _public_bundle(self, bundle: Mapping[str, Any]) -> Dict[str, Any]:
        method = getattr(self.context_bundle_service, "public_bundle", None)
        return dict(method(bundle)) if callable(method) else dict(bundle)

    def _prompt_context(self, bundle: Mapping[str, Any], fallback: Mapping[str, Any]) -> Dict[str, Any]:
        method = getattr(self.context_bundle_service, "prompt_context", None)
        return dict(method(bundle, fallback)) if callable(method) else dict(fallback)

    def _collect_coding_result(self, bundle: Mapping[str, Any], final: Mapping[str, Any]) -> Dict[str, Any]:
        method = getattr(self.context_bundle_service, "collect_coding_result", None)
        return dict(method(bundle, final)) if callable(method) else dict(final)

    @staticmethod
    def _developer_instructions(bundle: Mapping[str, Any]) -> str:
        workspace = bundle.get("coding_workspace") if isinstance(bundle.get("coding_workspace"), Mapping) else {}
        return (
            _CODING_WORKSPACE_DEVELOPER_INSTRUCTIONS
            if workspace.get("editable") is True
            else _SKILL_EXECUTION_DEVELOPER_INSTRUCTIONS
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
            return schema_file.resolve()

        for filename in ("schema.json", "output_schema.json"):
            candidate = skill_file.parent / filename
            if candidate.exists():
                self._load_output_schema(candidate)
                return candidate.resolve()
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

    provider_name = "codex"

    def __init__(
        self,
        *,
        cwd: str = ".",
        timeout_seconds: int = 180,
        hard_timeout_seconds: int = 0,
        model: str = "",
        reasoning_effort: str = "medium",
        sandbox: str = "workspace-write",
        auth_mode: str = "",
        complexity_level: str = "",
        capabilities: Optional[AgentCapabilityPolicy] = None,
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
        self.reasoning_effort = _trim(reasoning_effort) or "medium"
        self.complexity_level = _trim(complexity_level).lower()
        self.capabilities = capabilities

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
            return normalize_agent_run_result({
                "ok": False,
                "error": "`openai-codex` is not available",
                "events": [],
                "final": {},
                "session_id": session_id,
            }, provider=self.provider_name, stage=stage_name, session_id=session_id)
        skill_file = Path(skill_path)
        if not skill_file.is_absolute():
            skill_file = self.cwd / skill_file
        if not skill_file.exists():
            return normalize_agent_run_result({
                "ok": False,
                "error": f"skill file not found: {skill_file}",
                "events": [],
                "final": {},
                "session_id": session_id,
            }, provider=self.provider_name, stage=stage_name, session_id=session_id)
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
            return normalize_agent_run_result({
                "ok": False,
                "error": str(exc),
                "events": [],
                "final": {},
                "session_id": session_id,
            }, provider=self.provider_name, stage=stage_name, session_id=session_id)

        run_context = dict(context or {})
        provider_session_id = _trim(run_context.pop("_provider_session_id", ""))
        self._append_event(
            stage_events,
            {
                "source": "harness",
                "type": "stage_start",
                "content": f"codex sdk skill stage started: {stage_name}",
                "metadata": {"stage": stage_name, "session_id": session_id, "provider": self.provider_name},
            },
            event_sink,
        )
        bundle = self.context_bundle_service.build(
            stage=stage_name,
            user_request=user_request,
            context=run_context,
            run_id=session_id,
        )
        public_bundle = self._public_bundle(bundle)
        prompt_context = self._prompt_context(bundle, run_context)
        prompt_context["context_bundle"] = public_bundle
        execution_cwd = self._execution_cwd(public_bundle)
        self._append_event(
            stage_events,
            {
                "source": "harness",
                "type": "context_ready",
                "content": "context bundle prepared",
                "metadata": {
                    "stage": stage_name,
                    "bundle_dir": bundle.get("bundle_dir"),
                    "provider": self.provider_name,
                    "api_sources": list(public_bundle.get("api_sources") or []),
                    "module_plan": list(
                        (public_bundle.get("coding_workspace") or {}).get("module_plan_items") or []
                    ),
                    "first_implementation": bool(
                        (public_bundle.get("coding_workspace") or {}).get("first_implementation")
                    ),
                },
            },
            event_sink,
        )
        prompt = self._build_sdk_prompt(
            skill_text=skill_file.read_text(encoding="utf-8"),
            skill_root=str(skill_file.parent.resolve()),
            user_request=user_request,
            context=prompt_context,
            stage=stage_name,
        )
        events = list(stage_events)
        final: Dict[str, Any] = {}
        final_response = ""
        completed_texts: List[str] = []
        agent_deltas: List[str] = []
        error = ""
        transient_errors: List[str] = []
        timeout = False
        timeout_kind = ""
        timeout_after_seconds = 0
        isolated_home: Optional[tempfile.TemporaryDirectory[str]] = None
        active_provider_session_id = provider_session_id
        event_coalescer = AgentEventCoalescer()

        try:
            from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox, SkillInput, TextInput
            from openai_codex.types import Personality, ReasoningEffort, ReasoningSummary

            sdk_sandbox = self._sdk_sandbox(Sandbox)
            sdk_env, isolated_home = self._sdk_runtime_env(session_id=session_id)
            runtime_model = self._runtime_model()
            runtime_reasoning_effort = self._runtime_reasoning_effort()
            runtime_auth_mode = self._resolved_auth_mode()
            codex_bin = shutil.which(self.codex_bin)
            config = CodexConfig(
                codex_bin=codex_bin,
                config_overrides=self._codex_config_overrides(),
                cwd=str(execution_cwd),
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
                        "complexity": self.complexity_level,
                        "model": runtime_model,
                        "reasoning_effort": runtime_reasoning_effort,
                        "auth_mode": runtime_auth_mode,
                        "resumed": bool(provider_session_id),
                        "web_search": self.capabilities.web_search.value if self.capabilities else "inherited",
                        "mcp_server_count": len(self.capabilities.mcp_servers) if self.capabilities else -1,
                    },
                },
                event_sink,
            )
            with Codex(config=config) as codex:
                self._apply_auth(codex)
                if provider_session_id:
                    thread = codex.thread_resume(
                        provider_session_id,
                        approval_mode=ApprovalMode.deny_all,
                        cwd=str(execution_cwd),
                        model=runtime_model or None,
                        sandbox=sdk_sandbox,
                    )
                else:
                    thread = codex.thread_start(
                        approval_mode=ApprovalMode.deny_all,
                        cwd=str(execution_cwd),
                        developer_instructions=self._developer_instructions(public_bundle),
                        model=runtime_model or None,
                        sandbox=sdk_sandbox,
                    )
                active_provider_session_id = _trim(getattr(thread, "id", "")) or provider_session_id
                skill_name = self._skill_name(skill_file)
                turn_input = (
                    [TextInput(text=self._build_sdk_resume_prompt(user_request=user_request, context=prompt_context))]
                    if provider_session_id
                    else [
                        SkillInput(name=skill_name, path=str(skill_file.resolve())),
                        TextInput(text=prompt),
                    ]
                )
                turn_options = {
                    "approval_mode": ApprovalMode.deny_all,
                    "effort": ReasoningEffort(runtime_reasoning_effort),
                    "model": runtime_model or None,
                    "output_schema": output_schema,
                    "personality": Personality.pragmatic,
                    "sandbox": sdk_sandbox,
                }
                if self._supports_reasoning_summary(runtime_model):
                    turn_options["summary"] = ReasoningSummary.model_validate("concise")
                turn = thread.turn(
                    turn_input,
                    **turn_options,
                )
                notification_queue: queue.Queue[tuple[str, Any]] = queue.Queue()

                def _pump_turn_notifications() -> None:
                    try:
                        for streamed_notification in turn.stream():
                            notification_queue.put(("notification", streamed_notification))
                    except BaseException as exc:  # surfaced on the controlling thread
                        notification_queue.put(("error", exc))
                    finally:
                        notification_queue.put(("done", None))

                stream_thread = threading.Thread(
                    target=_pump_turn_notifications,
                    name=f"codex-sdk-{stage_name}-stream",
                    daemon=True,
                )
                stream_thread.start()
                last_activity_at = time.time()
                while True:
                    now = time.time()
                    hard_remaining = self.hard_timeout_seconds - (now - started_at)
                    idle_remaining = self.timeout_seconds - (now - last_activity_at)
                    wait_seconds = max(0.01, min(hard_remaining, idle_remaining, 1.0))
                    try:
                        queue_kind, queue_value = notification_queue.get(timeout=wait_seconds)
                    except queue.Empty:
                        now = time.time()
                        if now - started_at >= self.hard_timeout_seconds:
                            timeout = True
                            timeout_kind = "hard timeout"
                            timeout_after_seconds = self.hard_timeout_seconds
                        elif now - last_activity_at >= self.timeout_seconds:
                            timeout = True
                            timeout_kind = "idle timeout"
                            timeout_after_seconds = self.timeout_seconds
                        if not timeout:
                            continue
                        try:
                            turn.interrupt()
                        except Exception:
                            pass
                        timeout_event = {
                            "source": "harness",
                            "type": "tool_result",
                            "content": f"codex sdk {timeout_kind}",
                            "metadata": {
                                "stage": stage_name,
                                "status": "error",
                                "timeout_kind": timeout_kind,
                                "timeout_after_seconds": timeout_after_seconds,
                                "duration_ms": int((now - started_at) * 1000),
                            },
                        }
                        events.append(timeout_event)
                        self._send_event(timeout_event, event_sink)
                        break
                    if queue_kind == "error":
                        raise queue_value
                    if queue_kind == "done":
                        break
                    notification = queue_value
                    last_activity_at = time.time()
                    new_events = self._attach_stage(
                        self._normalize_sdk_notification(notification),
                        stage_name,
                    )
                    method = _trim(getattr(notification, "method", ""))
                    payload = getattr(notification, "payload", None)
                    if method == "item/agentMessage/delta":
                        delta = str(getattr(payload, "delta", "") or "")
                        if delta:
                            agent_deltas.append(delta)
                    if method == "item/completed":
                        text = self._completed_item_text(payload)
                        if text:
                            completed_texts.append(text)
                    if method == "error":
                        transient_error = self._sdk_error_text(payload)
                        if transient_error:
                            transient_errors.append(transient_error)
                    if method == "turn/completed":
                        error = self._sdk_turn_error_text(payload) or error
                    for event in new_events:
                        for ready_event in event_coalescer.push(event):
                            events.append(ready_event)
                            self._send_event(ready_event, event_sink)
                    if method == "turn/completed":
                        break
                final_response = _trim(completed_texts[-1] if completed_texts else "".join(agent_deltas))
        except Exception as exc:
            error = str(exc)
        finally:
            for ready_event in event_coalescer.flush():
                events.append(ready_event)
                self._send_event(ready_event, event_sink)
            if isolated_home is not None:
                isolated_home.cleanup()

        events.extend(self._extract_model_events(final_response))
        final = self._final_from_text(final_response) or self._find_final(events)
        if final and _is_coding_stage(stage_name):
            final = self._collect_coding_result(bundle, final)
        if final and not self._find_final(events):
            self._append_event(
                events,
                {**final, "metadata": {"stage": stage_name, "provider": self.provider_name}},
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
                    "provider": self.provider_name,
                    "ok": ok,
                    "final_status": final.get("status"),
                    "timeout": timeout,
                    "duration_ms": int((time.time() - started_at) * 1000),
                },
            },
            event_sink,
        )
        return normalize_agent_run_result({
            "ok": ok,
            "error": error or (f"codex sdk {timeout_kind or 'timeout'} after {timeout_after_seconds or self.hard_timeout_seconds}s" if timeout else ""),
            "timeout": timeout,
            "timeout_kind": timeout_kind,
            "timeout_after_seconds": timeout_after_seconds,
            "events": events,
            "final": final,
            "session_id": session_id,
            "provider_session_id": active_provider_session_id,
            "raw_stdout": "",
            "raw_stderr": "\n".join(transient_errors + ([error] if error else [])),
            "last_message": final_response,
            "context_bundle": public_bundle,
            "duration_ms": int((time.time() - started_at) * 1000),
        }, provider=self.provider_name, stage=stage_name, session_id=session_id)

    def run_turn(
        self,
        *,
        prompt: str,
        developer_instructions: str,
        output_schema: Mapping[str, Any],
        stage: str,
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Run one structured Codex turn without loading a Skill."""
        started_at = time.time()
        events: List[Dict[str, Any]] = []
        if not self.available():
            return normalize_agent_run_result(
                {"ok": False, "error": "`openai-codex` is not available", "events": [], "final": {}},
                provider=self.provider_name,
                stage=stage,
            )
        self._append_event(
            events,
            {
                "source": "harness",
                "type": "stage_start",
                "content": f"codex direct turn started: {stage}",
                "metadata": {"stage": stage, "provider": self.provider_name},
            },
            event_sink,
        )
        final_response = ""
        error = ""
        usage: Dict[str, Any] = {}
        isolated_home: Optional[tempfile.TemporaryDirectory[str]] = None
        try:
            from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox
            from openai_codex.types import Personality, ReasoningEffort, ReasoningSummary

            sdk_sandbox = self._sdk_sandbox(Sandbox)
            codex_bin = shutil.which(self.codex_bin)
            sdk_env, isolated_home = self._sdk_runtime_env()
            runtime_model = self._runtime_model()
            runtime_reasoning_effort = self._runtime_reasoning_effort()
            runtime_auth_mode = self._resolved_auth_mode()
            config = CodexConfig(
                codex_bin=codex_bin,
                config_overrides=self._codex_config_overrides(),
                cwd=str(self.cwd),
                env=sdk_env or None,
            )
            self._append_event(
                events,
                {
                    "source": "harness",
                    "type": "tool_call",
                    "content": "codex sdk direct turn running",
                    "metadata": {
                        "stage": stage,
                        "model": runtime_model or "default",
                        "reasoning_effort": runtime_reasoning_effort,
                        "auth_mode": runtime_auth_mode,
                        "sandbox": self.sandbox,
                        "complexity": self.complexity_level,
                        "web_search": self.capabilities.web_search.value if self.capabilities else "inherited",
                        "mcp_server_count": len(self.capabilities.mcp_servers) if self.capabilities else -1,
                    },
                },
                event_sink,
            )
            with Codex(config=config) as codex:
                self._apply_auth(codex)
                thread = codex.thread_start(
                    approval_mode=ApprovalMode.deny_all,
                    cwd=str(self.cwd),
                    developer_instructions=developer_instructions,
                    ephemeral=True,
                    model=runtime_model or None,
                    sandbox=sdk_sandbox,
                )
                turn_options = {
                    "approval_mode": ApprovalMode.deny_all,
                    "effort": ReasoningEffort(runtime_reasoning_effort),
                    "model": runtime_model or None,
                    "output_schema": dict(output_schema),
                    "personality": Personality.pragmatic,
                    "sandbox": sdk_sandbox,
                }
                if self._supports_reasoning_summary(runtime_model):
                    turn_options["summary"] = ReasoningSummary.model_validate("concise")
                turn = thread.turn(prompt, **turn_options)
                turn_result = turn.run()
                final_response = _trim(turn_result.final_response)
                if turn_result.error is not None:
                    error = _trim(getattr(turn_result.error, "message", "") or turn_result.error)
                if turn_result.usage is not None:
                    usage = self._payload_json(turn_result.usage)
        except Exception as exc:
            error = str(exc)
        finally:
            if isolated_home is not None:
                isolated_home.cleanup()

        final = self._final_from_text(final_response)
        ok = bool(final) and not error
        self._append_event(
            events,
            {
                "source": "harness",
                "type": "stage_result",
                "content": "codex direct turn completed" if ok else "codex direct turn failed",
                "metadata": {
                    "stage": stage,
                    "provider": self.provider_name,
                    "ok": ok,
                    "duration_ms": int((time.time() - started_at) * 1000),
                },
            },
            event_sink,
        )
        return normalize_agent_run_result({
            "ok": ok,
            "error": error or ("Codex did not return a structured result" if not final else ""),
            "events": events,
            "final": final,
            "last_message": final_response,
            "llm_usage": usage,
            "duration_ms": int((time.time() - started_at) * 1000),
        }, provider=self.provider_name, stage=stage)

    def _codex_config_overrides(self) -> tuple[str, ...]:
        """Translate explicit capability policy without mutating user config."""
        disabled_features = (
            "apps",
            "browser_use",
            "computer_use",
            "image_generation",
            "in_app_browser",
            "memories",
            "multi_agent",
            "plugins",
            "remote_plugin",
            "skill_mcp_dependency_install",
            "skill_search",
            "tool_suggest",
            "workspace_dependencies",
        )
        overrides = [f"features.{name}=false" for name in disabled_features]
        if self.capabilities is None:
            return tuple(overrides)
        web_search = {
            WebSearchPolicy.DISABLED: "disabled",
            WebSearchPolicy.PROVIDER_DEFAULT: "cached",
            WebSearchPolicy.LIVE: "live",
        }[self.capabilities.web_search]
        overrides.append(f"web_search={json.dumps(web_search)}")
        for name in sorted(self.capabilities.mcp_servers):
            overrides.append(f"mcp_servers.{name}.enabled=true")
            raw_config = dict(self.capabilities.mcp_servers[name])
            allowed_tools = raw_config.pop("allowed_tools", None)
            policy_tools = [
                tool_name.split("__", 2)[2]
                for tool_name in self.capabilities.mcp_allowed_tools
                if tool_name.startswith(f"mcp__{name}__")
            ]
            normalized_allowed_tools = list(dict.fromkeys(list(allowed_tools or ()) + policy_tools))
            if normalized_allowed_tools:
                raw_config["enabled_tools"] = normalized_allowed_tools
            raw_config.pop("type", None)
            allowed_keys = {
                "command",
                "args",
                "cwd",
                "url",
                "bearer_token_env_var",
                "env_vars",
                "env_http_headers",
                "startup_timeout_sec",
                "tool_timeout_sec",
                "enabled_tools",
                "disabled_tools",
                "required",
            }
            unsupported = sorted(set(raw_config) - allowed_keys)
            if unsupported:
                raise ValueError(f"unsupported Codex MCP config fields for {name}: {', '.join(unsupported)}")
            for key, value in sorted(raw_config.items()):
                overrides.append(f"mcp_servers.{name}.{key}={self._toml_value(value)}")
        return tuple(overrides)

    def _supports_reasoning_summary(self, model: str = "") -> bool:
        # The model capability is provider metadata, not a business routing
        # rule.  Spark rejects the parameter at request validation time.
        return _trim(model or self.model) != "gpt-5.3-codex-spark"

    def _resolved_auth_mode(self) -> str:
        """Resolve deployment auth without leaking it into business protocol."""
        configured = self.auth_mode.lower().replace("-", "_")
        aliases = {
            "chatgpt": "subscription",
            "crs": "crs_api_key",
            "server": "crs_api_key",
        }
        configured = aliases.get(configured, configured)
        if configured == "auto":
            return "crs_api_key" if _trim(os.environ.get("CODEX_CRS_API_KEY")) else "subscription"
        if configured not in {"subscription", "crs_api_key", "api_key", "access_token"}:
            raise ValueError(f"unsupported Codex auth mode: {configured or '-'}")
        return configured

    def _runtime_model(self) -> str:
        if self._resolved_auth_mode() == "crs_api_key":
            return _trim(os.environ.get("CODEX_CRS_MODEL")) or self.model or "gpt-5-codex"
        return self.model

    def _runtime_reasoning_effort(self) -> str:
        if self._resolved_auth_mode() == "crs_api_key":
            return _trim(os.environ.get("CODEX_CRS_REASONING_EFFORT")) or self.reasoning_effort
        return self.reasoning_effort

    def _crs_config_text(self) -> str:
        base_url = _trim(os.environ.get("CODEX_CRS_BASE_URL")) or "https://proxy.kingdomai.com/openai"
        return (
            f"{_FIN_AGENT_CODEX_CONFIG_MARKER}\n"
            'model_provider = "crs"\n'
            f"model = {self._toml_value(self._runtime_model())}\n"
            f"model_reasoning_effort = {self._toml_value(self._runtime_reasoning_effort())}\n\n"
            '[history]\n'
            'persistence = "none"\n\n'
            '[model_providers.crs]\n'
            'name = "crs"\n'
            f"base_url = {self._toml_value(base_url)}\n"
            'wire_api = "responses"\n'
            'env_key = "CODEX_CRS_API_KEY"\n'
        )

    @staticmethod
    def _remove_managed_config(config_file: Path) -> None:
        try:
            content = config_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        if content.startswith(_FIN_AGENT_CODEX_CONFIG_MARKER):
            config_file.unlink()

    def _sdk_runtime_env(
        self,
        *,
        session_id: str = "",
    ) -> tuple[Dict[str, str], Optional[tempfile.TemporaryDirectory[str]]]:
        """Isolate explicit capability runs from global Codex plugins and MCPs."""
        auth_mode = self._resolved_auth_mode()
        env = self._sdk_env()
        if self.capabilities is None and auth_mode != "crs_api_key":
            return env, None
        configured_home = _trim(os.environ.get("CODEX_HOME"))
        source_home = Path(configured_home).expanduser() if configured_home else Path.home() / ".codex"
        logical_session_id = _trim(session_id)
        isolated_home: Optional[tempfile.TemporaryDirectory[str]] = None
        if logical_session_id:
            session_root = Path(
                _trim(os.environ.get("CUSTOM_TOOL_AGENT_SESSION_ROOT")) or "data/agent_sessions"
            )
            session_key = hashlib.sha256(logical_session_id.encode("utf-8")).hexdigest()[:24]
            isolated_path = session_root / "codex" / session_key
            isolated_path.mkdir(parents=True, exist_ok=True)
            try:
                isolated_path.chmod(0o700)
            except OSError:
                pass
        else:
            isolated_home = tempfile.TemporaryDirectory(prefix="fin-agent-codex-")
            isolated_path = Path(isolated_home.name)
        config_file = isolated_path / "config.toml"
        if auth_mode == "crs_api_key":
            config_text = self._crs_config_text()
            if not config_file.exists() or config_file.read_text(encoding="utf-8") != config_text:
                config_file.write_text(config_text, encoding="utf-8")
                try:
                    config_file.chmod(0o600)
                except OSError:
                    pass
        else:
            self._remove_managed_config(config_file)

        auth_file = source_home / "auth.json"
        isolated_auth = isolated_path / "auth.json"
        if auth_mode != "subscription" and isolated_auth.is_symlink():
            isolated_auth.unlink()
        if auth_mode == "subscription" and auth_file.is_file() and not isolated_auth.exists():
            isolated_auth.symlink_to(auth_file.resolve())
        env["CODEX_HOME"] = str(isolated_path.resolve())
        return env, isolated_home

    @staticmethod
    def _build_sdk_resume_prompt(*, user_request: str, context: Mapping[str, Any]) -> str:
        """Continue a Coding thread with only the new contribution.

        The resumed thread already has the Skill, design, API references and
        workspace history.  Updated feedback and implementation files live in
        the same bundle directory.
        """
        feedback_refs = [
            _trim(context.get("test_feedback_ref")),
            _trim((context.get("current_implementation") or {}).get("last_test_ref"))
            if isinstance(context.get("current_implementation"), Mapping)
            else "",
        ]
        available_refs = [item for item in feedback_refs if item]
        reference_text = f"最新真实反馈：{', '.join(available_refs)}。" if available_refs else ""
        coding_feedback = _trim(context.get("coding_feedback"))
        current_request = coding_feedback or user_request
        return (
            "继续当前金融工具 Coding 会话。保持未受影响的实现不变，只处理本轮新增要求或真实运行反馈。"
            f"{reference_text}\n本轮要求：{current_request}"
        )

    @staticmethod
    def _toml_value(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        if isinstance(value, str):
            return json.dumps(value)
        if isinstance(value, (list, tuple)):
            return "[" + ",".join(CodexSdkSkillHarness._toml_value(item) for item in value) + "]"
        if isinstance(value, Mapping):
            entries = []
            for key, item in sorted(value.items()):
                normalized_key = _trim(key)
                if not re.fullmatch(r"[A-Za-z0-9_-]+", normalized_key):
                    raise ValueError(f"invalid TOML key: {normalized_key or '-'}")
                entries.append(f"{normalized_key}={CodexSdkSkillHarness._toml_value(item)}")
            return "{" + ",".join(entries) + "}"
        raise ValueError(f"unsupported TOML value type: {type(value).__name__}")

    def _build_sdk_prompt(
        self,
        *,
        skill_text: str,
        skill_root: str = "",
        user_request: str,
        context: Mapping[str, Any],
        stage: str,
    ) -> str:
        bundle = context.get("context_bundle") if isinstance(context.get("context_bundle"), Mapping) else {}
        stage_guidance = ""
        if _trim(stage) == "coding":
            stage_guidance = (
                "按 CODING_WORKSPACE.md 使用当前工作区；首次实现参考 DYNAMIC_TOOL_TEMPLATE.py。"
                "按需读取需求、Design、反馈和 API Catalog，将源码写入指定模块文件。\n"
            )
        elif _trim(stage) == "edit_plan":
            stage_guidance = (
                "先读取 CONTEXT.design_ref 中的完整现有 Design，再与 existing_manifest 和 "
                "existing_schema 做语义比较；只形成一次性 EditPlan，不读取实现源码。\n"
            )
        elif _trim(stage) == "edit_coding":
            stage_guidance = (
                "按 CODING_WORKSPACE.md 只修改 current_implementation.module_files 中与 "
                "implementation_instruction 直接相关的现有代码；涉及已有金融数据调用时按 "
                "api_dependency_ref 的窄契约修改，不扫描完整 API Catalog，不重做 Design，"
                "用本地合成正反例验证。\n"
            )
        return (
            "执行随本轮输入启用的 Skill。\n"
            f"资料包目录：{_trim(bundle.get('bundle_dir'))}\n"
            "按需使用 rg/sed 读取 CONTEXT 引用的资产和 API Catalog；不要展开无关文件。\n\n"
            f"{stage_guidance}\n"
            "# CONTEXT\n"
            f"{_json_text(dict(context))}\n\n"
            "# USER REQUEST\n"
            f"{user_request}\n"
        )

    @staticmethod
    def _skill_name(skill_file: Path) -> str:
        try:
            match = re.search(r"(?m)^name:\s*([^\n]+)$", skill_file.read_text(encoding="utf-8"))
        except OSError:
            match = None
        return _trim(match.group(1)) if match else skill_file.parent.name

    def _sdk_env(self) -> Dict[str, str]:
        auth_mode = self._resolved_auth_mode()
        if auth_mode == "access_token":
            token = _trim(os.environ.get("CODEX_ACCESS_TOKEN"))
            return {"CODEX_ACCESS_TOKEN": token} if token else {}
        if auth_mode == "crs_api_key":
            api_key = _trim(os.environ.get("CODEX_CRS_API_KEY"))
            if not api_key:
                raise RuntimeError("CODEX_CRS_API_KEY is required when Codex auth mode is crs_api_key")
            return {"CODEX_CRS_API_KEY": api_key}
        return {}

    def _apply_auth(self, codex: Any) -> None:
        if self._resolved_auth_mode() != "api_key":
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
            return self._raw_text_event(
                "codex",
                "agent_delta",
                getattr(payload, "delta", ""),
                metadata={
                    "item_id": _trim(getattr(payload, "item_id", "")),
                    "turn_id": _trim(getattr(payload, "turn_id", "")),
                },
            )
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
            item_metadata = self._completed_item_metadata(payload)
            if _trim(item_metadata.get("phase")) in {"commentary", "progress"}:
                try:
                    parsed = json.loads(text)
                except (TypeError, json.JSONDecodeError):
                    parsed = {}
                message = _trim(parsed.get("message")) if isinstance(parsed, Mapping) else ""
                return [{
                    "source": "codex",
                    "type": "item_completed",
                    "content": message or text,
                    "metadata": {"method": method, "item": item_metadata},
                }] if message or text else []
            events = self._extract_model_events(text)
            if events:
                return events
            return [{
                "source": "codex",
                "type": "item_completed",
                "content": text,
                "metadata": {"method": method, "item": item_metadata, **self._payload_json(payload)},
            }] if text else []
        return [{"source": "codex", "type": "event", "content": method, "metadata": self._payload_json(payload)}] if method else []

    @staticmethod
    def _attach_stage(events: List[Dict[str, Any]], stage: str) -> List[Dict[str, Any]]:
        for event in events:
            metadata = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else {}
            event["metadata"] = {**metadata, "stage": stage}
        return events

    @staticmethod
    def _text_event(source: str, event_type: str, content: Any) -> List[Dict[str, Any]]:
        text = _trim(content)
        return [{"source": source, "type": event_type, "content": text}] if text else []

    @staticmethod
    def _raw_text_event(
        source: str,
        event_type: str,
        content: Any,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> List[Dict[str, Any]]:
        """Keep structured-output deltas byte-for-byte for semantic assembly.

        The raw text is never sent to the UI.  It is consumed by
        ``LlmStreamBlockBuilder`` and only complete domain objects become
        Surface blocks.
        """
        text = str(content or "")
        event = {
            "source": source,
            "type": event_type,
            "content": text,
        }
        compact_metadata = {str(key): value for key, value in (metadata or {}).items() if _trim(value)}
        if compact_metadata:
            event["metadata"] = compact_metadata
        return [event] if text else []

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
    def _completed_item_metadata(payload: Any) -> Dict[str, Any]:
        try:
            item = getattr(payload, "item", None)
            root = getattr(item, "root", None)
            return {
                "id": _trim(getattr(root, "id", "")),
                "type": _trim(getattr(root, "type", "")),
                "phase": _trim(getattr(root, "phase", "")),
            }
        except Exception:
            return {}

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
        return {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
            "additionalProperties": False,
        }


class CodexCustomToolEditPlanner:
    """One-shot semantic router for editing an existing custom tool.

    The model decides whether the requested business change is local.  This
    wrapper only enforces invariants that must not be left to the model:
    public-contract edits never take the local path, and every Design change
    must be an exact replacement that applies once to the current asset.
    """

    _ASSET_ORDER = ("metadata", "design", "implementation", "contract")

    def __init__(
        self,
        *,
        harness: Optional[CodexExecSkillHarness] = None,
        skill_path: str = "src/skills/financial-tool-development/skills/financial-tool-edit-planning/SKILL.md",
        output_schema_path: str = "src/skills/financial-tool-development/skills/financial-tool-edit-planning/schema.json",
    ) -> None:
        self.harness = harness or CodexExecSkillHarness(cwd=".")
        self.skill_path = skill_path
        self.output_schema_path = output_schema_path

    def plan(
        self,
        user_request: str,
        *,
        manifest: Mapping[str, Any],
        design: Mapping[str, Any] | str,
        schema: Mapping[str, Any],
        context: Optional[Mapping[str, Any]] = None,
        run_id: str = "",
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        request = _trim(user_request)
        if not request:
            return self._failure("编辑要求不能为空。")
        if not isinstance(manifest, Mapping) or not isinstance(schema, Mapping):
            return self._failure("现有工具 manifest 或 Schema 无法读取。")
        design_asset = self._design_asset(design)
        if design_asset is None:
            return self._failure("现有工具 Design 无法读取。")

        run_context = dict(context or {})
        # These are system-resolved assets.  Caller-supplied context cannot
        # replace them with a different tool while the planner is running.
        run_context.update({
            "existing_manifest": dict(manifest),
            "current_design": design_asset,
            "existing_schema": dict(schema),
        })
        result = self.harness.run_skill(
            skill_path=self.skill_path,
            output_schema_path=self.output_schema_path,
            user_request=request,
            context=run_context,
            session_id=_trim(run_id),
            stage="edit_plan",
            event_sink=event_sink,
        )
        if not result.get("ok"):
            return self._failure(
                result.get("error") or "EditPlan 未能生成。",
                events=result.get("events") or [],
                raw=result,
            )

        final = dict(result.get("final") or {})
        normalized = self._normalize_plan(final, design)
        normalized.update({
            "events": result.get("events") or [],
            "raw": result,
        })
        return normalized

    @classmethod
    def apply_design_replacements(
        cls,
        design: Mapping[str, Any] | str,
        replacements: Iterable[Mapping[str, Any]],
    ) -> Dict[str, Any] | str:
        """Apply replacements only when each `before` occurs exactly once.

        The operation returns a deep copy and never mutates the saved Design.
        A caller can therefore build a candidate revision before persisting it.
        """

        if not isinstance(design, (Mapping, str)):
            raise ValueError("Design must be an object or string.")
        current: Any = copy.deepcopy(dict(design) if isinstance(design, Mapping) else design)
        for index, item in enumerate(replacements):
            if not isinstance(item, Mapping):
                raise ValueError(f"Design replacement {index + 1} is not an object.")
            before = item.get("before")
            after = item.get("after")
            if not isinstance(before, str) or not before:
                raise ValueError(f"Design replacement {index + 1} has no exact before text.")
            if not isinstance(after, str):
                raise ValueError(f"Design replacement {index + 1} has invalid after text.")
            if before == after:
                raise ValueError(f"Design replacement {index + 1} does not change the Design.")
            candidate, occurrence_count = cls._replace_in_strings(current, before, after)
            if occurrence_count != 1:
                raise ValueError(
                    f"Design replacement {index + 1} matched {occurrence_count} locations; expected exactly one."
                )
            current = candidate
        return current

    @classmethod
    def _normalize_plan(
        cls,
        final: Mapping[str, Any],
        design: Mapping[str, Any] | str,
    ) -> Dict[str, Any]:
        route = _trim(final.get("route"))
        impact_summary = _trim(final.get("impact_summary"))
        raw_assets = final.get("affected_assets")
        raw_asset_names = [
            _trim(item)
            for item in raw_assets
            if _trim(item)
        ] if isinstance(raw_assets, list) else []
        assets = [
            name for name in cls._ASSET_ORDER if name in raw_asset_names
        ]
        unknown_assets = [
            name for name in raw_asset_names if name not in cls._ASSET_ORDER
        ]
        metadata_value = final.get("metadata_patch")
        metadata_value = metadata_value if isinstance(metadata_value, Mapping) else {}
        metadata_patch = {
            "display_name": cls._optional_text(metadata_value.get("display_name")),
            "description": cls._optional_text(metadata_value.get("description")),
        }
        raw_replacements = final.get("design_replacements")
        replacements = [
            {
                "before": item.get("before"),
                "after": item.get("after"),
                "reason": _trim(item.get("reason")),
            }
            for item in raw_replacements or []
            if isinstance(item, Mapping)
        ] if isinstance(raw_replacements, list) else []
        implementation_instruction = _trim(final.get("implementation_instruction"))

        if route not in {"local_patch", "full_revision"}:
            return cls._invalid_output("EditPlan 缺少有效 route。")
        if not impact_summary:
            return cls._invalid_output("EditPlan 缺少影响说明。")
        if not assets or unknown_assets or len(raw_asset_names) != len(set(raw_asset_names)):
            return cls._fallback_full_revision(
                assets=assets,
                impact_summary=impact_summary,
                reason="受影响资产列表不完整或包含未知资产。",
            )

        if route == "full_revision":
            return {
                "ok": True,
                "route": "full_revision",
                "affected_assets": assets,
                "impact_summary": impact_summary,
                "metadata_patch": {"display_name": None, "description": None},
                "design_replacements": [],
                "implementation_instruction": "",
            }

        metadata_changed = any(value is not None for value in metadata_patch.values())
        if "contract" in assets:
            return cls._fallback_full_revision(
                assets=assets,
                impact_summary=impact_summary,
                reason="公开契约变化不能使用局部补丁。",
            )
        if ("metadata" in assets) != metadata_changed:
            return cls._fallback_full_revision(
                assets=assets,
                impact_summary=impact_summary,
                reason="元数据影响范围与 metadata_patch 不一致。",
            )
        if ("design" in assets) != bool(replacements):
            return cls._fallback_full_revision(
                assets=assets,
                impact_summary=impact_summary,
                reason="Design 影响范围与精确替换列表不一致。",
            )
        if assets == ["metadata"] and metadata_changed and not replacements:
            # Metadata is system-owned and complete in metadata_patch.  A
            # model may still write explanatory no-op prose in the Coding
            # field; it must never turn a display-only edit into a Coding run.
            return {
                "ok": True,
                "route": "local_patch",
                "affected_assets": assets,
                "impact_summary": impact_summary,
                "metadata_patch": metadata_patch,
                "design_replacements": [],
                "implementation_instruction": "",
            }
        if ("implementation" in assets) != bool(implementation_instruction):
            return cls._fallback_full_revision(
                assets=assets,
                impact_summary=impact_summary,
                reason="实现影响范围与 Coding 指令不一致。",
            )
        if replacements:
            try:
                cls.apply_design_replacements(design, replacements)
            except ValueError as exc:
                return cls._fallback_full_revision(
                    assets=assets,
                    impact_summary=impact_summary,
                    reason=str(exc),
                )
        return {
            "ok": True,
            "route": "local_patch",
            "affected_assets": assets,
            "impact_summary": impact_summary,
            "metadata_patch": metadata_patch,
            "design_replacements": replacements,
            "implementation_instruction": implementation_instruction,
        }

    @classmethod
    def _fallback_full_revision(
        cls,
        *,
        assets: Iterable[str],
        impact_summary: str,
        reason: str,
    ) -> Dict[str, Any]:
        normalized_assets = [name for name in cls._ASSET_ORDER if name in set(assets)]
        if not normalized_assets:
            normalized_assets = ["design", "implementation"]
        return {
            "ok": True,
            "route": "full_revision",
            "affected_assets": normalized_assets,
            "impact_summary": impact_summary,
            "metadata_patch": {"display_name": None, "description": None},
            "design_replacements": [],
            "implementation_instruction": "",
            "fallback_reason": _trim(reason),
        }

    @staticmethod
    def _invalid_output(error: str) -> Dict[str, Any]:
        return {
            "ok": False,
            "error": {
                "code": "edit_plan_output_invalid",
                "summary": _trim(error) or "EditPlan 输出无法解析。",
            },
        }

    @staticmethod
    def _failure(
        error: Any,
        *,
        events: Optional[Iterable[Mapping[str, Any]]] = None,
        raw: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        return {
            "ok": False,
            "error": {
                "code": "edit_plan_failed",
                "summary": _trim(error) or "EditPlan 未能生成。",
            },
            "events": [dict(item) for item in events or [] if isinstance(item, Mapping)],
            "raw": dict(raw or {}),
        }

    @staticmethod
    def _design_asset(design: Mapping[str, Any] | str) -> Optional[Dict[str, Any]]:
        if isinstance(design, Mapping):
            return copy.deepcopy(dict(design))
        if isinstance(design, str) and _trim(design):
            return {"document": design}
        return None

    @staticmethod
    def _optional_text(value: Any) -> Optional[str]:
        if value is None:
            return None
        return _trim(value) or None

    @classmethod
    def _replace_in_strings(cls, value: Any, before: str, after: str) -> tuple[Any, int]:
        if isinstance(value, str):
            count = value.count(before)
            return value.replace(before, after), count
        if isinstance(value, Mapping):
            result: Dict[Any, Any] = {}
            count = 0
            for key, item in value.items():
                replaced, item_count = cls._replace_in_strings(item, before, after)
                result[key] = replaced
                count += item_count
            return result, count
        if isinstance(value, list):
            result_list = []
            count = 0
            for item in value:
                replaced, item_count = cls._replace_in_strings(item, before, after)
                result_list.append(replaced)
                count += item_count
            return result_list, count
        if isinstance(value, tuple):
            result_items = []
            count = 0
            for item in value:
                replaced, item_count = cls._replace_in_strings(item, before, after)
                result_items.append(replaced)
                count += item_count
            return tuple(result_items), count
        return value, 0


class CodexCustomToolDesigner:
    def __init__(
        self,
        *,
        harness: Optional[CodexExecSkillHarness] = None,
        requirement_skill_path: str = "src/skills/financial-tool-development/skills/financial-tool-requirement/SKILL.md",
        requirement_schema_path: str = "src/skills/financial-tool-development/skills/financial-tool-requirement/schema.json",
        skill_path: str = "src/skills/financial-tool-development/skills/financial-tool-design/SKILL.md",
        output_schema_path: str = "src/skills/financial-tool-development/schema.json",
        flowchart_skill_path: str = "src/skills/financial-tool-development/skills/financial-tool-flowchart/SKILL.md",
        flowchart_schema_path: str = "src/skills/financial-tool-development/skills/financial-tool-flowchart/schema.json",
    ) -> None:
        self.harness = harness or CodexExecSkillHarness(cwd=".")
        self.requirement_skill_path = requirement_skill_path
        self.requirement_schema_path = requirement_schema_path
        self.skill_path = skill_path
        self.output_schema_path = output_schema_path
        self.flowchart_skill_path = flowchart_skill_path
        self.flowchart_schema_path = flowchart_schema_path

    def design(
        self,
        requirement_text: str,
        *,
        context: Optional[Mapping[str, Any]] = None,
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        run_context = dict(context or {})
        selected = [
            _trim(item)
            for item in run_context.get("selected_skills") or []
            if _trim(item)
        ]
        if not selected:
            selected = ["financial-tool-requirement"]
        run_requirement = "financial-tool-requirement" in selected
        run_design = "financial-tool-design" in selected
        run_flowchart = "financial-tool-flowchart" in selected
        stage_context = dict(run_context)
        stage_context.pop("selected_skills", None)
        events: List[Dict[str, Any]] = []
        requirement_final: Dict[str, Any] = {}

        if run_requirement:
            def requirement_event_sink(event: Dict[str, Any]) -> None:
                if _trim(event.get("source")) == "model" and _trim(event.get("type")) == "final":
                    return
                if event_sink:
                    event_sink(event)

            requirement_result = self.harness.run_skill(
                skill_path=self.requirement_skill_path,
                output_schema_path=self.requirement_schema_path,
                user_request=requirement_text,
                context=stage_context,
                stage="requirement",
                event_sink=requirement_event_sink,
            )
            events.extend(
                event
                for event in requirement_result.get("events") or []
                if not (_trim(event.get("source")) == "model" and _trim(event.get("type")) == "final")
            )
            if not requirement_result.get("ok"):
                return self._failure(requirement_result, events)
            requirement_final = dict(requirement_result.get("final") or {})
            stage_context["requirement_brief"] = requirement_final.get("requirement_brief") or ""
            stage_context["requirement_questions"] = [
                dict(item)
                for item in requirement_final.get("questions") or []
                if isinstance(item, Mapping)
            ]

        requirement_questions = [
            dict(item)
            for item in requirement_final.get("questions") or []
            if isinstance(item, Mapping)
        ]
        if run_requirement and requirement_questions:
            brief = _requirement_understanding(requirement_final.get("requirement_brief"))
            return {
                "ok": True,
                "message": _trim(requirement_final.get("summary")) or "请补充影响核心方案的信息。",
                "understanding": brief,
                "questions": requirement_questions,
                "design": {},
                "existing_analysis": {},
                "events": events,
                "raw": {"requirement": requirement_final},
            }
        # A complete, high-confidence requirement can continue in this turn.
        if run_requirement and not run_design and not run_flowchart and not requirement_questions:
            run_design = True
            run_flowchart = True
        if not run_design and not run_flowchart:
            brief = _requirement_understanding(requirement_final.get("requirement_brief"))
            return {
                "ok": True,
                "message": _trim(requirement_final.get("summary")) or "请补充影响核心方案的信息。",
                "understanding": brief,
                "questions": requirement_questions,
                "design": {},
                "existing_analysis": {},
                "events": events,
                "raw": {"requirement": requirement_final},
            }

        result: Dict[str, Any] = {}
        final: Dict[str, Any] = {}
        if run_design:
            result = self.harness.run_skill(
                skill_path=self.skill_path,
                output_schema_path=self.output_schema_path,
                user_request=requirement_text,
                context=stage_context,
                stage="design",
                event_sink=event_sink,
            )
            events.extend(result.get("events") or [])
            if not result.get("ok"):
                return self._failure(result, events)
            final = dict(result.get("final") or {})
        understanding = _requirement_understanding(
            requirement_final.get("requirement_brief")
            if requirement_final
            else stage_context.get("requirement_brief")
        )
        questions = requirement_questions
        design_value = final.get("design") if final.get("design") is not None else stage_context.get("current_design")
        design = (
            {"document": _trim(design_value)}
            if isinstance(design_value, str) and _trim(design_value)
            else dict(design_value)
            if isinstance(design_value, Mapping)
            else {}
        )
        finance_tool_profile = final.get("finance_tool_profile")
        if isinstance(finance_tool_profile, Mapping):
            design["finance_tool_profile"] = copy.deepcopy(
                dict(finance_tool_profile)
            )
        if run_flowchart and not design:
            return self._failure({"error": "流程图生成前没有可读取的模块与流程设计。"}, events)
        flowchart_final: Dict[str, Any] = {}
        if run_flowchart and design:
            flowchart_context = dict(stage_context)
            flowchart_context["design"] = design
            flowchart_result = self.harness.run_skill(
                skill_path=self.flowchart_skill_path,
                output_schema_path=self.flowchart_schema_path,
                user_request="请根据当前模块和流程设计生成对应流程图，不改变方案。",
                context=flowchart_context,
                stage="flowchart",
                event_sink=event_sink,
            )
            events.extend(flowchart_result.get("events") or [])
            if not flowchart_result.get("ok"):
                return self._failure(flowchart_result, events)
            flowchart_final = dict(flowchart_result.get("final") or {})
            mermaid = _trim(flowchart_final.get("mermaid"))
            if mermaid:
                design["mermaid"] = mermaid
        goal = _trim(understanding.get("goal"))
        return {
            "ok": True,
            "message": (
                f"{goal}的实现方案已形成，请检查后确认。" if goal else "实现方案已形成，请检查后确认。"
            ),
            "understanding": understanding,
            "questions": questions,
            "design": design,
            "existing_analysis": {},
            "events": events,
            "raw": {"requirement": requirement_final, "design": result, "flowchart": flowchart_final},
        }

    @staticmethod
    def _failure(result: Mapping[str, Any], events: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "ok": False,
            "message": "Design 调用失败，当前设计和实现均未改变。",
            "error": result.get("error") or "Agent design skill 未能生成最终设计。",
            "events": events,
            "raw": dict(result),
        }

    @staticmethod
    def _merge_questions(first: Any, second: Any) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for item in list(first or []) + list(second or []):
            if not isinstance(item, Mapping):
                continue
            question = dict(item)
            identity = _trim(question.get("id")) or _trim(question.get("question"))
            if identity and identity in seen:
                continue
            if identity:
                seen.add(identity)
            merged.append(question)
        return merged


class CodexCustomToolCoder:
    def __init__(
        self,
        *,
        harness: Optional[CodexExecSkillHarness] = None,
        skill_path: str = "src/skills/financial-tool-development/skills/financial-tool-implementation/SKILL.md",
        output_schema_path: str = "src/skills/financial-tool-development/skills/financial-tool-implementation/schema.json",
    ) -> None:
        self.harness = harness or CodexExecSkillHarness(cwd=".")
        self.skill_path = skill_path
        self.output_schema_path = output_schema_path

    def code(
        self,
        design: Mapping[str, Any],
        *,
        requirement_text: str = "",
        context: Optional[Mapping[str, Any]] = None,
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        request = "请根据 CONTEXT.design 或 CONTEXT.design_ref 中的权威设计实现或修订可动态加载执行的金融工具模块。"
        run_context = dict(context or {})
        agent_runtime = run_context.pop("_agent_runtime", None)
        agent_runtime = dict(agent_runtime) if isinstance(agent_runtime, Mapping) else {}
        provider_session_id = _trim(agent_runtime.get("provider_session_id"))
        if provider_session_id:
            run_context["_provider_session_id"] = provider_session_id
        run_context["design"] = dict(design)
        if _trim(requirement_text):
            run_context["requirement_brief"] = _trim(requirement_text)
        result = self.harness.run_skill(
            skill_path=self.skill_path,
            output_schema_path=self.output_schema_path,
            user_request=request,
            context=run_context,
            session_id=_trim(agent_runtime.get("session_id")),
            stage="coding",
            event_sink=event_sink,
        )
        if not result.get("ok"):
            failure = self._failure_summary(
                result.get("error"),
                failure_kind=result.get("failure_kind"),
            )
            return {
                "ok": False,
                "message": f"实现未完成：{failure['summary']} 当前设计已保留，可以重试。",
                "error": failure,
                "error_detail": result.get("error") or "",
                "events": result.get("events") or [],
                "raw": result,
                "agent_runtime": {
                    **agent_runtime,
                    "provider_session_id": _trim(result.get("provider_session_id")) or provider_session_id,
                },
            }
        final = dict(result.get("final") or {})
        implementation = final.get("implementation") if isinstance(final.get("implementation"), Mapping) else {}
        modules = [item for item in implementation.get("modules") or [] if isinstance(item, Mapping)]
        has_code = bool(_trim(final.get("code"))) or bool(
            modules and any(_trim(item.get("source_code")) for item in modules)
        )
        return {
            "ok": has_code,
            "message": _trim(final.get("message")) or ("代码已生成。" if has_code else "本次没有生成可执行模块。"),
            **({"error": {"code": "coding_output_empty", "summary": "本次没有生成可执行模块。"}} if not has_code else {}),
            "final": final,
            "events": result.get("events") or [],
            "raw": result,
            "agent_runtime": {
                **agent_runtime,
                "provider_session_id": _trim(result.get("provider_session_id")) or provider_session_id,
            },
        }

    @staticmethod
    def _failure_summary(error_detail: Any, *, failure_kind: Any = "") -> Dict[str, str]:
        text = _trim(error_detail)
        kind = _trim(failure_kind).lower()
        lowered = f"{kind} {text}".lower()
        if "invalid_json_schema" in lowered or "invalid schema for response_format" in lowered:
            field = ""
            missing_required = re.search(
                r"\bmissing\s+['\"]([^'\"]+)['\"]",
                text,
                flags=re.IGNORECASE,
            )
            if missing_required:
                field = _trim(missing_required.group(1))
                return {
                    "code": "coding_schema_invalid",
                    "summary": f"字段 {field} 未包含在严格 Schema 的 required 声明中。",
                }
            marker = "in context=('properties', '"
            if marker in lowered:
                field = lowered.split(marker, 1)[1].split("'", 1)[0].strip()
            detail = f"字段 {field} 缺少严格 Schema 所需的类型声明。" if field else "结构化输出 Schema 不符合严格格式要求。"
            return {"code": "coding_schema_invalid", "summary": detail}
        if "authorizationrequired" in lowered or "oauth authorization required" in lowered or "re-authorization required" in lowered:
            return {"code": "coding_auth_required", "summary": "Agent 服务授权已失效，需要重新授权。"}
        if "incorrect api key" in lowered or "invalid_api_key" in lowered or "error code: 401" in lowered:
            return {"code": "coding_auth_failed", "summary": "Coding 模型鉴权失败，请检查当前服务凭证。"}
        if "rate limit" in lowered or "error code: 429" in lowered:
            return {"code": "coding_rate_limited", "summary": "Coding 模型服务触发限流，本次实现没有完成。"}
        if "timeout" in lowered:
            return {"code": "coding_timeout", "summary": "Coding 调用超时，未取得有效结果。"}
        if "stream disconnected" in lowered or "reconnecting" in lowered:
            return {"code": "coding_connection_failed", "summary": "Coding 连接中断，未取得有效结果。"}
        if "error_max_turns" in lowered:
            permission_reason = ""
            marker = "tool permission denied:"
            if marker in lowered:
                permission_reason = CodexCustomToolCoder._permission_failure_summary(
                    lowered.split(marker, 1)[1].strip()
                )
            summary = (
                f"Coding Agent 的{permission_reason}，因此在最大执行轮次内没有完成。"
                if permission_reason
                else "Coding Agent 达到本次最大执行轮次，尚未完成实现与验证。"
            )
            return {"code": "coding_turn_limit", "summary": summary}
        if "tool permission denied:" in lowered:
            reason = CodexCustomToolCoder._permission_failure_summary(
                lowered.split("tool permission denied:", 1)[1].strip()
            )
            return {
                "code": "coding_tool_permission_denied",
                "summary": f"Coding Agent 的{reason or '工具调用被执行权限拒绝'}。",
            }
        if "missing_structured_output" in lowered or "did not return a structured result" in lowered:
            return {
                "code": "coding_output_missing",
                "summary": "Coding Agent 已结束，但没有返回系统可接收的实现结果。",
            }
        safe_detail = re.sub(r"\s+", " ", text)[:240]
        return {
            "code": "coding_runtime_failed",
            "summary": f"Coding 运行失败：{safe_detail}" if safe_detail else "Coding 运行失败，未返回错误详情。",
        }

    @staticmethod
    def _permission_failure_summary(reason: str) -> str:
        if "compile target is outside" in reason:
            return "编译目标不在可编辑模块范围内"
        if "focused test scripts must be stored under scratch" in reason:
            return "测试脚本不在隔离测试目录内"
        if "configured python interpreter" in reason:
            return "Python 解释器不符合执行环境约定"
        if "only isolated python compile/test commands" in reason:
            return "测试命令超出隔离执行范围"
        if "shell execution is disabled" in reason:
            return "当前阶段未开放代码执行权限"
        if "write target is outside" in reason:
            return "写入位置不在可编辑模块或隔离测试目录内"
        return "工具调用被执行权限拒绝"


class CodexCustomToolEditCoder(CodexCustomToolCoder):
    """Focused Coding adapter for a planner-approved local implementation edit."""

    default_complexity = "fastest"

    def __init__(
        self,
        *,
        harness: Optional[CodexExecSkillHarness] = None,
        skill_path: str = "src/skills/financial-tool-development/skills/financial-tool-edit-implementation/SKILL.md",
        output_schema_path: str = "src/skills/financial-tool-development/skills/financial-tool-implementation/schema.json",
    ) -> None:
        super().__init__(
            harness=harness,
            skill_path=skill_path,
            output_schema_path=output_schema_path,
        )

    def code(
        self,
        design: Mapping[str, Any],
        *,
        implementation_instruction: str = "",
        requirement_text: str = "",
        context: Optional[Mapping[str, Any]] = None,
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        del requirement_text  # A local edit is governed by the EditPlan, not the original free-form request.
        source_context = dict(context or {})
        instruction = _trim(
            implementation_instruction
            or source_context.get("implementation_instruction")
            or source_context.get("coding_feedback")
        )
        current_implementation = source_context.get("current_implementation")
        current_implementation = (
            dict(current_implementation)
            if isinstance(current_implementation, Mapping)
            else {}
        )
        current_modules = [
            dict(item)
            for item in current_implementation.get("modules") or []
            if isinstance(item, Mapping) and isinstance(item.get("source_code"), str)
        ]
        if not instruction:
            return self._local_failure(
                "edit_coding_instruction_missing",
                "局部 EditPlan 没有提供实现修改指令。",
            )
        if not isinstance(design, Mapping) or not design:
            return self._local_failure(
                "edit_coding_design_missing",
                "局部 Coding 没有取得修订后的完整 Design。",
            )
        if not current_modules or not any(_trim(item.get("source_code")) for item in current_modules):
            return self._local_failure(
                "edit_coding_implementation_missing",
                "局部 Coding 只能修改已有实现，但当前没有可读取的模块源码。",
            )

        agent_runtime_value = source_context.get("_agent_runtime")
        agent_runtime = (
            dict(agent_runtime_value)
            if isinstance(agent_runtime_value, Mapping)
            else {}
        )
        workspace_identity = source_context.get("_workspace_identity")
        run_context: Dict[str, Any] = {
            "design": dict(design),
            "current_implementation": {
                **current_implementation,
                "modules": current_modules,
            },
            "implementation_instruction": instruction,
        }
        provider_session_id = _trim(agent_runtime.get("provider_session_id"))
        if provider_session_id:
            run_context["_provider_session_id"] = provider_session_id
        if isinstance(workspace_identity, Mapping):
            run_context["_workspace_identity"] = dict(workspace_identity)

        result = self.harness.run_skill(
            skill_path=self.skill_path,
            output_schema_path=self.output_schema_path,
            user_request="请严格按 CONTEXT.implementation_instruction 局部修改现有实现并完成合成正反例验证。",
            context=run_context,
            session_id=_trim(agent_runtime.get("session_id")),
            stage="edit_coding",
            event_sink=event_sink,
        )
        provider_session_id = _trim(result.get("provider_session_id")) or provider_session_id
        next_agent_runtime = {
            **agent_runtime,
            "provider_session_id": provider_session_id,
        }
        if not result.get("ok"):
            failure = self._failure_summary(
                result.get("error"),
                failure_kind=result.get("failure_kind"),
            )
            return {
                "ok": False,
                "message": f"局部实现未完成：{failure['summary']} 原实现保持不变。",
                "error": failure,
                "error_detail": result.get("error") or "",
                "events": result.get("events") or [],
                "raw": result,
                "agent_runtime": next_agent_runtime,
            }

        final = dict(result.get("final") or {})
        implementation = (
            dict(final.get("implementation"))
            if isinstance(final.get("implementation"), Mapping)
            else {}
        )
        edited_modules = [
            dict(item)
            for item in implementation.get("modules") or []
            if isinstance(item, Mapping) and isinstance(item.get("source_code"), str)
        ]
        if not edited_modules:
            return self._local_result_failure(
                code="edit_coding_output_empty",
                summary="局部 Coding 没有回收出可执行模块。",
                result=result,
                agent_runtime=next_agent_runtime,
            )
        if not self._modules_changed(current_modules, edited_modules):
            return self._local_result_failure(
                code="edit_coding_no_change",
                summary="局部 Coding 完成后源码没有发生变化。",
                result=result,
                agent_runtime=next_agent_runtime,
            )

        expected_tool_name = _trim(design.get("tool_name"))
        tool_contract = final.get("tool_contract")
        tool_contract = tool_contract if isinstance(tool_contract, Mapping) else {}
        # Identity is a system-owned fact.  The model's duplicated
        # tool_contract is explanatory output and must not be able to rename
        # the selected tool or reject an otherwise valid local patch.
        if expected_tool_name:
            final["tool_contract"] = {
                **dict(tool_contract),
                "tool_name": expected_tool_name,
            }

        evidence = final.get("coding_test_evidence")
        evidence = evidence if isinstance(evidence, Mapping) else {}
        evidence_cases = [
            dict(item)
            for item in evidence.get("cases") or []
            if isinstance(item, Mapping)
            and isinstance(item.get("input"), Mapping)
            and isinstance(item.get("actual"), Mapping)
        ]
        if not evidence_cases:
            return self._local_result_failure(
                code="edit_coding_evidence_missing",
                summary="局部 Coding 没有留下可回收的合成验证证据。",
                result=result,
                agent_runtime=next_agent_runtime,
            )
        return {
            "ok": True,
            "message": _trim(final.get("message")) or "局部代码修改和合成正反例验证已完成。",
            "final": final,
            "events": result.get("events") or [],
            "raw": result,
            "agent_runtime": next_agent_runtime,
        }

    @staticmethod
    def _modules_changed(
        before_modules: Iterable[Mapping[str, Any]],
        after_modules: Iterable[Mapping[str, Any]],
    ) -> bool:
        def sources(items: Iterable[Mapping[str, Any]]) -> Dict[str, str]:
            result: Dict[str, str] = {}
            for index, item in enumerate(items):
                module_id = _trim(item.get("module_id")) or f"module_{index + 1}"
                result[module_id] = str(item.get("source_code") or "")
            return result

        return sources(before_modules) != sources(after_modules)

    @staticmethod
    def _local_failure(code: str, summary: str) -> Dict[str, Any]:
        return {
            "ok": False,
            "message": f"局部实现未开始：{summary}",
            "error": {"code": code, "summary": summary},
            "events": [],
            "raw": {},
            "agent_runtime": {},
        }

    @staticmethod
    def _local_result_failure(
        *,
        code: str,
        summary: str,
        result: Mapping[str, Any],
        agent_runtime: Mapping[str, Any],
    ) -> Dict[str, Any]:
        return {
            "ok": False,
            "message": f"局部实现未完成：{summary} 原实现保持不变。",
            "error": {"code": code, "summary": summary},
            "events": result.get("events") or [],
            "raw": dict(result),
            "agent_runtime": dict(agent_runtime),
        }


class CodexCustomToolTester:
    def __init__(
        self,
        *,
        harness: Optional[CodexExecSkillHarness] = None,
        skill_path: str = "src/skills/financial-tool-development/skills/financial-tool-test-execution/SKILL.md",
        output_schema_path: str = "src/skills/financial-tool-development/skills/financial-tool-test-execution/schema.json",
    ) -> None:
        self.harness = harness or CodexExecSkillHarness(cwd=".")
        self.skill_path = skill_path
        self.output_schema_path = output_schema_path

    def plan(
        self,
        request: str,
        *,
        context: Optional[Mapping[str, Any]] = None,
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        result = self.harness.run_skill(
            skill_path=self.skill_path,
            output_schema_path=self.output_schema_path,
            user_request=request,
            context=dict(context or {}),
            stage="test",
            event_sink=event_sink,
        )
        if not result.get("ok"):
            return {
                "ok": False,
                "message": "测试样例规划失败，当前实现未改变。",
                "error": result.get("error") or "test planning failed",
                "events": result.get("events") or [],
            }
        final = dict(result.get("final") or {})
        cases = [dict(item) for item in final.get("cases") or [] if isinstance(item, Mapping)]
        next_action = _trim(final.get("next_action"))
        if next_action not in {"run_tests", "finish"}:
            next_action = "run_tests" if cases else "finish"
        return {
            "ok": True,
            "message": _trim(final.get("summary")),
            "next_action": next_action,
            "assessment": _trim(final.get("assessment")),
            "cases": cases,
            "presentation": dict(final.get("presentation") or {}),
            "events": result.get("events") or [],
        }
