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
from src.services.fixed_upstream_loopback_bridge import (
    FixedUpstreamLoopbackBridge,
)


def _trim(value: Any) -> str:
    return str(value or "").strip()


def _canonical_skill_id(value: Any) -> str:
    normalized = _trim(value)
    return normalized.rsplit(":", 1)[-1] if normalized else ""


def _normalize_llm_usage(value: Any) -> Dict[str, int]:
    source = value if isinstance(value, Mapping) else {}
    prompt_tokens = int(
        source.get("prompt_tokens")
        or source.get("input_tokens")
        or 0
    )
    completion_tokens = int(
        source.get("completion_tokens")
        or source.get("output_tokens")
        or 0
    )
    total_tokens = int(
        source.get("total_tokens")
        or prompt_tokens + completion_tokens
    )
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "call_count": 1 if total_tokens > 0 else 0,
    }


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
    session_dir: Path
    last_used: float
    busy: bool = False
    prewarmed: bool = False
    turn_count: int = 0
    eviction_handle: Any = None


class _FinanceCcFirstResponseTimeout(TimeoutError):
    """The provider accepted the turn but produced no stream message in time."""


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
        warm_pool_size: Optional[int] = None,
        first_response_timeout_seconds: Optional[float] = None,
        turn_runner: Optional[Callable[..., Dict[str, Any]]] = None,
        system_tools: Any = None,
        system_prompt_path: str | Path | None = None,
        skill_root: str | Path | None = None,
        skill_names: Optional[list[str]] = None,
        skill_snapshot_provider: Optional[Callable[[], Mapping[str, Any]]] = None,
        skill_snapshot_validator: Optional[
            Callable[[Mapping[str, Any]], None]
        ] = None,
        runtime_scope_prefix: str = "",
        max_turns: Optional[int] = None,
        system_context_paths: Optional[list[str | Path]] = None,
        effort: str = "",
    ) -> None:
        enabled_text = _trim(os.environ.get("FINANCE_CC_SHADOW_ENABLED")).lower()
        self.enabled = bool(enabled) if enabled is not None else enabled_text in {"1", "true", "yes", "on"}
        self.provider = _trim(provider or os.environ.get("FINANCE_CC_PROVIDER") or "deepseek").lower()
        self.model = _trim(model or os.environ.get("FINANCE_CC_MODEL") or "deepseek-chat")
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
        self.skill_snapshot_provider = skill_snapshot_provider
        self.skill_snapshot_validator = skill_snapshot_validator
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
        self.warm_pool_size = max(
            0,
            int(
                warm_pool_size
                if warm_pool_size is not None
                else os.environ.get("FINANCE_CC_WARM_POOL_SIZE") or 2
            ),
        )
        self.first_response_timeout_seconds = max(
            0.01,
            float(
                first_response_timeout_seconds
                if first_response_timeout_seconds is not None
                else os.environ.get("FINANCE_CC_FIRST_RESPONSE_TIMEOUT_SECONDS") or 45
            ),
        )
        self._runtime_guard = threading.Lock()
        self._runtime_ready = threading.Event()
        self._runtime_loop: Optional[asyncio.AbstractEventLoop] = None
        self._runtime_thread: Optional[threading.Thread] = None
        self._live_clients: dict[str, _LiveClaudeClient] = {}
        self._warm_pool_guard = threading.Lock()
        self._warm_futures_guard = threading.Lock()
        self._warm_futures: set[Any] = set()
        self._warm_session_ids: set[str] = set()
        self._warm_pool_context: dict[str, Any] = {}
        self._warm_pool_filling = False
        self._warm_pool_error = ""
        self._closing = threading.Event()
        bridge_flag = _trim(
            os.environ.get("FINANCE_CC_HTTPS_BRIDGE_ENABLED") or "1"
        ).lower()
        self._https_bridge_enabled = (
            self.provider == "dashscope"
            and bridge_flag not in {"0", "false", "no", "off"}
        )
        self._https_bridge_guard = threading.Lock()
        self._https_bridge: Optional[FixedUpstreamLoopbackBridge] = None

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
        key = self._session_key(
            thread_id=thread_id,
            owner_id=owner_id,
            context=context,
        )
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
            prompt = self.build_user_prompt(user_text, context or {})
            runtime_tool_context = dict(context or {})
            runtime_tool_context["_agent_runtime_scope"] = (
                f"{self.runtime_scope_prefix}:{key}" if self.runtime_scope_prefix else key
            )
            marker_session_id = _trim(marker.get("session_id"))
            resumed = bool(marker.get("resumable", bool(marker))) and bool(marker_session_id)
            claimed_warm = None if resumed else self._claim_warm_client(runtime_tool_context)
            if claimed_warm is not None:
                session_id, client_session_dir = claimed_warm
            else:
                session_id = (
                    marker_session_id
                    if resumed
                    else self._session_id(key, generation=generation)
                )
                stored_session_dir = _trim(marker.get("client_session_dir"))
                client_session_dir = (
                    Path(stored_session_dir)
                    if resumed and stored_session_dir
                    else session_dir
                )
            started_at = time.monotonic()
            try:
                result = self._turn_runner(
                    prompt=prompt,
                    session_id=session_id,
                    resume=resumed,
                    session_dir=client_session_dir,
                    owner_id=owner_id,
                    tool_context=runtime_tool_context,
                    event_sink=event_sink,
                )
                # The SDK session owns its history. A completed SDK turn remains
                # resumable even when ResultMessage reports a business/model
                # error. Transport failures are different: the live client was
                # discarded and must never be resumed.
                transport_failed = bool(_trim(result.get("failure_kind")))
                next_generation = generation + 1 if transport_failed else generation
                marker_path.write_text(
                    json.dumps(
                        {
                            "session_id": (
                                self._session_id(key, generation=next_generation)
                                if transport_failed
                                else session_id
                            ),
                            "generation": next_generation,
                            "resumable": not transport_failed,
                            "client_session_dir": (
                                str(session_dir)
                                if transport_failed
                                else str(client_session_dir)
                            ),
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
                    "stream_event_count": int(result.get("stream_event_count") or 0),
                    "text_delta_count": int(result.get("text_delta_count") or 0),
                    "client_reused": bool(result.get("client_reused")),
                    "client_prewarmed": bool(result.get("client_prewarmed")),
                    "failure_kind": _trim(result.get("failure_kind")),
                    "api_retry_count": int(
                        result.get("api_retry_count") or 0
                    ),
                    "api_error_status": result.get("api_error_status"),
                    "turn_timeout_seconds": int(
                        result.get("turn_timeout_seconds") or 0
                    ),
                    "provider_transport": (
                        dict(result.get("provider_transport") or {})
                        if isinstance(result.get("provider_transport"), Mapping)
                        else {}
                    ),
                    # Keep the full model result for the business response.
                    # Observability storage applies its own bounded copy in
                    # `_append_record`; it must not truncate the user answer.
                    "result": _trim(result.get("result")),
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
                    "llm_usage": _normalize_llm_usage(result.get("llm_usage")),
                    "model_name": self.model,
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
                            "client_session_dir": str(session_dir),
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
        research_mode_prompt = _trim(
            context.get("_finance_research_mode_prompt")
        )
        if research_mode_prompt:
            lines.append(
                "[系统记录的本轮研究模式]\n" + research_mode_prompt
            )
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

    def prewarm(
        self,
        *,
        context: Optional[Mapping[str, Any]] = None,
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        if (
            not self.enabled
            or self.warm_pool_size <= 0
            or self._closing.is_set()
        ):
            return self.pool_status()
        tool_context = dict(context or {})
        tool_context["_agent_runtime_scope"] = f"{self.runtime_scope_prefix}:warm-pool"
        with self._warm_pool_guard:
            self._warm_pool_context = dict(tool_context)
        loop = self._ensure_runtime_loop()
        future = asyncio.run_coroutine_threadsafe(
            self._ensure_warm_pool(tool_context),
            loop,
        )
        self._track_warm_future(future)
        future.result(timeout=max(1.0, float(timeout)))
        return self.pool_status()

    def pool_status(self) -> Dict[str, Any]:
        with self._warm_pool_guard:
            warm_ids = set(self._warm_session_ids)
            error = self._warm_pool_error
        assigned = sum(
            1 for session_id in self._live_clients if session_id not in warm_ids
        )
        return {
            "target_warm_clients": self.warm_pool_size,
            "warm_clients": len(warm_ids),
            "assigned_live_clients": assigned,
            "max_assigned_live_clients": self.max_live_clients,
            "error": error,
        }

    def _claim_warm_client(
        self,
        tool_context: Mapping[str, Any],
    ) -> Optional[tuple[str, Path]]:
        if self.warm_pool_size <= 0 or self._closing.is_set():
            return None
        options = self._runtime_options(tool_context)
        claimed: Optional[tuple[str, Path]] = None
        with self._warm_pool_guard:
            for session_id in list(self._warm_session_ids):
                entry = self._live_clients.get(session_id)
                if entry is None:
                    self._warm_session_ids.discard(session_id)
                    continue
                if entry.options_fingerprint != options["fingerprint"]:
                    continue
                self._warm_session_ids.remove(session_id)
                entry.prewarmed = True
                claimed = (session_id, entry.session_dir)
                break
            self._warm_pool_context = dict(tool_context)
        self._schedule_warm_pool_replenishment()
        return claimed

    def _schedule_warm_pool_replenishment(self) -> None:
        if self.warm_pool_size <= 0 or self._closing.is_set():
            return
        with self._warm_pool_guard:
            context = dict(self._warm_pool_context)
        if not context:
            return
        loop = self._ensure_runtime_loop()
        future = asyncio.run_coroutine_threadsafe(
            self._ensure_warm_pool(context),
            loop,
        )
        self._track_warm_future(future)

    def _track_warm_future(self, future: Any) -> None:
        with self._warm_futures_guard:
            if self._closing.is_set():
                future.cancel()
                return
            self._warm_futures.add(future)

        def finished(done: Any) -> None:
            with self._warm_futures_guard:
                self._warm_futures.discard(done)

        future.add_done_callback(finished)

    def close(self) -> None:
        self._closing.set()
        with self._warm_futures_guard:
            warm_futures = list(self._warm_futures)
            self._warm_futures.clear()
        for future in warm_futures:
            future.cancel()
        for future in warm_futures:
            try:
                future.result(timeout=5)
            except Exception:
                pass
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
        with self._https_bridge_guard:
            bridge = self._https_bridge
            self._https_bridge = None
        if bridge is not None:
            bridge.close()

    def _provider_base_url(self, upstream_base_url: str) -> str:
        if not self._https_bridge_enabled:
            return upstream_base_url
        with self._https_bridge_guard:
            if self._https_bridge is None:
                self._https_bridge = FixedUpstreamLoopbackBridge(
                    upstream_base_url,
                    # This is an upstream read-idle limit, not a whole-turn
                    # limit. A bounded idle window prevents abandoned CLI
                    # requests from retaining bridge threads for the full
                    # long-turn timeout.
                    timeout_seconds=max(
                        15,
                        float(
                            os.environ.get(
                                "FINANCE_CC_BRIDGE_READ_IDLE_SECONDS"
                            )
                            or 120
                        ),
                    ),
                )
                return self._https_bridge.start()
            if self._https_bridge.upstream_base_url != upstream_base_url.rstrip(
                "/"
            ):
                raise RuntimeError(
                    "Finance CC provider endpoint changed after bridge startup"
                )
            return self._https_bridge.base_url

    def _provider_bridge_diagnostics(self) -> Dict[str, Any]:
        with self._https_bridge_guard:
            bridge = self._https_bridge
        if bridge is None:
            return {}
        return bridge.diagnostics()

    def _ensure_runtime_loop(self) -> asyncio.AbstractEventLoop:
        if self._closing.is_set():
            raise RuntimeError("Finance CC client runtime is closing")
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

    @staticmethod
    def _progress_profile(tool_context: Mapping[str, Any]) -> Dict[str, str]:
        is_tool_development = (
            _trim(tool_context.get("turn_mode")) == "tool_development"
            or _trim(tool_context.get("entry")) == "custom_tool_flow"
        )
        if not is_tool_development:
            research_mode = _trim(
                tool_context.get("_finance_research_mode")
            ).lower()
            running = (
                "已按快速回答处理，正在确认最关键的对象、时间和证据。"
                if research_mode == "fast"
                else "已进入深度研究，正在明确核心命题并规划最小充分证据。"
                if research_mode == "deep"
                else "正在理解问题，并由业务 Skill 判断适合本题的分析深度与证据范围。"
            )
            return {
                "stage": "runtime",
                "progress_id": "finance_understanding",
                "title": "问题理解",
                "running": running,
                "ready": "已明确本题的对象与口径，开始获取所需证据。",
                "completed": "已完成问题理解；本题无需额外数据查询。",
                "failed": "金融问答服务未及时返回，本轮没有产生新的查询结果。",
                "result_progress_id": "finance_synthesis",
                "result_title": "回答整理",
                "result_completed": "证据与回答已整理完成。",
                "result_failed": "本轮金融问答未能完成。",
            }
        state = (
            tool_context.get("custom_tool_state")
            if isinstance(tool_context.get("custom_tool_state"), Mapping)
            else {}
        )
        has_requirement = bool(
            _trim(state.get("requirement_brief"))
            or _trim(state.get("requirement_text"))
        )
        stage = "design" if has_requirement else "requirement"
        return {
            "stage": stage,
            "progress_id": "custom_tool_understanding",
            "title": "工具需求与设计",
            "running": (
                "已进入自定义工具创建，正在结合你的描述梳理目标、输入输出和关键规则。"
                if stage == "requirement"
                else "正在结合已保存的需求继续形成或修订工具设计。"
            ),
            "ready": "需求范围已经明确，开始形成可执行、可验证的工具方案。",
            "completed": "本轮自定义工具需求与设计已经整理完成。",
            "failed": "工具设计服务未及时返回；你的需求已经保留，可以重试当前阶段。",
            "result_progress_id": "custom_tool_design_result",
            "result_title": "工具设计",
            "result_completed": "本轮工具需求或设计结果已经整理完成。",
            "result_failed": "本轮工具设计未能完成，已有需求和业务资产保持不变。",
        }

    @staticmethod
    def _turn_timeout_seconds(
        tool_context: Mapping[str, Any],
        *,
        activated_skill_id: str = "",
    ) -> int:
        state = (
            tool_context.get("custom_tool_state")
            if isinstance(tool_context.get("custom_tool_state"), Mapping)
            else {}
        )
        has_confirmed_design = (
            isinstance(state.get("design_contract"), Mapping)
            and bool(state.get("design_contract"))
        )
        budget_by_skill = (
            tool_context.get("_finance_skill_execution_budget")
            if isinstance(
                tool_context.get("_finance_skill_execution_budget"),
                Mapping,
            )
            else {}
        )
        skill_budget = _trim(
            budget_by_skill.get(_trim(activated_skill_id))
        ).lower()
        use_long_budget = has_confirmed_design or skill_budget == "long"
        timeout_env = (
            "FINANCE_CC_LONG_TURN_TIMEOUT_SECONDS"
            if use_long_budget
            else "FINANCE_CC_TURN_TIMEOUT_SECONDS"
        )
        timeout_default = 900 if use_long_budget else 180
        return max(30, int(os.environ.get(timeout_env) or timeout_default))

    def initial_progress_event(
        self,
        context: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        profile = self._progress_profile(context or {})
        return {
            "source": "claude",
            "type": "reasoning_summary_delta",
            "content": profile["running"],
            "metadata": {
                "stage": profile["stage"],
                "progress_id": profile["progress_id"],
                "title": profile["title"],
                "status": "running",
            },
        }

    @staticmethod
    def _is_meaningful_response_message(message: Any) -> bool:
        """Ignore SDK lifecycle chatter when deciding whether a turn responded."""
        class_name = type(message).__name__
        if class_name in {"AssistantMessage", "UserMessage"}:
            return bool(getattr(message, "content", None))
        if class_name == "ResultMessage":
            return True
        if class_name != "StreamEvent":
            return False
        event = getattr(message, "event", None)
        event = event if isinstance(event, Mapping) else {}
        event_type = _trim(event.get("type"))
        if event_type == "content_block_start":
            block = (
                event.get("content_block")
                if isinstance(event.get("content_block"), Mapping)
                else {}
            )
            return _trim(block.get("type")) in {"text", "tool_use"}
        if event_type != "content_block_delta":
            return False
        delta = event.get("delta") if isinstance(event.get("delta"), Mapping) else {}
        delta_type = _trim(delta.get("type"))
        return (
            delta_type == "text_delta" and bool(_trim(delta.get("text")))
        ) or (
            delta_type == "input_json_delta"
            and bool(_trim(delta.get("partial_json")))
        )

    @staticmethod
    async def _response_messages(
        client: Any,
        *,
        prompt: str,
        first_response_timeout_seconds: float,
    ):
        iterator = None
        try:
            async with asyncio.timeout(first_response_timeout_seconds):
                await client.query(prompt)
                iterator = client.receive_response().__aiter__()
                async for message in iterator:
                    yield message
                    if FinanceClaudeSessionService._is_meaningful_response_message(
                        message
                    ):
                        break
        except TimeoutError as exc:
            raise _FinanceCcFirstResponseTimeout from exc
        if iterator is None:
            return
        async for message in iterator:
            yield message

    def _runtime_options(self, tool_context: Mapping[str, Any]) -> Dict[str, Any]:
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
        skill_snapshot = (
            self._pinned_skill_snapshot(tool_context)
            if "_finance_skill_runtime_binding" in tool_context
            else self._skill_snapshot()
        )
        effective_skill_root = Path(
            _trim(skill_snapshot.get("runtime_root")) or self.skill_root
        )
        raw_snapshot_skill_names = skill_snapshot.get("skill_names")
        configured_skill_names = (
            tuple(
                _trim(item)
                for item in raw_snapshot_skill_names
                if _trim(item)
            )
            if isinstance(raw_snapshot_skill_names, (list, tuple))
            else self.skill_names
        )
        effective_skill_names = self._effective_skill_names(
            tool_context,
            configured_skill_names=configured_skill_names,
        )
        skill_revision = (
            _trim(skill_snapshot.get("revision"))
            or _trim(tool_context.get("_finance_skill_catalog_revision"))
        )
        if effective_skill_names and self.skill_snapshot_validator is not None:
            self.skill_snapshot_validator(
                {
                    "revision": skill_revision,
                    "runtime_root": effective_skill_root,
                    "skill_names": list(configured_skill_names),
                }
            )
        fingerprint = hashlib.sha256(
            "\0".join(
                [
                    self.provider,
                    self.model,
                    effort,
                    str(max_turns),
                    "tools" if self.system_tools is not None else "no-tools",
                    str(effective_skill_root),
                    skill_revision,
                    *effective_skill_names,
                    *[
                        _trim(item)
                        for item in tool_context.get("allowed_agent_tools") or []
                        if _trim(item)
                    ],
                    json.dumps(
                        tool_context.get("skill_tool_access") or {},
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    system_prompt,
                ]
            ).encode("utf-8")
        ).hexdigest()
        return {
            "system_prompt": system_prompt,
            "effort": effort,
            "max_turns": max_turns,
            "skill_root": effective_skill_root,
            "skill_revision": skill_revision,
            "effective_skill_names": effective_skill_names,
            "fingerprint": fingerprint,
        }

    async def _create_live_client(
        self,
        *,
        session_id: str,
        session_dir: Path,
        owner_id: str,
        tool_context: Mapping[str, Any],
        options: Mapping[str, Any],
        resume: bool,
        prewarmed: bool,
    ) -> tuple[_LiveClaudeClient, Dict[str, Any]]:
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, create_sdk_mcp_server

        provider_env = ClaudeSdkSkillHarness(
            provider=self.provider,
            model=self.model,
            query_impl=lambda **_: None,
        ).provider_env()
        upstream_base_url = _trim(provider_env.get("ANTHROPIC_BASE_URL"))
        if upstream_base_url:
            provider_env["ANTHROPIC_BASE_URL"] = self._provider_base_url(
                upstream_base_url
            )
            if provider_env["ANTHROPIC_BASE_URL"].startswith(
                "http://127.0.0.1:"
            ):
                inherited_no_proxy = _trim(
                    provider_env.get("NO_PROXY")
                    or provider_env.get("no_proxy")
                    or os.environ.get("NO_PROXY")
                    or os.environ.get("no_proxy")
                )
                no_proxy_items = [
                    item.strip()
                    for item in inherited_no_proxy.split(",")
                    if item.strip()
                ]
                for local_host in ("127.0.0.1", "localhost", "::1"):
                    if local_host not in no_proxy_items:
                        no_proxy_items.append(local_host)
                no_proxy = ",".join(no_proxy_items)
                provider_env["NO_PROXY"] = no_proxy
                provider_env["no_proxy"] = no_proxy
        provider_env.update(
            {
                "CLAUDE_CONFIG_DIR": str(session_dir / "claude"),
                "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1",
                "CLAUDE_CODE_MAX_RETRIES": str(
                    max(
                        0,
                        int(
                            os.environ.get("FINANCE_CC_API_MAX_RETRIES")
                            or os.environ.get("CLAUDE_CODE_MAX_RETRIES")
                            or 2
                        ),
                    )
                ),
            }
        )
        if not provider_env.get("ANTHROPIC_AUTH_TOKEN") and not provider_env.get("ANTHROPIC_API_KEY"):
            raise RuntimeError(f"missing credential for Finance CC provider {self.provider}")
        mcp_servers: dict[str, Any] = {}
        allowed_tools: list[str] = []
        tool_runtime = None
        tracker = self._empty_tracker()
        if self.system_tools is not None:
            runtime_factory = getattr(self.system_tools, "create_runtime", None)
            if callable(runtime_factory):
                tool_runtime = runtime_factory()
            else:
                from src.services.finance_cc_system_tools import FinanceCcToolRuntime

                tool_runtime = FinanceCcToolRuntime()
            tools, allowed_tools, tracker = self.system_tools.build_tools(
                owner_ids=[owner_id] if owner_id else [],
                tool_context=tool_context,
                event_sink=None,
                runtime=tool_runtime,
            )
            mcp_servers["finance"] = create_sdk_mcp_server(
                name="finance",
                version="1.0.0",
                tools=tools,
            )
        effective_skill_names = list(options["effective_skill_names"])
        agent_options = ClaudeAgentOptions(
            tools=["Skill"] if effective_skill_names else [],
            allowed_tools=["Skill", *allowed_tools] if effective_skill_names else allowed_tools,
            disallowed_tools=[
                "Bash", "Edit", "Write", "Read", "Glob", "Grep", "WebFetch", "WebSearch",
                "Agent", "Task", "AskUserQuestion", "NotebookEdit",
            ],
            system_prompt=str(options["system_prompt"]),
            mcp_servers=mcp_servers,
            strict_mcp_config=bool(mcp_servers),
            permission_mode="dontAsk",
            setting_sources=[],
            plugins=[
                {
                    "type": "local",
                    "path": str(Path(options["skill_root"]).resolve()),
                }
            ]
            if effective_skill_names
            else [],
            skills=effective_skill_names,
            cwd=str(session_dir),
            include_partial_messages=True,
            max_turns=int(options["max_turns"]),
            model=self.model,
            effort=str(options["effort"]),
            resume=session_id if resume else None,
            session_id=None if resume else session_id,
            env=provider_env,
        )
        client = ClaudeSDKClient(options=agent_options)
        try:
            await client.connect()
        except Exception:
            try:
                await client.disconnect()
            except Exception:
                pass
            raise
        return (
            _LiveClaudeClient(
                client=client,
                tool_runtime=tool_runtime,
                options_fingerprint=str(options["fingerprint"]),
                session_dir=session_dir,
                last_used=time.monotonic(),
                prewarmed=prewarmed,
            ),
            tracker,
        )

    async def _ensure_warm_pool(self, tool_context: Mapping[str, Any]) -> None:
        if self.warm_pool_size <= 0 or self._closing.is_set():
            return
        with self._warm_pool_guard:
            if self._warm_pool_filling:
                return
            self._warm_pool_filling = True
        try:
            options = self._runtime_options(tool_context)
            with self._warm_pool_guard:
                mismatched = [
                    session_id
                    for session_id in self._warm_session_ids
                    if (
                        self._live_clients.get(session_id) is None
                        or self._live_clients[session_id].options_fingerprint
                        != options["fingerprint"]
                    )
                ]
            for session_id in mismatched:
                await self._discard_live_client(session_id)
            while True:
                if self._closing.is_set():
                    break
                with self._warm_pool_guard:
                    missing = self.warm_pool_size - len(self._warm_session_ids)
                if missing <= 0:
                    break
                session_id = str(uuid.uuid4())
                session_dir = self.root_dir / "_client_sessions" / session_id
                session_dir.mkdir(parents=True, exist_ok=True)
                entry, _ = await self._create_live_client(
                    session_id=session_id,
                    session_dir=session_dir,
                    owner_id="",
                    tool_context=tool_context,
                    options=options,
                    resume=False,
                    prewarmed=True,
                )
                if self._closing.is_set():
                    try:
                        await asyncio.wait_for(
                            entry.client.disconnect(),
                            timeout=5,
                        )
                    except Exception:
                        pass
                    break
                self._live_clients[session_id] = entry
                with self._warm_pool_guard:
                    self._warm_session_ids.add(session_id)
                    self._warm_pool_error = ""
        except Exception as exc:
            with self._warm_pool_guard:
                self._warm_pool_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            raise
        finally:
            with self._warm_pool_guard:
                self._warm_pool_filling = False

    async def _make_pool_room(self) -> None:
        while True:
            with self._warm_pool_guard:
                warm_ids = set(self._warm_session_ids)
            assigned_count = sum(
                1 for session_id in self._live_clients if session_id not in warm_ids
            )
            if assigned_count < self.max_live_clients:
                return
            candidates = [
                (session_id, entry)
                for session_id, entry in self._live_clients.items()
                if session_id not in warm_ids and not entry.busy
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
        with self._warm_pool_guard:
            self._warm_session_ids.discard(session_id)
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
        options = self._runtime_options(tool_context)
        entry = self._live_clients.get(session_id)
        if entry is not None and entry.options_fingerprint != options["fingerprint"]:
            await self._discard_live_client(session_id, expected=entry)
            entry = None
            resume = True
        client_prewarmed = bool(entry is not None and entry.prewarmed and entry.turn_count == 0)
        reused_live_client = bool(entry is not None and entry.turn_count > 0)
        if entry is None:
            await self._make_pool_room()
            entry, tracker = await self._create_live_client(
                session_id=session_id,
                session_dir=session_dir,
                owner_id=owner_id,
                tool_context=tool_context,
                options=options,
                resume=resume,
                prewarmed=False,
            )
            self._live_clients[session_id] = entry
            if entry.tool_runtime is not None:
                # Tool closures retain the mutable runtime.  Rebind the first
                # cold turn to its real event sink after client construction;
                # otherwise only prewarmed/reused clients expose tool progress.
                tracker = entry.tool_runtime.begin_turn(
                    owner_ids=[owner_id] if owner_id else [],
                    tool_context=tool_context,
                    event_sink=event_sink,
                )
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
            "error": "",
            "failure_kind": "",
            "api_retry_count": 0,
            "api_error_status": None,
            "stream_event_count": 0,
            "text_delta_count": 0,
            "client_reused": reused_live_client,
            "client_prewarmed": client_prewarmed,
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
            "llm_usage": {},
            "provider_transport": {},
        }
        bridge_before = self._provider_bridge_diagnostics()
        tool_names_by_id: dict[str, str] = {}
        public_tool_progress: dict[str, tuple[str, str]] = {}
        progress_profile = self._progress_profile(tool_context)
        active_stage = progress_profile["stage"]
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
        if not bool(tool_context.get("_initial_progress_emitted")):
            self._send_event(
                event_sink,
                self.initial_progress_event(tool_context),
            )
        timeout_seconds = self._turn_timeout_seconds(tool_context)
        timeout_started_at = asyncio.get_running_loop().time()
        timeout_scope = asyncio.timeout(timeout_seconds)
        evidence["turn_timeout_seconds"] = timeout_seconds
        try:
            async with timeout_scope:
                async for message in self._response_messages(
                    entry.client,
                    prompt=prompt,
                    first_response_timeout_seconds=min(
                        self.first_response_timeout_seconds,
                        max(0.01, float(timeout_seconds) * 0.8),
                    ),
                ):
                    class_name = type(message).__name__
                    if (
                        class_name == "SystemMessage"
                        and _trim(getattr(message, "subtype", ""))
                        == "api_retry"
                    ):
                        evidence["api_retry_count"] += 1
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
                                            "content": progress_profile["ready"],
                                            "metadata": {
                                                "stage": progress_profile["stage"],
                                                "progress_id": progress_profile["progress_id"],
                                                "title": progress_profile["title"],
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
                                    skill_id = _canonical_skill_id(qualified_skill)
                                    skill_timeout_seconds = self._turn_timeout_seconds(
                                        tool_context,
                                        activated_skill_id=skill_id,
                                    )
                                    if skill_timeout_seconds > timeout_seconds:
                                        timeout_seconds = skill_timeout_seconds
                                        evidence["turn_timeout_seconds"] = timeout_seconds
                                        timeout_scope.reschedule(
                                            timeout_started_at + timeout_seconds
                                        )
                                    evidence["skill_entries"].append(
                                        {
                                            "skill_id": skill_id,
                                            "qualified_skill": qualified_skill,
                                        }
                                    )
                                    activate_skill = getattr(
                                        entry.tool_runtime,
                                        "activate_skill",
                                        None,
                                    )
                                    if skill_id and callable(activate_skill):
                                        activate_skill(skill_id)
                                    if tool_use_id and _trim(tool_context.get("turn_mode")) == "tool_development":
                                        public_tool_progress[tool_use_id] = (
                                            "工具设计方法",
                                            "正在加载与当前阶段匹配的工具需求或设计方法。",
                                        )
                                    elif tool_use_id:
                                        public_tool_progress[tool_use_id] = (
                                            "专业分析方法",
                                            "正在加载与问题匹配的专业分析方法。",
                                        )
                                elif name in {"financial_news_search", "general_search"} and tool_use_id:
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
                        evidence["llm_usage"] = _normalize_llm_usage(
                            getattr(message, "usage", None)
                        )
                        sdk_failed = (
                            bool(getattr(message, "is_error", False))
                            or _trim(getattr(message, "subtype", "")) != "success"
                        )
                        evidence["error"] = evidence["result"] if sdk_failed else ""
                        evidence["api_error_status"] = getattr(
                            message,
                            "api_error_status",
                            None,
                        )
                        if sdk_failed and (
                            evidence["api_retry_count"] > 0
                            or evidence["result"].lower().startswith("api error:")
                        ):
                            evidence["failure_kind"] = "provider_api_error"
                if not _trim(evidence["result"]) and not _trim(evidence["error"]):
                    evidence["error"] = "Finance CC completed without a final response"
                    evidence["failure_kind"] = "empty_response"
                if evidence["failure_kind"] == "provider_api_error":
                    await self._discard_live_client(session_id, expected=entry)
        except _FinanceCcFirstResponseTimeout:
            evidence["failure_kind"] = "first_response_timeout"
            evidence["error"] = (
                "Finance CC first response timed out after "
                f"{self.first_response_timeout_seconds:g}s"
            )
            await self._discard_live_client(session_id, expected=entry)
        except TimeoutError:
            evidence["failure_kind"] = "turn_timeout"
            evidence["error"] = f"Finance CC turn timed out after {timeout_seconds}s"
            await self._discard_live_client(session_id, expected=entry)
        except Exception:
            await self._discard_live_client(session_id, expected=entry)
            raise
        finally:
            entry.busy = False
            entry.turn_count += 1
            entry.prewarmed = False
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
                    "content": (
                        progress_profile["failed"]
                        if has_transport_error
                        else progress_profile["completed"]
                    ),
                    "metadata": {
                        "stage": progress_profile["stage"],
                        "progress_id": progress_profile["progress_id"],
                        "title": progress_profile["title"],
                        "status": "error" if has_transport_error else "completed",
                    },
                },
            )
        self._send_event(
            event_sink,
            {
                "source": "claude",
                "type": "reasoning_summary_delta",
                "content": (
                    progress_profile["result_failed"]
                    if has_transport_error
                    else progress_profile["result_completed"]
                ),
                "metadata": {
                    "stage": active_stage,
                    "progress_id": progress_profile["result_progress_id"],
                    "title": progress_profile["result_title"],
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
        bridge_after = self._provider_bridge_diagnostics()
        if bridge_after:
            request_delta = max(
                0,
                int(bridge_after.get("total_requests") or 0)
                - int(bridge_before.get("total_requests") or 0),
            )
            failure_delta = max(
                0,
                int(bridge_after.get("upstream_failures") or 0)
                - int(bridge_before.get("upstream_failures") or 0),
            )
            evidence["provider_transport"] = {
                "adapter": "verified_https_loopback",
                "request_count": request_delta,
                "failure_count": failure_delta,
                "last_error_type": (
                    _trim(bridge_after.get("last_error_type"))
                    if failure_delta
                    else ""
                ),
            }
        return evidence

    def _skill_snapshot(self) -> Mapping[str, Any]:
        if self.skill_snapshot_provider is None:
            return {}
        snapshot = self.skill_snapshot_provider()
        return snapshot if isinstance(snapshot, Mapping) else {}

    def _pinned_skill_snapshot(
        self,
        tool_context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Require a valid system-generated sibling content-addressed binding."""

        raw = tool_context.get("_finance_skill_runtime_binding")
        if not isinstance(raw, Mapping):
            raise RuntimeError("invalid Finance Skill runtime binding")
        revision = _trim(raw.get("revision"))
        if revision != _trim(
            tool_context.get("_finance_skill_catalog_revision")
        ):
            raise RuntimeError("Finance Skill runtime binding revision mismatch")
        if len(revision) != 64:
            raise RuntimeError("invalid Finance Skill runtime binding")
        try:
            int(revision, 16)
            runtime_root = Path(_trim(raw.get("runtime_root"))).resolve()
            configured_root = self.skill_root.resolve()
        except (TypeError, ValueError, OSError) as exc:
            raise RuntimeError("invalid Finance Skill runtime binding") from exc
        if (
            runtime_root.name != revision
            or runtime_root.parent != configured_root.parent
        ):
            raise RuntimeError("invalid Finance Skill runtime binding")
        raw_names = raw.get("skill_names")
        if not isinstance(raw_names, list):
            raise RuntimeError("invalid Finance Skill runtime binding")
        return {
            "revision": revision,
            "runtime_root": runtime_root,
            "skill_names": [
                _trim(item)
                for item in raw_names
                if _trim(item)
            ],
        }

    def _effective_skill_names(
        self,
        tool_context: Mapping[str, Any],
        *,
        configured_skill_names: Optional[tuple[str, ...]] = None,
    ) -> tuple[str, ...]:
        available = (
            self.skill_names
            if configured_skill_names is None
            else configured_skill_names
        )
        raw_allowed = tool_context.get("allowed_finance_skills")
        if not isinstance(raw_allowed, list):
            return available
        allowed = {
            _canonical_skill_id(item)
            for item in raw_allowed
            if _canonical_skill_id(item)
        }
        return tuple(
            name
            for name in available
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

    def _session_key(
        self,
        *,
        thread_id: int | str,
        owner_id: str,
        context: Optional[Mapping[str, Any]] = None,
    ) -> str:
        owner_hash = hashlib.sha256(_trim(owner_id).encode("utf-8")).hexdigest()[:16]
        thread_text = _trim(thread_id) or "unknown"
        key = f"{owner_hash}/{thread_text}"
        runtime_context = context if isinstance(context, Mapping) else {}
        if _trim(runtime_context.get("entry")) != "custom_tool_flow":
            return key
        custom_tool_state = (
            runtime_context.get("custom_tool_state")
            if isinstance(runtime_context.get("custom_tool_state"), Mapping)
            else {}
        )
        flow_id = (
            _trim(runtime_context.get("custom_tool_flow_id"))
            or _trim(custom_tool_state.get("custom_tool_flow_id"))
        )
        if not flow_id:
            return key
        flow_hash = hashlib.sha256(flow_id.encode("utf-8")).hexdigest()[:24]
        return f"{key}/custom_tool/{flow_hash}"

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
        log_record = dict(record)
        log_record["result"] = _trim(log_record.get("result"))[:2_000]
        log_record["error"] = _trim(log_record.get("error"))[:500]
        with self._log_lock:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(log_record, ensure_ascii=False) + "\n")

    def _finish_future(self, future: Future) -> None:
        with self._futures_guard:
            self._futures.discard(future)
