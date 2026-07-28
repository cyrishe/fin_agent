from __future__ import annotations

import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, Dict, Mapping, Optional
import uuid

from src.services.agent_providers.claude import ClaudeSdkSkillHarness


def _trim(value: Any) -> str:
    return str(value or "").strip()


def _canonical_skill_id(value: Any) -> str:
    normalized = _trim(value)
    return normalized.rsplit(":", 1)[-1] if normalized else ""


def _append_runtime_context(prompt: str, runtime: Any) -> str:
    builder = getattr(runtime, "current_context_prompt", None)
    if not callable(builder):
        return prompt
    try:
        context = _trim(builder())
    except Exception:
        return prompt
    if not context:
        return prompt
    return f"{prompt}\n\n[系统提供的当前运行时索引]\n{context}"


@dataclass
class _LiveClaudeClient:
    client: Any
    tool_runtime: Any
    options_fingerprint: str
    last_used: float
    busy: bool = False
    eviction_handle: Any = None


class FinanceClaudeSessionService:
    """Run a provider-backed Finance CC conversation without owning routing or DB state."""

    def __init__(
        self,
        *,
        enabled: Optional[bool] = None,
        provider: str = "",
        model: str = "",
        root_dir: str | Path = "data/finance_cc_sessions",
        log_path: str | Path = "outputs/finance_cc_shadow/events.jsonl",
        max_workers: int = 2,
        client_idle_seconds: Optional[float] = None,
        max_live_clients: Optional[int] = None,
        turn_runner: Optional[Callable[..., Dict[str, Any]]] = None,
        system_tools: Any = None,
        system_prompt_path: str | Path | None = None,
        skill_root: str | Path | None = None,
        skill_names: Optional[list[str]] = None,
        runtime_scope_prefix: str = "",
        max_turns: Optional[int] = None,
        system_context_paths: Optional[list[str | Path]] = None,
        effort: str = "",
    ) -> None:
        enabled_text = _trim(os.environ.get("FINANCE_CC_SHADOW_ENABLED")).lower()
        self.enabled = bool(enabled) if enabled is not None else enabled_text in {"1", "true", "yes", "on"}
        self.provider = _trim(provider or os.environ.get("FINANCE_CC_PROVIDER") or "dashscope").lower()
        self.model = _trim(model or os.environ.get("FINANCE_CC_MODEL") or "deepseek-v4-flash")
        self.root_dir = Path(root_dir)
        self.log_path = Path(log_path)
        self.system_prompt_path = Path(system_prompt_path or "src/prompts/finance_cc/main.system.md")
        self.skill_root = Path(skill_root or "src/skills/financial-tool-development")
        self.skill_names = tuple(
            skill_names
            if skill_names is not None
            else [
                "fin-agent-financial-tools:financial-tool-requirement",
                "fin-agent-financial-tools:financial-tool-design",
                "fin-agent-financial-tools:financial-tool-flowchart",
            ]
        )
        self.runtime_scope_prefix = _trim(runtime_scope_prefix)
        self.max_turns = max(2, int(max_turns)) if max_turns is not None else None
        self.system_context_paths = tuple(
            Path(item) for item in (system_context_paths or [])
        )
        self.effort = _trim(effort)
        self._turn_runner = turn_runner or self._run_client_turn
        self.system_tools = system_tools
        self._executor = ThreadPoolExecutor(max_workers=max(1, int(max_workers)), thread_name_prefix="finance-cc-shadow")
        self._locks_guard = threading.Lock()
        self._session_locks: dict[str, threading.Lock] = {}
        self._log_lock = threading.Lock()
        self._futures_guard = threading.Lock()
        self._futures: set[Future] = set()
        self.client_idle_seconds = max(
            0.05,
            float(
                client_idle_seconds
                if client_idle_seconds is not None
                else os.environ.get("FINANCE_CC_CLIENT_IDLE_SECONDS") or 300
            ),
        )
        self.max_live_clients = max(
            1,
            int(
                max_live_clients
                if max_live_clients is not None
                else os.environ.get("FINANCE_CC_MAX_LIVE_CLIENTS") or max(1, int(max_workers))
            ),
        )
        self._runtime_guard = threading.Lock()
        self._runtime_ready = threading.Event()
        self._runtime_loop: Optional[asyncio.AbstractEventLoop] = None
        self._runtime_thread: Optional[threading.Thread] = None
        self._live_clients: dict[str, _LiveClaudeClient] = {}

    def submit(
        self,
        *,
        thread_id: int | str,
        owner_id: str,
        user_text: str,
        context: Optional[Mapping[str, Any]] = None,
        turn_id: int | str = "",
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> bool:
        if not self.enabled or not _trim(user_text):
            return False
        future = self._executor.submit(
            self.run_turn,
            thread_id=thread_id,
            owner_id=owner_id,
            user_text=user_text,
            context=context,
            turn_id=turn_id,
            event_sink=event_sink,
        )
        with self._futures_guard:
            self._futures.add(future)
        future.add_done_callback(self._finish_future)
        return True

    def run_turn(
        self,
        *,
        thread_id: int | str,
        owner_id: str,
        user_text: str,
        context: Optional[Mapping[str, Any]] = None,
        turn_id: int | str = "",
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        key = self._session_key(thread_id=thread_id, owner_id=owner_id)
        with self._session_lock(key), self._process_session_lock(self._session_dir(key)):
            session_dir = self._session_dir(key)
            marker_path = session_dir / "session.json"
            session_dir.mkdir(parents=True, exist_ok=True)
            marker: Dict[str, Any] = {}
            if marker_path.is_file():
                try:
                    loaded_marker = json.loads(marker_path.read_text(encoding="utf-8"))
                    marker = loaded_marker if isinstance(loaded_marker, dict) else {}
                except (OSError, json.JSONDecodeError):
                    marker = {}
            generation = max(0, int(marker.get("generation") or 0))
            session_id = _trim(marker.get("session_id")) or self._session_id(key, generation=generation)
            resumed = bool(marker.get("resumable", bool(marker))) and bool(_trim(marker.get("session_id")))
            prompt = self.build_user_prompt(user_text, context or {})
            runtime_tool_context = dict(context or {})
            runtime_tool_context["_agent_runtime_scope"] = (
                f"{self.runtime_scope_prefix}:{key}" if self.runtime_scope_prefix else key
            )
            started_at = time.monotonic()
            try:
                result = self._turn_runner(
                    prompt=prompt,
                    session_id=session_id,
                    resume=resumed,
                    session_dir=session_dir,
                    owner_id=owner_id,
                    tool_context=runtime_tool_context,
                    event_sink=event_sink,
                )
                # The SDK session owns its history. A completed SDK turn remains
                # resumable even when its textual result reports an error; that
                # feedback belongs to the next LLM turn rather than a Python
                # branch which discards the conversation.
                marker_path.write_text(
                    json.dumps(
                        {"session_id": session_id, "generation": generation, "resumable": True},
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                record = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "thread_id": str(thread_id),
                    "turn_id": str(turn_id or ""),
                    "session_id": session_id,
                    "resumed": resumed,
                    "duration_ms": round((time.monotonic() - started_at) * 1_000),
                    "stream_event_count": int(result.get("stream_event_count") or 0),
                    "text_delta_count": int(result.get("text_delta_count") or 0),
                    "client_reused": bool(result.get("client_reused")),
                    "result": _trim(result.get("result"))[:2_000],
                    "error": _trim(result.get("error"))[:500],
                    "tool_calls": [dict(item) for item in result.get("tool_calls") or [] if isinstance(item, Mapping)],
                    "agent_tool_names": [_trim(item) for item in result.get("agent_tool_names") or [] if _trim(item)],
                    "skill_results": [
                        _trim(item)[:5_000] for item in result.get("skill_results") or [] if _trim(item)
                    ],
                    "skill_entries": [
                        dict(item)
                        for item in result.get("skill_entries") or []
                        if isinstance(item, Mapping)
                    ],
                    "interaction_requests": [
                        dict(item) for item in result.get("interaction_requests") or [] if isinstance(item, Mapping)
                    ],
                    "artifact_updates": [
                        dict(item) for item in result.get("artifact_updates") or [] if isinstance(item, Mapping)
                    ],
                    "asset_reads": [dict(item) for item in result.get("asset_reads") or [] if isinstance(item, Mapping)],
                    "dynamic_runs": [dict(item) for item in result.get("dynamic_runs") or [] if isinstance(item, Mapping)],
                    "implementation_runs": [
                        dict(item) for item in result.get("implementation_runs") or [] if isinstance(item, Mapping)
                    ],
                    "result_refs": [
                        dict(item) for item in result.get("result_refs") or [] if isinstance(item, Mapping)
                    ],
                }
                record["ok"] = not bool(record["error"])
            except Exception as exc:
                next_generation = generation + 1
                marker_path.write_text(
                    json.dumps(
                        {
                            "session_id": self._session_id(key, generation=next_generation),
                            "generation": next_generation,
                            "resumable": False,
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                record = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "thread_id": str(thread_id),
                    "turn_id": str(turn_id or ""),
                    "session_id": session_id,
                    "resumed": resumed,
                    "duration_ms": round((time.monotonic() - started_at) * 1_000),
                    "stream_event_count": 0,
                    "text_delta_count": 0,
                    "result": "",
                    "error": f"{type(exc).__name__}: {str(exc)[:400]}",
                    "ok": False,
                }
            self._append_record(record)
            return record

    @staticmethod
    def build_user_prompt(user_text: str, context: Mapping[str, Any]) -> str:
        lines = [f"用户当前的问题是：\n{_trim(user_text)}"]
        ui_action = context.get("ui_action") if isinstance(context.get("ui_action"), Mapping) else {}
        action_label = _trim(ui_action.get("label") or ui_action.get("action_id"))
        if action_label:
            lines.append(f"用户同时通过界面提交了：{action_label}。")
        lines.append("请结合当前会话历史和按需读取的系统资产，处理本轮新增信息。")
        return "\n\n".join(lines)

    def drain(self, timeout: float = 30.0) -> list[Dict[str, Any]]:
        with self._futures_guard:
            futures = list(self._futures)
        results = []
        deadline = time.monotonic() + max(0.0, timeout)
        for future in futures:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                value = future.result(timeout=remaining)
            except Exception:
                continue
            if isinstance(value, Mapping):
                results.append(dict(value))
        return results

    def close(self) -> None:
        loop = self._runtime_loop
        thread = self._runtime_thread
        if loop is not None and loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(self._close_live_clients(), loop)
                future.result(timeout=10)
            except Exception:
                pass
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=5)
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _ensure_runtime_loop(self) -> asyncio.AbstractEventLoop:
        with self._runtime_guard:
            if (
                self._runtime_loop is not None
                and self._runtime_loop.is_running()
                and self._runtime_thread is not None
                and self._runtime_thread.is_alive()
            ):
                return self._runtime_loop
            self._runtime_ready.clear()

            def run_loop() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._runtime_loop = loop
                self._runtime_ready.set()
                loop.run_forever()
                loop.close()

            self._runtime_thread = threading.Thread(
                target=run_loop,
                name="finance-cc-client-runtime",
                daemon=True,
            )
            self._runtime_thread.start()
        if not self._runtime_ready.wait(timeout=5):
            raise RuntimeError("Finance CC client runtime did not start")
        if self._runtime_loop is None:
            raise RuntimeError("Finance CC client runtime is unavailable")
        return self._runtime_loop

    @staticmethod
    def _empty_tracker() -> Dict[str, Any]:
        return {
            "calls": [],
            "interaction_requests": [],
            "artifact_updates": [],
            "asset_reads": [],
            "dynamic_runs": [],
            "implementation_runs": [],
            "result_refs": [],
        }

    async def _make_pool_room(self) -> None:
        while len(self._live_clients) >= self.max_live_clients:
            candidates = [
                (session_id, entry)
                for session_id, entry in self._live_clients.items()
                if not entry.busy
            ]
            if not candidates:
                return
            session_id, entry = min(candidates, key=lambda item: item[1].last_used)
            await self._discard_live_client(session_id, expected=entry)

    async def _discard_live_client(
        self,
        session_id: str,
        *,
        expected: Optional[_LiveClaudeClient] = None,
    ) -> None:
        entry = self._live_clients.get(session_id)
        if entry is None or (expected is not None and entry is not expected):
            return
        self._live_clients.pop(session_id, None)
        if entry.eviction_handle is not None:
            entry.eviction_handle.cancel()
            entry.eviction_handle = None
        try:
            await asyncio.wait_for(entry.client.disconnect(), timeout=5)
        except Exception:
            pass

    def _schedule_idle_eviction(self, session_id: str, entry: _LiveClaudeClient) -> None:
        expected_last_used = entry.last_used
        loop = asyncio.get_running_loop()

        def expire() -> None:
            asyncio.create_task(
                self._expire_idle_client(
                    session_id,
                    entry=entry,
                    expected_last_used=expected_last_used,
                )
            )

        entry.eviction_handle = loop.call_later(self.client_idle_seconds, expire)

    async def _expire_idle_client(
        self,
        session_id: str,
        *,
        entry: _LiveClaudeClient,
        expected_last_used: float,
    ) -> None:
        current = self._live_clients.get(session_id)
        if (
            current is not entry
            or current.busy
            or current.last_used != expected_last_used
        ):
            return
        await self._discard_live_client(session_id, expected=entry)

    async def _close_live_clients(self) -> None:
        for session_id, entry in list(self._live_clients.items()):
            await self._discard_live_client(session_id, expected=entry)

    def _run_client_turn(
        self,
        *,
        prompt: str,
        session_id: str,
        resume: bool,
        session_dir: Path,
        owner_id: str,
        tool_context: Mapping[str, Any],
        event_sink: Optional[Callable[[Dict[str, Any]], None]],
    ) -> Dict[str, Any]:
        loop = self._ensure_runtime_loop()
        future = asyncio.run_coroutine_threadsafe(
            self._run_client_turn_async(
                prompt=prompt,
                session_id=session_id,
                resume=resume,
                session_dir=session_dir,
                owner_id=owner_id,
                tool_context=tool_context,
                event_sink=event_sink,
            ),
            loop,
        )
        return future.result()

    async def _run_client_turn_async(
        self,
        *,
        prompt: str,
        session_id: str,
        resume: bool,
        session_dir: Path,
        owner_id: str,
        tool_context: Mapping[str, Any],
        event_sink: Optional[Callable[[Dict[str, Any]], None]],
    ) -> Dict[str, Any]:
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

        system_prompt_parts = [
            self.system_prompt_path.read_text(encoding="utf-8").strip()
        ]
        for context_path in self.system_context_paths:
            system_prompt_parts.append(
                context_path.read_text(encoding="utf-8").strip()
            )
        agent_system_prompt = _trim(tool_context.get("_agent_system_prompt"))
        if agent_system_prompt:
            system_prompt_parts.append(agent_system_prompt)
        finance_skill_catalog_prompt = _trim(
            tool_context.get("_finance_skill_catalog_prompt")
        )
        if finance_skill_catalog_prompt:
            system_prompt_parts.append(
                "\n".join(
                    [
                        "[当前可用的金融业务 Skill 摘要]",
                        "这些摘要用于本轮语义选择；匹配专业任务时先加载 Skill，"
                        "单一事实查询或概念解释不强行加载。",
                        finance_skill_catalog_prompt,
                    ]
                )
            )
        system_prompt = "\n\n".join(
            item for item in system_prompt_parts if item
        )
        effort = self.effort or _trim(os.environ.get("FINANCE_CC_EFFORT") or "low")
        max_turns = self.max_turns or max(
            2,
            int(os.environ.get("FINANCE_CC_MAX_TURNS") or 12),
        )
        effective_skill_names = self._effective_skill_names(tool_context)
        options_fingerprint = hashlib.sha256(
            "\0".join(
                [
                    self.provider,
                    self.model,
                    effort,
                    str(max_turns),
                    "tools" if self.system_tools is not None else "no-tools",
                    str(self.skill_root),
                    *effective_skill_names,
                    *[
                        _trim(item)
                        for item in tool_context.get("allowed_agent_tools") or []
                        if _trim(item)
                    ],
                    system_prompt,
                ]
            ).encode("utf-8")
        ).hexdigest()
        entry = self._live_clients.get(session_id)
        if entry is not None and entry.options_fingerprint != options_fingerprint:
            await self._discard_live_client(session_id, expected=entry)
            entry = None
            resume = True
        reused_live_client = entry is not None
        if entry is None:
            await self._make_pool_room()
            provider_env = ClaudeSdkSkillHarness(
                provider=self.provider,
                model=self.model,
                query_impl=lambda **_: None,
            ).provider_env()
            provider_env.update(
                {
                    "CLAUDE_CONFIG_DIR": str(session_dir / "claude"),
                    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
                    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                    "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1",
                }
            )
            if not provider_env.get("ANTHROPIC_AUTH_TOKEN") and not provider_env.get("ANTHROPIC_API_KEY"):
                raise RuntimeError(f"missing credential for Finance CC provider {self.provider}")
            mcp_servers: dict[str, Any] = {}
            allowed_tools: list[str] = []
            tool_runtime = None
            tracker = self._empty_tracker()
            if self.system_tools is not None:
                from claude_agent_sdk import create_sdk_mcp_server

                runtime_factory = getattr(self.system_tools, "create_runtime", None)
                if callable(runtime_factory):
                    tool_runtime = runtime_factory()
                else:
                    from src.services.finance_cc_system_tools import FinanceCcToolRuntime

                    tool_runtime = FinanceCcToolRuntime()
                tools, allowed_tools, tracker = self.system_tools.build_tools(
                    owner_ids=[owner_id] if owner_id else [],
                    tool_context=tool_context,
                    event_sink=event_sink,
                    runtime=tool_runtime,
                )
                mcp_servers["finance"] = create_sdk_mcp_server(name="finance", version="1.0.0", tools=tools)
            skill_root = self.skill_root.resolve()
            options = ClaudeAgentOptions(
                tools=["Skill"] if effective_skill_names else [],
                allowed_tools=["Skill", *allowed_tools] if effective_skill_names else allowed_tools,
                disallowed_tools=[
                    "Bash", "Edit", "Write", "Read", "Glob", "Grep", "WebFetch", "WebSearch",
                    "Agent", "Task", "AskUserQuestion", "NotebookEdit",
                ],
                system_prompt=system_prompt,
                mcp_servers=mcp_servers,
                strict_mcp_config=bool(mcp_servers),
                permission_mode="dontAsk",
                setting_sources=[],
                plugins=[{"type": "local", "path": str(skill_root)}] if effective_skill_names else [],
                skills=list(effective_skill_names),
                cwd=str(session_dir),
                include_partial_messages=True,
                max_turns=max_turns,
                model=self.model,
                effort=effort,
                resume=session_id if resume else None,
                session_id=None if resume else session_id,
                env=provider_env,
            )
            client = ClaudeSDKClient(options=options)
            try:
                await client.connect()
            except Exception:
                try:
                    await client.disconnect()
                except Exception:
                    pass
                raise
            entry = _LiveClaudeClient(
                client=client,
                tool_runtime=tool_runtime,
                options_fingerprint=options_fingerprint,
                last_used=time.monotonic(),
            )
            self._live_clients[session_id] = entry
        else:
            tracker = (
                entry.tool_runtime.begin_turn(
                    owner_ids=[owner_id] if owner_id else [],
                    tool_context=tool_context,
                    event_sink=event_sink,
                )
                if entry.tool_runtime is not None
                else self._empty_tracker()
            )
        prompt = _append_runtime_context(prompt, entry.tool_runtime)
        if entry.eviction_handle is not None:
            entry.eviction_handle.cancel()
            entry.eviction_handle = None
        entry.busy = True
        evidence: Dict[str, Any] = {
            "result": "",
            "error": "ClaudeSDKClient stream ended without ResultMessage",
            "stream_event_count": 0,
            "text_delta_count": 0,
            "client_reused": reused_live_client,
            "tool_calls": tracker.get("calls", []),
            "interaction_requests": tracker.get("interaction_requests", []),
            "artifact_updates": tracker.get("artifact_updates", []),
            "asset_reads": tracker.get("asset_reads", []),
            "dynamic_runs": tracker.get("dynamic_runs", []),
            "implementation_runs": tracker.get("implementation_runs", []),
            "result_refs": tracker.get("result_refs", []),
            "agent_tool_names": [],
            "skill_results": [],
            "skill_entries": [],
        }
        tool_names_by_id: dict[str, str] = {}
        public_tool_progress: dict[str, tuple[str, str]] = {}
        active_stage = "runtime"
        understanding_completed = False
        self._send_event(
            event_sink,
            {
                "source": "claude",
                "type": "stage_start",
                "content": "Finance CC started",
                "metadata": {"stage": active_stage, "user_visible": False},
            },
        )
        self._send_event(
            event_sink,
            {
                "source": "claude",
                "type": "reasoning_summary_delta",
                "content": "正在理解问题，并确认其中的对象、时间与金融口径。",
                "metadata": {
                    "stage": active_stage,
                    "progress_id": "finance_understanding",
                    "title": "问题理解",
                    "status": "running",
                },
            },
        )
        state = tool_context.get("custom_tool_state") if isinstance(tool_context.get("custom_tool_state"), Mapping) else {}
        has_confirmed_design = isinstance(state.get("design_contract"), Mapping) and bool(state.get("design_contract"))
        timeout_env = "FINANCE_CC_LONG_TURN_TIMEOUT_SECONDS" if has_confirmed_design else "FINANCE_CC_TURN_TIMEOUT_SECONDS"
        timeout_default = 900 if has_confirmed_design else 180
        timeout_seconds = max(30, int(os.environ.get(timeout_env) or timeout_default))
        try:
            async with asyncio.timeout(timeout_seconds):
                await entry.client.query(prompt)
                async for message in entry.client.receive_response():
                    class_name = type(message).__name__
                    if class_name == "StreamEvent":
                        evidence["stream_event_count"] += 1
                        event = getattr(message, "event", None)
                        event = event if isinstance(event, dict) else {}
                        if event.get("type") == "content_block_delta":
                            delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
                            if delta.get("type") == "text_delta" and delta.get("text"):
                                evidence["text_delta_count"] += 1
                    if class_name == "AssistantMessage":
                        for block in getattr(message, "content", None) or []:
                            if type(block).__name__ == "ToolUseBlock":
                                name = _trim(getattr(block, "name", ""))
                                tool_input = getattr(block, "input", None)
                                tool_input = tool_input if isinstance(tool_input, Mapping) else {}
                                active_stage = self._stage_for_tool(name, tool_input)
                                tool_use_id = _trim(getattr(block, "id", ""))
                                if not understanding_completed:
                                    understanding_completed = True
                                    self._send_event(
                                        event_sink,
                                        {
                                            "source": "claude",
                                            "type": "reasoning_summary_delta",
                                            "content": "已明确本题的对象与口径，开始获取所需证据。",
                                            "metadata": {
                                                "stage": active_stage,
                                                "progress_id": "finance_understanding",
                                                "title": "问题理解",
                                                "status": "completed",
                                            },
                                        },
                                    )
                                if tool_use_id and name:
                                    tool_names_by_id[tool_use_id] = name
                                if name and name not in evidence["agent_tool_names"]:
                                    evidence["agent_tool_names"].append(name)
                                if name == "Skill":
                                    qualified_skill = next(
                                        (
                                            _trim(value)
                                            for key, value in tool_input.items()
                                            if _trim(key).lower()
                                            in {"skill", "skill_id", "skill_name", "name"}
                                            and _trim(value)
                                        ),
                                        "",
                                    )
                                    if not qualified_skill:
                                        qualified_skill = next(
                                            (
                                                _trim(value)
                                                for value in tool_input.values()
                                                if isinstance(value, str)
                                                and ":" in _trim(value)
                                            ),
                                            "",
                                        )
                                    evidence["skill_entries"].append(
                                        {
                                            "skill_id": _canonical_skill_id(
                                                qualified_skill
                                            ),
                                            "qualified_skill": qualified_skill,
                                        }
                                    )
                                    if tool_use_id:
                                        public_tool_progress[tool_use_id] = (
                                            "专业分析方法",
                                            "正在加载与问题匹配的专业分析方法。",
                                        )
                                elif name in {"financial_news_search"} and tool_use_id:
                                    public_tool_progress[tool_use_id] = (
                                        "补充信息",
                                        "正在检索与问题直接相关的公开金融信息。",
                                    )
                                if tool_use_id in public_tool_progress:
                                    progress_title, progress_content = public_tool_progress[
                                        tool_use_id
                                    ]
                                    self._send_event(
                                        event_sink,
                                        {
                                            "source": "claude",
                                            "type": "reasoning_summary_delta",
                                            "content": progress_content,
                                            "metadata": {
                                                "stage": active_stage,
                                                "progress_id": f"finance_tool_{tool_use_id}",
                                                "title": progress_title,
                                                "status": "running",
                                            },
                                        },
                                    )
                                self._send_event(
                                    event_sink,
                                    {
                                        "source": "claude",
                                        "type": "tool_call",
                                        "content": name or "Finance CC tool",
                                        "metadata": {
                                            "stage": active_stage,
                                            "tool": name,
                                            "user_visible": False,
                                        },
                                    },
                                )
                    if class_name == "UserMessage":
                        for block in getattr(message, "content", None) or []:
                            if type(block).__name__ != "ToolResultBlock":
                                continue
                            tool_name = tool_names_by_id.get(_trim(getattr(block, "tool_use_id", "")), "")
                            tool_use_id = _trim(getattr(block, "tool_use_id", ""))
                            if tool_use_id in public_tool_progress:
                                progress_title, _ = public_tool_progress[tool_use_id]
                                self._send_event(
                                    event_sink,
                                    {
                                        "source": "claude",
                                        "type": "reasoning_summary_delta",
                                        "content": (
                                            "专业分析方法已就绪。"
                                            if tool_name == "Skill"
                                            else "相关公开金融信息已返回。"
                                        ),
                                        "metadata": {
                                            "stage": active_stage,
                                            "progress_id": f"finance_tool_{tool_use_id}",
                                            "title": progress_title,
                                            "status": "completed",
                                        },
                                    },
                                )
                            if tool_name != "Skill":
                                continue
                            content = getattr(block, "content", "")
                            if isinstance(content, list):
                                content = json.dumps(content, ensure_ascii=False, default=str)
                            skill_result = _trim(content)
                            if skill_result:
                                evidence["skill_results"].append(skill_result[:5_000])
                    if class_name == "ResultMessage":
                        evidence["result"] = _trim(getattr(message, "result", ""))
                        sdk_failed = (
                            bool(getattr(message, "is_error", False))
                            or _trim(getattr(message, "subtype", "")) != "success"
                        )
                        evidence["error"] = evidence["result"] if sdk_failed else ""
                if not _trim(evidence["result"]) and not _trim(evidence["error"]):
                    evidence["error"] = "Finance CC completed without a final response"
        except TimeoutError:
            evidence["error"] = f"Finance CC turn timed out after {timeout_seconds}s"
            await self._discard_live_client(session_id, expected=entry)
        except Exception:
            await self._discard_live_client(session_id, expected=entry)
            raise
        finally:
            entry.busy = False
            entry.last_used = time.monotonic()
            if self._live_clients.get(session_id) is entry:
                self._schedule_idle_eviction(session_id, entry)
        has_transport_error = bool(_trim(evidence["error"]))
        if not understanding_completed:
            self._send_event(
                event_sink,
                {
                    "source": "claude",
                    "type": "reasoning_summary_delta",
                    "content": "已完成问题理解；本题无需额外数据查询。",
                    "metadata": {
                        "stage": active_stage,
                        "progress_id": "finance_understanding",
                        "title": "问题理解",
                        "status": "completed",
                    },
                },
            )
        self._send_event(
            event_sink,
            {
                "source": "claude",
                "type": "reasoning_summary_delta",
                "content": (
                    "本轮金融问答未能完成。"
                    if has_transport_error
                    else "证据与回答已整理完成。"
                ),
                "metadata": {
                    "stage": active_stage,
                    "progress_id": "finance_synthesis",
                    "title": "回答整理",
                    "status": "error" if has_transport_error else "completed",
                },
            },
        )
        self._send_event(
            event_sink,
            {
                "source": "claude",
                "type": "error" if has_transport_error else "stage_result",
                "content": evidence["error"] if has_transport_error else "Finance CC completed",
                "metadata": {"stage": active_stage, "user_visible": False},
            },
        )
        return evidence

    def _effective_skill_names(
        self,
        tool_context: Mapping[str, Any],
    ) -> tuple[str, ...]:
        raw_allowed = tool_context.get("allowed_finance_skills")
        if not isinstance(raw_allowed, list):
            return self.skill_names
        allowed = {
            _canonical_skill_id(item)
            for item in raw_allowed
            if _canonical_skill_id(item)
        }
        return tuple(
            name
            for name in self.skill_names
            if _canonical_skill_id(name) in allowed
        )

    @staticmethod
    def _stage_for_tool(name: str, tool_input: Mapping[str, Any]) -> str:
        normalized = _trim(name).lower()
        if normalized == "skill":
            skill_text = json.dumps(dict(tool_input), ensure_ascii=False).lower()
            if "fin-agent-finance-business:" in skill_text:
                return "runtime"
            if "requirement" in skill_text:
                return "requirement"
            if "flowchart" in skill_text:
                return "flowchart"
            if "test-execution" in skill_text:
                return "test"
            return "design"
        if normalized.endswith("implement_dynamic_tool"):
            return "coding"
        if normalized.endswith("run_dynamic_tool"):
            return "test"
        if normalized.endswith("read_finance_asset"):
            return "view"
        return "runtime"

    @staticmethod
    def _send_event(
        event_sink: Optional[Callable[[Dict[str, Any]], None]],
        event: Dict[str, Any],
    ) -> None:
        if event_sink is None:
            return
        try:
            event_sink(event)
        except Exception:
            return

    def _session_key(self, *, thread_id: int | str, owner_id: str) -> str:
        owner_hash = hashlib.sha256(_trim(owner_id).encode("utf-8")).hexdigest()[:16]
        thread_text = _trim(thread_id) or "unknown"
        return f"{owner_hash}/{thread_text}"

    def _session_dir(self, key: str) -> Path:
        return self.root_dir / key

    @staticmethod
    def _session_id(key: str, *, generation: int = 0) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"fin-agent:finance-cc:{key}:{max(0, int(generation))}"))

    def _session_lock(self, key: str) -> threading.Lock:
        with self._locks_guard:
            return self._session_locks.setdefault(key, threading.Lock())

    @staticmethod
    @contextmanager
    def _process_session_lock(session_dir: Path):
        session_dir.mkdir(parents=True, exist_ok=True)
        lock_handle = (session_dir / "session.lock").open("a+", encoding="utf-8")
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            lock_handle.close()

    def _append_record(self, record: Mapping[str, Any]) -> None:
        with self._log_lock:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(dict(record), ensure_ascii=False) + "\n")

    def _finish_future(self, future: Future) -> None:
        with self._futures_guard:
            self._futures.discard(future)
