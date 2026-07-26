from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, AsyncIterator, Callable, Dict, Iterable, List, Mapping, Optional
from urllib.parse import urlparse

import fastjsonschema
from json_repair import loads as repair_json

from src.services.agent_providers.skill_support import AgentSkillHarnessSupport
from src.services.agent_providers.protocol import AgentEventCoalescer, normalize_agent_run_result
from src.services.agent_providers.runtime_policy import AgentCapabilityPolicy, WebSearchPolicy
from src.services.coding_execution_contract import coding_command_denial_reason
from src.services.custom_tool_context_bundle_service import CustomToolContextBundleService


DEEPSEEK_ANTHROPIC_BASE_URL = "https://api.deepseek.com/anthropic"
DASHSCOPE_ANTHROPIC_BASE_URL = "https://dashscope.aliyuncs.com/apps/anthropic"
DEFAULT_CLAUDE_PROVIDER = "deepseek"
DEFAULT_CLAUDE_MODEL = "deepseek-v4-flash"
_PROVIDERS = {"anthropic", "deepseek", "dashscope", "gateway"}
_MUTATING_TOOLS = {"Edit", "Write", "NotebookEdit"}
_READ_TOOLS = {"Read", "Glob", "Grep"}
def _trim(value: Any) -> str:
    return str(value or "").strip()


class ClaudeSdkSkillHarness(AgentSkillHarnessSupport):
    """Claude Agent SDK adapter implementing the same skill contract as Codex.

    Provider-specific messages, permissions, credentials, and timeouts terminate
    here. Business callers continue to exchange the existing Design/Coding schema.
    """

    provider_name = "claude"

    def __init__(
        self,
        *,
        cwd: str = ".",
        timeout_seconds: int = 300,
        hard_timeout_seconds: int = 1800,
        model: str = "",
        effort: str = "high",
        provider: str = "",
        base_url: str = "",
        max_turns: int = 20,
        max_budget_usd: float = 0.0,
        thinking: str = "",
        complexity_level: str = "",
        capabilities: Optional[AgentCapabilityPolicy] = None,
        context_bundle_service: Optional[CustomToolContextBundleService] = None,
        query_impl: Optional[Callable[..., AsyncIterator[Any]]] = None,
        structured_output_recovery: Optional[Callable[[str, str, Mapping[str, Any]], Mapping[str, Any]]] = None,
    ) -> None:
        super().__init__(
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            hard_timeout_seconds=hard_timeout_seconds,
            model=model,
            context_bundle_service=context_bundle_service,
        )
        self.provider = _trim(provider or os.environ.get("CLAUDE_PROVIDER") or DEFAULT_CLAUDE_PROVIDER).lower()
        if self.provider not in _PROVIDERS:
            raise ValueError(f"unsupported Claude provider: {self.provider or '-'}")
        self.base_url = self._resolve_base_url(base_url)
        self.model = _trim(model or os.environ.get("STOCK_AGENT_CUSTOM_TOOL_CLAUDE_MODEL") or os.environ.get("CLAUDE_MODEL"))
        if not self.model:
            self.model = _trim(os.environ.get("LLM_DEFAULT_MODEL")) or DEFAULT_CLAUDE_MODEL
        self.effort = _trim(effort or os.environ.get("STOCK_AGENT_CUSTOM_TOOL_CLAUDE_EFFORT") or "high")
        self.max_turns = max(1, int(max_turns or 20))
        self.max_budget_usd = max(0.0, float(max_budget_usd or 0.0))
        self.thinking = _trim(thinking).lower()
        if self.thinking not in {"", "disabled", "adaptive"}:
            raise ValueError(f"unsupported Claude thinking mode: {self.thinking}")
        self.complexity_level = _trim(complexity_level).lower()
        self.capabilities = capabilities
        self._query_impl = query_impl
        self._structured_output_recovery = structured_output_recovery

    def available(self) -> bool:
        if self._query_impl is not None:
            return True
        try:
            import claude_agent_sdk  # noqa: F401
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
        stage_name = _trim(stage) or self._infer_stage(Path(skill_path))
        if not self.available():
            return self._unavailable_result(session_id=session_id, stage=stage_name)

        skill_file = Path(skill_path)
        if not skill_file.is_absolute():
            skill_file = self.cwd / skill_file
        if not skill_file.exists():
            return self._failure_result(f"skill file not found: {skill_file}", session_id=session_id, stage=stage_name)
        try:
            output_schema_file = self._resolve_output_schema_file(
                skill_file=skill_file,
                output_schema_path=output_schema_path,
            )
            if output_schema_file is None:
                return self._failure_result(
                    "Claude skill runs require an output schema",
                    session_id=session_id,
                    stage=stage_name,
                )
            output_schema = self._load_output_schema(output_schema_file)
        except (FileNotFoundError, ValueError) as exc:
            return self._failure_result(str(exc), session_id=session_id, stage=stage_name)

        run_context = dict(context or {})
        resume_session_id = _trim(run_context.pop("_provider_session_id", ""))
        events: List[Dict[str, Any]] = []
        self._append_event(
            events,
            {
                "source": "harness",
                "type": "stage_start",
                "content": f"claude sdk skill stage started: {stage_name}",
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
        execution_cwd = self._execution_cwd(public_bundle).resolve()
        self._append_event(
            events,
            {
                "source": "harness",
                "type": "context_ready",
                "content": "context bundle prepared",
                "metadata": {"stage": stage_name, "bundle_dir": public_bundle.get("bundle_dir"), "provider": self.provider_name},
            },
            event_sink,
        )
        _plugin, qualified_skill = self._enabled_native_skill_config(skill_file.resolve())
        if qualified_skill:
            prompt = (
                f"/{qualified_skill}\n\n"
                + self._build_native_skill_prompt(
                    user_request=user_request,
                    context=prompt_context,
                    stage=stage_name,
                )
            )
        else:
            prompt = self._build_prompt(
                skill_text=skill_file.read_text(encoding="utf-8"),
                skill_root=str(skill_file.parent.resolve()),
                user_request=user_request,
                context=prompt_context,
                structured_output=True,
                stage=stage_name,
            )
        allowed_write_paths = self._allowed_write_paths(public_bundle, execution_cwd)
        run_result = self._run_async_sync(
            self._stream_query(
                prompt=prompt,
                system_prompt=self._developer_instructions(public_bundle),
                output_schema=output_schema,
                stage=stage_name,
                cwd=execution_cwd,
                readable_roots=(execution_cwd, skill_file.parent.resolve()),
                allowed_write_paths=allowed_write_paths,
                skill_file=skill_file.resolve(),
                allow_asset_reads=bool(prompt_context.get("design_ref")),
                resume_session_id=resume_session_id,
                event_sink=event_sink,
            )
        )
        events.extend(run_result.get("events") or [])
        final = dict(run_result.get("final") or {})
        if final and stage_name == "coding":
            final = self._collect_coding_result(bundle, final)
        if final:
            self._append_event(
                events,
                {**final, "source": "model", "type": "final", "metadata": {"stage": stage_name, "provider": self.provider_name}},
                event_sink,
            )
        ok = bool(final) and not run_result.get("error") and not run_result.get("timeout")
        self._append_event(
            events,
            {
                "source": "harness",
                "type": "stage_result",
                "content": "claude sdk skill stage parsed" if ok else "claude sdk skill stage failed",
                "metadata": {
                    "stage": stage_name,
                    "provider": self.provider_name,
                    "ok": ok,
                    "final_status": final.get("status"),
                    "duration_ms": int((time.time() - started_at) * 1000),
                },
            },
            event_sink,
        )
        return normalize_agent_run_result({
            "ok": ok,
            "error": _trim(run_result.get("error")) or ("Claude did not return a structured result" if not final else ""),
            "failure_kind": _trim(run_result.get("failure_kind")),
            "timeout": bool(run_result.get("timeout")),
            "timeout_kind": _trim(run_result.get("timeout_kind")),
            "timeout_after_seconds": int(run_result.get("timeout_after_seconds") or 0),
            "events": events,
            "final": final,
            "session_id": session_id,
            "provider_session_id": _trim(run_result.get("provider_session_id")),
            "raw_stdout": "",
            "raw_stderr": _trim(run_result.get("raw_stderr")),
            "last_message": _trim(run_result.get("last_message")),
            "llm_usage": dict(run_result.get("llm_usage") or {}),
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
        started_at = time.time()
        if not self.available():
            return self._unavailable_result(session_id="", stage=stage)
        events: List[Dict[str, Any]] = []
        self._append_event(
            events,
            {
                "source": "harness",
                "type": "stage_start",
                "content": f"claude direct turn started: {stage}",
                "metadata": {"stage": stage, "provider": self.provider_name},
            },
            event_sink,
        )
        run_result = self._run_async_sync(
            self._stream_query(
                prompt=prompt,
                system_prompt=developer_instructions,
                output_schema=dict(output_schema),
                stage=stage,
                cwd=self.cwd.resolve(),
                readable_roots=(self.cwd.resolve(),),
                allowed_write_paths=frozenset(),
                event_sink=event_sink,
                allow_writes=False,
            )
        )
        events.extend(run_result.get("events") or [])
        final = dict(run_result.get("final") or {})
        ok = bool(final) and not run_result.get("error") and not run_result.get("timeout")
        self._append_event(
            events,
            {
                "source": "harness",
                "type": "stage_result",
                "content": "claude direct turn completed" if ok else "claude direct turn failed",
                "metadata": {"stage": stage, "provider": self.provider_name, "ok": ok, "duration_ms": int((time.time() - started_at) * 1000)},
            },
            event_sink,
        )
        return normalize_agent_run_result({
            "ok": ok,
            "error": _trim(run_result.get("error")) or ("Claude did not return a structured result" if not final else ""),
            "timeout": bool(run_result.get("timeout")),
            "events": events,
            "final": final,
            "last_message": _trim(run_result.get("last_message")),
            "provider_session_id": _trim(run_result.get("provider_session_id")),
            "llm_usage": dict(run_result.get("llm_usage") or {}),
            "duration_ms": int((time.time() - started_at) * 1000),
        }, provider=self.provider_name, stage=stage)

    async def _stream_query(
        self,
        *,
        prompt: str,
        system_prompt: str,
        output_schema: Mapping[str, Any],
        stage: str,
        cwd: Path,
        readable_roots: Iterable[Path],
        allowed_write_paths: frozenset[Path],
        event_sink: Optional[Callable[[Dict[str, Any]], None]],
        allow_writes: bool = True,
        skill_file: Optional[Path] = None,
        allow_asset_reads: bool = False,
        resume_session_id: str = "",
    ) -> Dict[str, Any]:
        query_impl, options_cls = self._sdk_bindings()
        events: List[Dict[str, Any]] = []
        stderr_lines: List[str] = []
        permission_denials: List[str] = []
        if not self._uses_native_structured_output():
            system_prompt = self._compatibility_output_prompt(system_prompt, output_schema)
        options = options_cls(**self._option_values(
            system_prompt=system_prompt,
            output_schema=output_schema,
            stage=stage,
            cwd=cwd,
            readable_roots=tuple(readable_roots),
            allowed_write_paths=allowed_write_paths,
            allow_writes=allow_writes,
            stderr_lines=stderr_lines,
            permission_denials=permission_denials,
            skill_file=skill_file,
            allow_asset_reads=allow_asset_reads,
            resume_session_id=resume_session_id,
        ))
        self._append_event(
            events,
            {
                "source": "harness",
                "type": "tool_call",
                "content": "claude sdk turn running",
                "metadata": {
                    "stage": stage,
                    "provider": self.provider_name,
                    "transport_provider": self.provider,
                    "model": self.model,
                    "effort": self.effort,
                    "complexity": self.complexity_level,
                    "web_search": self.capabilities.web_search.value if self.capabilities else "disabled",
                    "mcp_server_count": len(self.capabilities.mcp_servers) if self.capabilities else 0,
                },
            },
            event_sink,
        )
        iterator = query_impl(prompt=prompt, options=options).__aiter__()
        started = time.monotonic()
        final: Dict[str, Any] = {}
        last_message = ""
        usage: Dict[str, Any] = {}
        provider_session_id = ""
        timeout = False
        timeout_kind = ""
        timeout_after_seconds = 0
        error = ""
        failure_kind = ""
        saw_result = False
        stream_state: Dict[str, Any] = {
            "structured_indexes": set(),
            "seen_tool_use_ids": set(),
        }
        event_coalescer = AgentEventCoalescer()
        try:
            while True:
                remaining = self.hard_timeout_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    timeout = True
                    timeout_kind = "hard timeout"
                    timeout_after_seconds = self.hard_timeout_seconds
                    break
                try:
                    message = await asyncio.wait_for(
                        iterator.__anext__(),
                        timeout=min(float(self.timeout_seconds), remaining),
                    )
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    timeout = True
                    timeout_kind = "idle timeout" if remaining > self.timeout_seconds else "hard timeout"
                    timeout_after_seconds = self.timeout_seconds if timeout_kind == "idle timeout" else self.hard_timeout_seconds
                    break
                normalized, result = self._normalize_message(message, stage=stage, stream_state=stream_state)
                for event in normalized:
                    for ready_event in event_coalescer.push(event):
                        events.append(ready_event)
                        self._send_event(ready_event, event_sink)
                if result is None:
                    continue
                saw_result = True
                provider_session_id = _trim(result.get("session_id"))
                last_message = _trim(result.get("result"))
                usage = dict(result.get("usage") or {})
                subtype = _trim(result.get("subtype"))
                structured = result.get("structured_output")
                if subtype == "success" and not result.get("is_error") and isinstance(structured, Mapping):
                    final = self._with_protocol_defaults(dict(structured), output_schema)
                elif subtype == "success" and not result.get("is_error") and not self._uses_native_structured_output():
                    final, error = self._validated_compatibility_output(last_message, output_schema)
                else:
                    failure_kind = subtype if subtype and subtype != "success" else "missing_structured_output"
                    error = _trim(result.get("error")) or f"Claude structured output failed: {failure_kind}"
        except Exception as exc:
            error = self._safe_error(exc)
        finally:
            for ready_event in event_coalescer.flush():
                events.append(ready_event)
                self._send_event(ready_event, event_sink)
            close = getattr(iterator, "aclose", None)
            if callable(close):
                try:
                    await close()
                except Exception:
                    pass
        if timeout:
            error = f"claude sdk {timeout_kind} after {timeout_after_seconds}s"
        elif not saw_result and not error:
            error = "Claude provider stream ended without a result"
        if permission_denials:
            denial_event = {
                "source": "harness",
                "type": "tool_result",
                "content": "tool permission denied",
                "metadata": {
                    "stage": stage,
                    "provider": self.provider_name,
                    "is_error": True,
                    "reason": permission_denials[-1],
                },
            }
            events.append(denial_event)
            self._send_event(denial_event, event_sink)
        if error == "Claude structured output failed: missing_json_object" and last_message:
            recovery_event = {
                "source": "harness",
                "type": "tool_call",
                "content": "structured output recovery",
                "metadata": {"stage": stage, "provider": self.provider_name},
            }
            events.append(recovery_event)
            self._send_event(recovery_event, event_sink)
            try:
                recovered = await asyncio.to_thread(
                    self._recover_structured_output,
                    prompt,
                    last_message,
                    output_schema,
                )
                recovered = self._with_protocol_defaults(dict(recovered), output_schema)
                fastjsonschema.validate(dict(output_schema), recovered)
                final = recovered
                error = ""
                recovery_error = ""
            except Exception as exc:
                recovery_error = self._redact(_trim(exc))[:500]
            recovery_result = {
                "source": "harness",
                "type": "tool_result",
                "content": "structured output recovery completed" if not recovery_error else "structured output recovery failed",
                "metadata": {
                    "stage": stage,
                    "provider": self.provider_name,
                    "is_error": bool(recovery_error),
                    "error": recovery_error,
                },
            }
            events.append(recovery_result)
            self._send_event(recovery_result, event_sink)
        if error and permission_denials:
            error = f"{error}; tool permission denied: {permission_denials[-1]}"
        return {
            "events": events,
            "final": final,
            "error": error,
            "failure_kind": failure_kind,
            "timeout": timeout,
            "timeout_kind": timeout_kind,
            "timeout_after_seconds": timeout_after_seconds,
            "last_message": last_message,
            "provider_session_id": provider_session_id,
            "llm_usage": usage,
            "raw_stderr": "\n".join(stderr_lines[-50:]),
        }

    def _recover_structured_output(
        self,
        prompt: str,
        last_message: str,
        output_schema: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if self._structured_output_recovery is not None:
            return self._structured_output_recovery(prompt, last_message, output_schema)
        from src.utils.ai_service import chat_qwen_flash_structured

        content, _usage = chat_qwen_flash_structured(
            [
                {
                    "role": "system",
                    "content": (
                        "将已经完成的 Skill 回答整理为一个符合 JSON Schema 的 JSON 对象。"
                        "只整理原回答中的信息，不扩展任务，不输出解释。"
                        f"\nJSON Schema: {json.dumps(dict(output_schema), ensure_ascii=False)}"
                    ),
                },
                {
                    "role": "user",
                    "content": f"原任务：\n{prompt}\n\n已完成的回答：\n{last_message}",
                },
            ],
            enable_think=False,
            temperature=0.0,
        )
        payload = self._extract_json_object(content) or self._repair_json_object(content)
        if payload is None:
            raise ValueError("structured output recovery returned no JSON object")
        return payload

    def _option_values(
        self,
        *,
        system_prompt: str,
        output_schema: Mapping[str, Any],
        stage: str,
        cwd: Path,
        readable_roots: tuple[Path, ...],
        allowed_write_paths: frozenset[Path],
        allow_writes: bool,
        stderr_lines: List[str],
        permission_denials: Optional[List[str]] = None,
        skill_file: Optional[Path] = None,
        allow_asset_reads: bool = False,
        resume_session_id: str = "",
    ) -> Dict[str, Any]:
        coding = _trim(stage) == "coding" and allow_writes
        stage_name = _trim(stage)
        if stage_name == "flowchart":
            tools = ["Read"]
        elif stage_name == "design" and allow_asset_reads:
            tools = ["Read", "Grep"]
        elif stage_name == "coding":
            tools = ["Read", "Glob", "Grep"]
        else:
            tools = []
        if coding:
            tools.extend(["Edit", "Write", "Bash"])
        plugin, qualified_skill = self._enabled_native_skill_config(skill_file)
        web_search_enabled = bool(
            self.capabilities and self.capabilities.web_search != WebSearchPolicy.DISABLED
        )
        if web_search_enabled:
            tools.append("WebSearch")
        mcp_servers = self._claude_mcp_servers()
        allowed_tools = list(tools)
        if self.capabilities:
            allowed_tools.extend(self.capabilities.mcp_allowed_tools)
        disallowed_tools = ["WebFetch", "Task", "AskUserQuestion", "NotebookEdit"]
        if not web_search_enabled:
            disallowed_tools.append("WebSearch")
        values: Dict[str, Any] = {
            "tools": tools,
            "allowed_tools": allowed_tools,
            "disallowed_tools": disallowed_tools,
            "system_prompt": system_prompt,
            "mcp_servers": mcp_servers,
            "strict_mcp_config": True,
            "permission_mode": "dontAsk",
            "cwd": str(cwd),
            "setting_sources": [],
            "skills": [qualified_skill] if qualified_skill else [],
            "plugins": [plugin] if plugin else [],
            "include_partial_messages": True,
            "max_turns": self.max_turns,
            "model": self.model,
            "effort": self.effort or None,
            "env": self.provider_env(),
            "hooks": self._build_hooks(
                readable_roots=readable_roots,
                allowed_write_paths=allowed_write_paths,
                allow_bash=coding,
                cwd=cwd,
                permission_denials=permission_denials,
            ),
            "sandbox": {
                "enabled": coding,
                "autoAllowBashIfSandboxed": True,
                "allowUnsandboxedCommands": False,
            },
            "stderr": lambda line: stderr_lines.append(self._redact(_trim(line))[:2_000]),
        }
        if self.max_budget_usd > 0:
            values["max_budget_usd"] = self.max_budget_usd
        if _trim(resume_session_id):
            values["resume"] = _trim(resume_session_id)
        if self.thinking:
            values["thinking"] = {"type": self.thinking}
        if self._uses_native_structured_output():
            values["output_format"] = {"type": "json_schema", "schema": dict(output_schema)}
        else:
            values["output_format"] = None
        return values

    @staticmethod
    def _native_skill_config(skill_file: Optional[Path]) -> tuple[Optional[Dict[str, str]], str]:
        if skill_file is None:
            return None, ""
        skill_name = ""
        try:
            content = skill_file.read_text(encoding="utf-8")
            match = re.search(r"(?m)^name:\s*([^\n]+)$", content)
            skill_name = _trim(match.group(1)) if match else ""
        except OSError:
            return None, ""
        for parent in (skill_file.parent, *skill_file.parents):
            manifest = parent / ".claude-plugin" / "plugin.json"
            if not manifest.is_file():
                continue
            try:
                plugin_name = _trim(json.loads(manifest.read_text(encoding="utf-8")).get("name"))
            except (OSError, json.JSONDecodeError):
                return None, ""
            if plugin_name and skill_name:
                return {"type": "local", "path": str(parent)}, f"{plugin_name}:{skill_name}"
        return None, ""

    def _enabled_native_skill_config(self, skill_file: Optional[Path]) -> tuple[Optional[Dict[str, str]], str]:
        """Local plugins require the authenticated Anthropic Claude Code runtime.

        MaaS transports still run through Claude Agent SDK, but receive the same
        Skill inline so they do not depend on a separate Claude login.
        """
        if self.provider != "anthropic":
            return None, ""
        return self._native_skill_config(skill_file)

    def _claude_mcp_servers(self) -> Dict[str, Dict[str, Any]]:
        if not self.capabilities:
            return {}
        normalized: Dict[str, Dict[str, Any]] = {}
        for name, raw_config in self.capabilities.mcp_servers.items():
            config = dict(raw_config)
            config.pop("allowed_tools", None)
            config.pop("enabled", None)
            if config.get("type") == "streamable_http":
                config["type"] = "http"
            env_names = config.pop("env_vars", ())
            if env_names and "command" in config:
                server_env = dict(config.get("env") or {})
                for env_name in env_names:
                    key = _trim(env_name)
                    if key and os.environ.get(key):
                        server_env[key] = os.environ[key]
                if server_env:
                    config["env"] = server_env
            bearer_env = _trim(config.pop("bearer_token_env_var", ""))
            env_http_headers = dict(config.pop("env_http_headers", {}) or {})
            resolved_headers = {}
            for header_name, env_name in env_http_headers.items():
                header = _trim(header_name)
                value = _trim(os.environ.get(_trim(env_name)))
                if header and value:
                    resolved_headers[header] = value
            if bearer_env:
                token = _trim(os.environ.get(bearer_env))
                if token:
                    headers = dict(resolved_headers)
                    headers.setdefault("Authorization", f"Bearer {token}")
                    config["headers"] = headers
            elif resolved_headers:
                config["headers"] = resolved_headers
            normalized[name] = config
        return normalized

    def _uses_native_structured_output(self) -> bool:
        return self.provider not in {"dashscope", "deepseek"}

    @staticmethod
    def _compatibility_output_prompt(system_prompt: str, output_schema: Mapping[str, Any]) -> str:
        schema_json = json.dumps(dict(output_schema), ensure_ascii=False, separators=(",", ":"))
        instruction = (
            "The current Anthropic-compatible transport does not expose the SDK StructuredOutput tool. "
            "Return exactly one JSON object matching the following JSON Schema. Do not wrap it in markdown "
            f"or add explanatory text. JSON Schema: {schema_json}"
        )
        return f"{system_prompt.rstrip()}\n\n{instruction}" if system_prompt.strip() else instruction

    @classmethod
    def _validated_compatibility_output(
        cls,
        value: str,
        output_schema: Mapping[str, Any],
    ) -> tuple[Dict[str, Any], str]:
        candidate = cls._extract_json_object(value)
        validation_error = ""
        if candidate is not None:
            candidate = cls._with_protocol_defaults(candidate, output_schema)
            try:
                fastjsonschema.validate(dict(output_schema), candidate)
                return candidate, ""
            except (fastjsonschema.JsonSchemaException, ValueError, TypeError) as exc:
                validation_error = cls._redact(_trim(exc))[:500]

        repaired = cls._repair_json_object(value)
        if repaired is not None:
            repaired = cls._with_protocol_defaults(repaired, output_schema)
            try:
                fastjsonschema.validate(dict(output_schema), repaired)
                return repaired, ""
            except (fastjsonschema.JsonSchemaException, ValueError, TypeError) as exc:
                validation_error = cls._redact(_trim(exc))[:500]

        if validation_error:
            return {}, f"Claude structured output schema validation failed: {validation_error}"
        return {}, "Claude structured output failed: missing_json_object"

    @staticmethod
    def _with_protocol_defaults(value: Dict[str, Any], output_schema: Mapping[str, Any]) -> Dict[str, Any]:
        result = dict(value)
        properties = output_schema.get("properties")
        properties = properties if isinstance(properties, Mapping) else {}
        if "source" in properties:
            result.setdefault("source", "model")
        if "type" in properties:
            result.setdefault("type", "final")
        return result

    @staticmethod
    def _extract_json_object(value: str) -> Optional[Dict[str, Any]]:
        text = _trim(value)
        if not text:
            return None
        decoder = json.JSONDecoder()
        for index, character in enumerate(text):
            if character != "{":
                continue
            try:
                parsed, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, Mapping):
                return dict(parsed)
        return None

    @staticmethod
    def _repair_json_object(value: str) -> Optional[Dict[str, Any]]:
        text = _trim(value)
        if not text:
            return None
        fenced = re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
        for fragment in [*fenced, text]:
            try:
                parsed = repair_json(fragment)
            except (ValueError, TypeError):
                continue
            if isinstance(parsed, Mapping):
                return dict(parsed)
        return None

    def provider_env(self) -> Dict[str, str]:
        env = {"CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1"}
        if self.capabilities and self.capabilities.mcp_servers:
            env["ENABLE_TOOL_SEARCH"] = "true"
        if self.base_url:
            env["ANTHROPIC_BASE_URL"] = self.base_url
        if self.provider == "deepseek":
            token = _trim(os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("CLAUDE_AUTH_TOKEN"))
            if token:
                env["ANTHROPIC_AUTH_TOKEN"] = token
        elif self.provider == "dashscope":
            token = _trim(
                os.environ.get("DASHSCOPE_API_KEY")
                or os.environ.get("LLM_API_KEY")
                or os.environ.get("LLM_KEY")
                or os.environ.get("CLAUDE_AUTH_TOKEN")
            )
            if token:
                env["ANTHROPIC_AUTH_TOKEN"] = token
        else:
            api_key = _trim(os.environ.get("ANTHROPIC_API_KEY"))
            auth_token = _trim(os.environ.get("CLAUDE_AUTH_TOKEN"))
            if api_key:
                env["ANTHROPIC_API_KEY"] = api_key
            elif auth_token:
                env["ANTHROPIC_AUTH_TOKEN"] = auth_token
        if self.provider in {"deepseek", "dashscope"}:
            env.update({
                "ANTHROPIC_MODEL": self.model,
                "ANTHROPIC_DEFAULT_OPUS_MODEL": self.model,
                "ANTHROPIC_DEFAULT_SONNET_MODEL": self.model,
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": self.model,
                "CLAUDE_CODE_SUBAGENT_MODEL": self.model,
            })
        return env

    def _resolve_base_url(self, configured: str) -> str:
        supplied = _trim(configured or os.environ.get("CLAUDE_BASE_URL"))
        expected = {
            "deepseek": DEEPSEEK_ANTHROPIC_BASE_URL,
        }.get(self.provider, "")
        if expected and supplied and supplied.rstrip("/") != expected:
            raise ValueError(f"{self.provider} credentials may only use {expected}")
        if expected:
            return expected
        if self.provider == "dashscope":
            endpoint = supplied or DASHSCOPE_ANTHROPIC_BASE_URL
            if not self._is_trusted_dashscope_endpoint(endpoint):
                raise ValueError("dashscope credentials require an official Alibaba Cloud Anthropic endpoint")
            return endpoint.rstrip("/")
        if self.provider == "gateway" and not supplied:
            raise ValueError("CLAUDE_BASE_URL is required for gateway provider")
        return supplied.rstrip("/")

    @staticmethod
    def _is_trusted_dashscope_endpoint(value: str) -> bool:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        trusted_host = host in {
            "dashscope.aliyuncs.com",
            "dashscope-us.aliyuncs.com",
            "coding.dashscope.aliyuncs.com",
            "coding-intl.dashscope.aliyuncs.com",
            "token-plan.cn-beijing.maas.aliyuncs.com",
        } or host.endswith(".maas.aliyuncs.com")
        return parsed.scheme == "https" and trusted_host and parsed.path.rstrip("/") == "/apps/anthropic"

    def _sdk_bindings(self) -> tuple[Callable[..., AsyncIterator[Any]], Any]:
        if self._query_impl is not None:
            try:
                from claude_agent_sdk import ClaudeAgentOptions
            except Exception:
                class ClaudeAgentOptions:  # type: ignore[no-redef]
                    def __init__(self, **kwargs: Any) -> None:
                        self.__dict__.update(kwargs)
            return self._query_impl, ClaudeAgentOptions
        from claude_agent_sdk import ClaudeAgentOptions, query

        return query, ClaudeAgentOptions

    @staticmethod
    def _build_hooks(
        *,
        readable_roots: tuple[Path, ...],
        allowed_write_paths: frozenset[Path],
        allow_bash: bool,
        cwd: Path,
        permission_denials: Optional[List[str]] = None,
    ) -> Dict[str, List[Any]]:
        try:
            from claude_agent_sdk import HookMatcher
        except Exception:
            class HookMatcher:  # type: ignore[no-redef]
                def __init__(self, *, matcher: Any, hooks: List[Any]) -> None:
                    self.matcher = matcher
                    self.hooks = hooks

        async def pre_tool_use(input_data: Mapping[str, Any], tool_use_id: str | None, context: Any) -> Dict[str, Any]:
            tool_name = _trim(input_data.get("tool_name"))
            tool_input = input_data.get("tool_input") if isinstance(input_data.get("tool_input"), Mapping) else {}
            reason = ClaudeSdkSkillHarness._tool_denial_reason(
                tool_name=tool_name,
                tool_input=tool_input,
                readable_roots=readable_roots,
                allowed_write_paths=allowed_write_paths,
                allow_bash=allow_bash,
                cwd=cwd,
            )
            if not reason:
                return {}
            if permission_denials is not None and reason not in permission_denials:
                permission_denials.append(reason)
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }

        return {"PreToolUse": [HookMatcher(matcher=None, hooks=[pre_tool_use])]}

    @staticmethod
    def _tool_denial_reason(
        *,
        tool_name: str,
        tool_input: Mapping[str, Any],
        readable_roots: tuple[Path, ...],
        allowed_write_paths: frozenset[Path],
        allow_bash: bool,
        cwd: Path,
    ) -> str:
        if tool_name in _MUTATING_TOOLS:
            path = ClaudeSdkSkillHarness._tool_path(tool_input, cwd=cwd)
            scratch_root = (cwd / "scratch").resolve()
            if path in allowed_write_paths or path == scratch_root or path.is_relative_to(scratch_root):
                return ""
            return "write target is outside the editable modules and isolated scratch directory"
        if tool_name in _READ_TOOLS:
            if tool_name == "Glob":
                pattern = _trim(tool_input.get("pattern"))
                if ".." in Path(pattern).parts or Path(pattern).is_absolute():
                    return "glob pattern is outside the run context"
            path = ClaudeSdkSkillHarness._tool_path(tool_input, cwd=cwd, optional=True)
            if path is None:
                return ""
            return "" if any(path == root or path.is_relative_to(root) for root in readable_roots) else "read target is outside the run context"
        if tool_name == "Bash":
            command = _trim(tool_input.get("command"))
            if not allow_bash:
                return "shell execution is disabled for this stage"
            if tool_input.get("dangerouslyDisableSandbox"):
                return "unsandboxed shell execution is disabled"
            return coding_command_denial_reason(
                command,
                cwd=cwd,
                allowed_module_paths=allowed_write_paths,
            )
        return "tool is outside this stage's capability profile"

    @staticmethod
    def _tool_path(tool_input: Mapping[str, Any], *, cwd: Path, optional: bool = False) -> Optional[Path]:
        raw = _trim(tool_input.get("file_path") or tool_input.get("path"))
        if not raw:
            return None if optional else cwd.parent / "__missing_path__"
        path = Path(raw)
        return (path if path.is_absolute() else cwd / path).resolve()

    @staticmethod
    def _allowed_write_paths(bundle: Mapping[str, Any], cwd: Path) -> frozenset[Path]:
        workspace = bundle.get("coding_workspace") if isinstance(bundle.get("coding_workspace"), Mapping) else {}
        return frozenset(
            (cwd / _trim(path)).resolve()
            for path in workspace.get("module_files") or []
            if _trim(path) and (cwd / _trim(path)).resolve().is_relative_to(cwd)
        )

    @staticmethod
    def _normalize_message(
        message: Any,
        *,
        stage: str,
        stream_state: Optional[Dict[str, Any]] = None,
    ) -> tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        class_name = type(message).__name__
        metadata = {"stage": stage, "provider": "claude"}
        state = stream_state if isinstance(stream_state, dict) else {}
        structured_indexes = state.setdefault("structured_indexes", set())
        seen_tool_use_ids = state.setdefault("seen_tool_use_ids", set())
        if class_name == "StreamEvent":
            raw = getattr(message, "event", {})
            raw = raw if isinstance(raw, Mapping) else {}
            event_type = _trim(raw.get("type"))
            delta = raw.get("delta") if isinstance(raw.get("delta"), Mapping) else {}
            delta_type = _trim(delta.get("type"))
            if event_type == "content_block_delta" and delta_type == "text_delta":
                text = str(delta.get("text") or "")
                return ([{"source": "claude", "type": "agent_delta", "content": text, "metadata": metadata}] if text else []), None
            index = int(raw.get("index") or 0)
            if event_type == "content_block_delta" and delta_type == "input_json_delta" and index in structured_indexes:
                chunk = str(delta.get("partial_json") or "")
                return ([{"source": "claude", "type": "agent_delta", "content": chunk, "metadata": metadata}] if chunk else []), None
            if event_type == "content_block_delta" and delta_type in {"thinking_delta", "signature_delta"}:
                text = str(delta.get("thinking") or delta.get("text") or "")
                return ([{"source": "claude", "type": "reasoning_delta", "content": text, "metadata": metadata}] if text else []), None
            if event_type == "content_block_start":
                block = raw.get("content_block") if isinstance(raw.get("content_block"), Mapping) else {}
                if _trim(block.get("type")) in {"tool_use", "server_tool_use"}:
                    if _trim(block.get("name")) == "StructuredOutput":
                        structured_indexes.add(index)
                    tool_use_id = _trim(block.get("id"))
                    if tool_use_id:
                        seen_tool_use_ids.add(tool_use_id)
                    return [{"source": "harness", "type": "tool_call", "content": _trim(block.get("name")) or "tool call", "metadata": {**metadata, "tool_use_id": tool_use_id}}], None
            if event_type == "content_block_stop":
                structured_indexes.discard(index)
            return [], None
        if class_name == "SystemMessage":
            subtype = _trim(getattr(message, "subtype", ""))
            data = getattr(message, "data", {})
            data = data if isinstance(data, Mapping) else {}
            if subtype == "init":
                return [{"source": "harness", "type": "turn_started", "content": "turn started", "metadata": {**metadata, "provider_session_id": _trim(data.get("session_id") or data.get("sessionId")), "model": _trim(data.get("model"))}}], None
            return [], None
        if class_name == "AssistantMessage":
            events = []
            for block in getattr(message, "content", []) or []:
                if type(block).__name__ == "ToolUseBlock":
                    tool_use_id = _trim(getattr(block, "id", ""))
                    if tool_use_id and tool_use_id in seen_tool_use_ids:
                        continue
                    if tool_use_id:
                        seen_tool_use_ids.add(tool_use_id)
                    events.append({"source": "harness", "type": "tool_call", "content": _trim(getattr(block, "name", "")) or "tool call", "metadata": {**metadata, "tool_use_id": tool_use_id}})
            return events, None
        if class_name == "UserMessage":
            events = []
            for block in getattr(message, "content", []) or []:
                if type(block).__name__ == "ToolResultBlock":
                    events.append({"source": "harness", "type": "tool_result", "content": "tool completed", "metadata": {**metadata, "tool_use_id": _trim(getattr(block, "tool_use_id", "")), "is_error": bool(getattr(block, "is_error", False))}})
            return events, None
        if class_name == "ResultMessage":
            result = {
                "subtype": _trim(getattr(message, "subtype", "")),
                "is_error": bool(getattr(message, "is_error", False)),
                "result": _trim(getattr(message, "result", "")),
                "structured_output": getattr(message, "structured_output", None),
                "session_id": _trim(getattr(message, "session_id", "")),
                "usage": getattr(message, "usage", {}) if isinstance(getattr(message, "usage", {}), Mapping) else {},
                "error": _trim(getattr(message, "result", "")) if bool(getattr(message, "is_error", False)) else "",
            }
            return [{"source": "harness", "type": "turn_completed", "content": "turn completed", "metadata": {**metadata, "provider_session_id": result["session_id"], "subtype": result["subtype"]}}], result
        if class_name == "RateLimitEvent":
            return [{"source": "harness", "type": "error", "content": "provider rate limit", "metadata": metadata}], None
        return [], None

    def _run_async_sync(self, coroutine: Any) -> Dict[str, Any]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coroutine)
        result: Dict[str, Any] = {}
        failure: List[BaseException] = []

        def runner() -> None:
            try:
                result.update(asyncio.run(coroutine))
            except BaseException as exc:  # pragma: no cover - defensive cross-thread propagation
                failure.append(exc)

        thread = threading.Thread(target=runner, name="claude-sdk-runner", daemon=True)
        thread.start()
        thread.join(self.hard_timeout_seconds + 5)
        if thread.is_alive():
            return {"final": {}, "events": [], "error": "claude sdk bridge hard timeout", "timeout": True, "timeout_kind": "hard timeout", "timeout_after_seconds": self.hard_timeout_seconds}
        if failure:
            return {"final": {}, "events": [], "error": self._safe_error(failure[0])}
        return result

    @staticmethod
    def _safe_error(exc: BaseException) -> str:
        return ClaudeSdkSkillHarness._redact(_trim(exc) or type(exc).__name__)[:1_000]

    @staticmethod
    def _redact(value: str) -> str:
        text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED]", value)
        return re.sub(
            r"(?i)(authorization|api[_-]?key|token|secret|password)(\s*[:=]\s*)([^\s,;}]+)",
            r"\1\2[REDACTED]",
            text,
        )

    @staticmethod
    def _failure_result(error: str, *, session_id: str, stage: str = "") -> Dict[str, Any]:
        return normalize_agent_run_result(
            {"ok": False, "error": error, "events": [], "final": {}, "session_id": session_id},
            provider="claude",
            stage=stage,
            session_id=session_id,
        )

    @classmethod
    def _unavailable_result(cls, *, session_id: str, stage: str = "") -> Dict[str, Any]:
        return cls._failure_result("`claude-agent-sdk` is not available", session_id=session_id, stage=stage)
