from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any, Callable, Mapping, Optional
from urllib.parse import urlparse
import uuid

from src.scenarios.financial_qa.dsh_service import (
    FinanceDeepSeekHarnessSessionService,
    _atomic_write,
    _event_data,
    _json_arguments,
    _llm_step_usages,
    _model_name,
    _notification_tool_payload,
    _trim,
    _usage,
)
from src.services.finance_cc_system_tools import FinanceCcSystemTools


def _flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return _trim(value).lower() in {"1", "true", "yes", "on"}


def _policy_config(value: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    config: dict[str, Any] = {
        "enabled": True,
        "duplicateCallLimit": 1,
        "maxRequiredStageSteers": 1,
        "businessHint": "",
        "budgets": {
            "requirement": {"reasoningEffort": "low", "maxTokens": 4096},
            "design": {"reasoningEffort": "low", "maxTokens": 6144},
            "flow": {"reasoningEffort": "off", "maxTokens": 3072},
            "direct": {"reasoningEffort": "low", "maxTokens": 3072},
            "final": {"reasoningEffort": "off", "maxTokens": 2048},
        },
    }
    raw = _trim(os.environ.get("FINANCE_DSH_CUSTOM_TOOL_POLICY_CONFIG"))
    if raw:
        parsed = json.loads(raw)
        if not isinstance(parsed, Mapping):
            raise ValueError(
                "FINANCE_DSH_CUSTOM_TOOL_POLICY_CONFIG 必须是 JSON object"
            )
        config.update(dict(parsed))
    if value:
        config.update(dict(value))
    return config


def _custom_loop_observability(
    events: list[dict[str, Any]],
    *,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    marker = "[CUSTOM_TOOL_LOOP stage="
    active_stage: dict[str, str] = {}
    requests: list[dict[str, Any]] = []
    for event in events:
        event_type = _trim(event.get("type"))
        data = _event_data(event)
        if event_type == "user/message":
            source = (
                data.get("source")
                if isinstance(data.get("source"), Mapping)
                else {}
            )
            if _trim(source.get("plugin")) != "fin-agent-custom-tool-loop-policy":
                continue
            texts = [
                _trim(item.get("text"))
                for item in data.get("content") or []
                if isinstance(item, Mapping) and _trim(item.get("type")) == "text"
            ]
            text = "\n".join(item for item in texts if item)
            if marker in text:
                header = text.split("]", 1)[0]
                active_stage = {
                    "stage": header.split("stage=", 1)[1].split(" ", 1)[0],
                    "reason": (
                        header.split("reason=", 1)[1]
                        if "reason=" in header
                        else ""
                    ),
                    "prompt_sha256": hashlib.sha256(
                        text.encode("utf-8")
                    ).hexdigest(),
                }
            continue
        if event_type != "request/header":
            continue
        header = (
            data.get("header")
            if isinstance(data.get("header"), Mapping)
            else {}
        )
        request_config = (
            header.get("config")
            if isinstance(header.get("config"), Mapping)
            else {}
        )
        requests.append(
            {
                "request_index": len(requests) + 1,
                "stage": active_stage.get("stage", ""),
                "stage_reason": active_stage.get("reason", ""),
                "prompt_injected": bool(active_stage),
                "prompt_sha256": active_stage.get("prompt_sha256", ""),
                "reasoning_effort": _trim(
                    request_config.get("reasoningEffort")
                ),
                "max_tokens": int(request_config.get("maxTokens") or 0),
                "visible_tools": [
                    _trim(item.get("name"))
                    for item in header.get("tools") or []
                    if isinstance(item, Mapping) and _trim(item.get("name"))
                ],
            }
        )
    return {
        "enabled": bool(config.get("enabled", True)),
        "config": dict(config),
        "request_count": len(requests),
        "requests": requests,
        "final_stage": requests[-1]["stage"] if requests else "",
    }


def _finish_error(events: list[dict[str, Any]], finish_reason: str) -> str:
    for event in reversed(events):
        data = _event_data(event)
        if _trim(event.get("type")) == "turn/end":
            reason = data.get("reason") if isinstance(data.get("reason"), Mapping) else {}
            error = reason.get("error") if isinstance(reason.get("error"), Mapping) else {}
            message = _trim(error.get("message"))
            if message:
                return message
        if _trim(event.get("type")) == "assistant/chunk":
            chunk = data.get("chunk") if isinstance(data.get("chunk"), Mapping) else {}
            reason = chunk.get("reason") if isinstance(chunk.get("reason"), Mapping) else {}
            failure = reason.get("failure") if isinstance(reason.get("failure"), Mapping) else {}
            message = _trim(failure.get("message"))
            if message:
                return message
    return f"DeepSeek Harness turn ended with {finish_reason}"


def _coalesce_artifact_updates(values: Any) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in values or []:
        if not isinstance(item, Mapping):
            continue
        artifact_type = _trim(item.get("artifact_type"))
        if not artifact_type:
            continue
        if artifact_type not in latest:
            order.append(artifact_type)
        latest[artifact_type] = dict(item)
    return [latest[artifact_type] for artifact_type in order]


class CustomToolDeepSeekHarnessSessionService(
    FinanceDeepSeekHarnessSessionService
):
    """DSH Opt controller for custom-tool semantics; Codex remains coder."""

    def __init__(
        self,
        *,
        enabled: Optional[bool] = None,
        system_tools: Optional[FinanceCcSystemTools] = None,
        root_dir: str | Path = "data/custom_tool_dsh",
        log_path: str | Path = "outputs/custom_tool_dsh/events.jsonl",
        worker_count: Optional[int] = None,
        harness_factory: Optional[Callable[..., Any]] = None,
        loop_policy_config: Optional[Mapping[str, Any]] = None,
    ) -> None:
        resolved_enabled = (
            bool(enabled)
            if enabled is not None
            else _flag("FINANCE_DSH_CUSTOM_TOOL_ENABLED", True)
        )
        super().__init__(
            enabled=resolved_enabled,
            system_tools=system_tools or FinanceCcSystemTools(),
            root_dir=root_dir,
            log_path=log_path,
            worker_count=(
                worker_count
                if worker_count is not None
                else int(os.environ.get("FINANCE_DSH_CUSTOM_TOOL_WORKERS") or 2)
            ),
            harness_factory=harness_factory,
            loop_policy_config={"enabled": False},
        )
        self.queue_timeout_seconds = max(
            1.0,
            float(
                os.environ.get("FINANCE_DSH_CUSTOM_TOOL_QUEUE_TIMEOUT_SECONDS")
                or os.environ.get("FINANCE_DSH_QUEUE_TIMEOUT_SECONDS")
                or 300
            ),
        )
        self.turn_timeout_seconds = max(
            30.0,
            float(
                os.environ.get("FINANCE_DSH_CUSTOM_TOOL_TURN_TIMEOUT_SECONDS")
                or 300
            ),
        )
        self.max_tokens = max(
            1024,
            int(os.environ.get("FINANCE_DSH_CUSTOM_TOOL_MAX_TOKENS") or 8192),
        )
        self.model = _trim(
            os.environ.get("FINANCE_DSH_CUSTOM_TOOL_MODEL") or self.model
        )
        # Personalized-tool semantics are production MaaS traffic. Never fall
        # back to a developer's DeepSeek official endpoint or personal key.
        self.base_url = _trim(
            os.environ.get("FINANCE_DSH_CUSTOM_TOOL_BASE_URL")
            or os.environ.get("DASHSCOPE_BASE_URL")
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        self.api_key = _trim(os.environ.get("DASHSCOPE_API_KEY"))
        self._assert_dashscope_route()
        self.loop_policy_config = _policy_config(loop_policy_config)
        self.patch_path = (
            self.repo_root
            / "config"
            / "deepseek_harness"
            / "custom_tool.patch.yml"
        ).resolve()
        self.policy_plugin_path = (
            self.repo_root
            / "src"
            / "scenarios"
            / "custom_tool"
            / "dsh_loop_policy.mjs"
        ).resolve()
        self.system_prompt_path = (
            self.repo_root
            / "src"
            / "scenarios"
            / "custom_tool"
            / "dsh_system.md"
        ).resolve()
        self.system_prompt = self.system_prompt_path.read_text(encoding="utf-8")
        self.prompt_assets = {
            "global_system": {
                "path": str(self.system_prompt_path),
                "sha256": hashlib.sha256(
                    self.system_prompt.encode("utf-8")
                ).hexdigest(),
            },
            "stage_policy": {
                "path": str(self.policy_plugin_path),
                "sha256": hashlib.sha256(
                    self.policy_plugin_path.read_bytes()
                ).hexdigest(),
            },
            "skills": {
                stage: {
                    "path": str((self.repo_root / relative).resolve()),
                    "sha256": hashlib.sha256(
                        (self.repo_root / relative).read_bytes()
                    ).hexdigest(),
                }
                for stage, relative in {
                    "requirement": "src/skills/financial-tool-development/skills/financial-tool-requirement/SKILL.md",
                    "design": "src/skills/financial-tool-development/skills/financial-tool-design/SKILL.md",
                    "flow": "src/skills/financial-tool-development/skills/financial-tool-flowchart/SKILL.md",
                }.items()
            },
        }

    def _assert_dashscope_route(self) -> None:
        host = (urlparse(self.base_url).hostname or "").lower()
        if "dashscope" not in host or not host.endswith("aliyuncs.com"):
            raise ValueError(
                "自定义工具 DSH 必须连接阿里云 DashScope MaaS，"
                "请设置 DASHSCOPE_BASE_URL"
            )

    @staticmethod
    def initial_progress_event(
        context: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        state = (
            context.get("custom_tool_state")
            if isinstance(context, Mapping)
            and isinstance(context.get("custom_tool_state"), Mapping)
            else {}
        )
        stage = "design" if _trim(state.get("requirement_brief")) else "requirement"
        return {
            "source": "deepseek_harness",
            "type": "reasoning_summary_delta",
            "content": (
                "正在结合已保存资产继续形成或调整工具方案。"
                if stage == "design"
                else "正在结合金融语义梳理工具目标、关键规则和预期结果。"
            ),
            "metadata": {
                "stage": stage,
                "progress_id": "custom_tool_understanding",
                "title": "工具需求与设计",
                "status": "running",
            },
        }

    @staticmethod
    def _session_key(
        *,
        thread_id: int | str,
        owner_id: str,
        context: Mapping[str, Any],
    ) -> str:
        state = (
            context.get("custom_tool_state")
            if isinstance(context.get("custom_tool_state"), Mapping)
            else {}
        )
        flow_id = _trim(
            context.get("custom_tool_flow_id")
            or state.get("custom_tool_flow_id")
        )
        raw = f"{_trim(owner_id)}:{_trim(thread_id)}:{flow_id}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _prompt(user_text: str, context: Mapping[str, Any]) -> str:
        sections = [f"用户当前的问题是：\n{_trim(user_text)}"]
        ui_action = (
            context.get("ui_action")
            if isinstance(context.get("ui_action"), Mapping)
            else {}
        )
        action_label = _trim(ui_action.get("label") or ui_action.get("action_id"))
        if action_label:
            sections.append(f"用户同时通过界面提交了：{action_label}。")
        sections.append(
            "请结合当前持久会话和系统持有的工具状态处理本轮新增信息；"
            "需要精确内容时使用资产读取工具，不要要求模型重复输出系统事实。"
        )
        return "\n\n".join(sections)

    def run_turn(
        self,
        *,
        thread_id: int | str,
        owner_id: str,
        user_text: str,
        context: Optional[Mapping[str, Any]] = None,
        turn_id: int | str = "",
        event_sink: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError(
                "DSH 自定义工具路径未启用；请设置 FINANCE_DSH_CUSTOM_TOOL_ENABLED=1"
            )
        runtime_context = dict(context or {})
        key = self._session_key(
            thread_id=thread_id,
            owner_id=owner_id,
            context=runtime_context,
        )
        session_id = f"custom-tool-{key}"
        runtime_scope = f"custom_tool_dsh:{key}"
        tool_context = {
            **runtime_context,
            "_agent_runtime_scope": runtime_scope,
        }
        prompt = self._prompt(user_text, runtime_context)
        revision = uuid.uuid4().hex
        started = time.monotonic()
        with self._worker(key) as (worker, queue_wait_ms):
            catalog_revision = self._catalog_revision()
            if (
                worker.harness is not None
                and worker.catalog_revision != catalog_revision
            ):
                worker.harness.close()
                worker.harness = None
            _atomic_write(
                worker.context_path,
                {
                    "revision": revision,
                    "finance_catalog_revision": catalog_revision,
                    "owner_ids": [owner_id] if _trim(owner_id) else [],
                    "tool_context": tool_context,
                },
            )
            if worker.trace_path.is_file():
                worker.trace_path.unlink()
            resumed = session_id in worker.seen_sessions
            client_reused = worker.harness is not None
            if worker.harness is None:
                worker.harness = self._create_harness(worker)
                worker.catalog_revision = catalog_revision
            call_names: dict[str, str] = {}
            call_arguments: dict[str, dict[str, Any]] = {}

            def on_notification(notification: Any) -> None:
                if getattr(notification, "method", "") != "session.event":
                    return
                payload = getattr(notification, "payload", {})
                event = payload.get("event") if isinstance(payload, Mapping) else {}
                if not isinstance(event, Mapping):
                    return
                event_type = _trim(event.get("type"))
                data = _event_data(event)
                if event_type == "tool/call":
                    call_id = _trim(data.get("callId"))
                    name = _trim(data.get("name"))
                    arguments = _json_arguments(data.get("arguments"))
                    call_names[call_id] = name
                    call_arguments[call_id] = arguments
                    if name.endswith("save_finance_artifact"):
                        artifact_type = _trim(arguments.get("artifact_type"))
                        labels = {
                            "requirement": ("需求理解", "正在固化已收敛的需求。"),
                            "design": ("工具设计", "正在保存可实现的逻辑设计。"),
                            "flow": ("流程资产", "正在生成并保存业务流程图。"),
                        }
                        title, content = labels.get(
                            artifact_type,
                            ("工具资产", "正在保存工具资产。"),
                        )
                        self._emit(
                            event_sink,
                            content,
                            progress_id=call_id,
                            title=title,
                            status="running",
                        )
                    elif name.endswith("run_dynamic_tool"):
                        self._emit(
                            event_sink,
                            "正在按当前工具契约执行。",
                            progress_id=call_id,
                            title="工具执行",
                            status="running",
                        )
                elif event_type == "tool/result":
                    message = data.get("message")
                    source = message.get("source") if isinstance(message, Mapping) else {}
                    call_id = _trim(source.get("callId")) if isinstance(source, Mapping) else ""
                    name = call_names.get(call_id, "")
                    result_payload = _notification_tool_payload(data)
                    failed = bool(result_payload.get("error"))
                    if name.endswith("save_finance_artifact"):
                        artifact_type = _trim(
                            call_arguments.get(call_id, {}).get("artifact_type")
                        )
                        titles = {
                            "requirement": "需求理解",
                            "design": "工具设计",
                            "flow": "流程资产",
                        }
                        self._emit(
                            event_sink,
                            "资产保存未完成。" if failed else "资产已经保存。",
                            progress_id=call_id,
                            title=titles.get(artifact_type, "工具资产"),
                            status="error" if failed else "completed",
                        )
                    elif name.endswith("run_dynamic_tool"):
                        self._emit(
                            event_sink,
                            "工具执行未完成。" if failed else "工具已完成执行。",
                            progress_id=call_id,
                            title="工具执行",
                            status="error" if failed else "completed",
                        )

            try:
                result = worker.harness.run(
                    prompt,
                    session_id=session_id,
                    on_notification=on_notification,
                )
                worker.seen_sessions.add(session_id)
                events = [dict(item) for item in result.events]
                tracker = self._trace(worker, revision)
                artifact_updates = _coalesce_artifact_updates(
                    tracker.get("artifact_updates")
                )
                raw_interaction_requests = [
                    dict(item)
                    for item in tracker.get("interaction_requests") or []
                    if isinstance(item, Mapping)
                ]
                interaction_requests = (
                    [raw_interaction_requests[-1]]
                    if raw_interaction_requests
                    else []
                )
                implementation_requested = bool(
                    not interaction_requests
                    and any(
                        _trim(item.get("artifact_type")) == "flow"
                        for item in artifact_updates
                    )
                )
                finish_reason = _trim(result.finish_reason)
                terminal_tool_result = implementation_requested or bool(
                    interaction_requests
                )
                error = ""
                terminal_finish_warning = ""
                if terminal_tool_result and finish_reason not in {
                    "",
                    "completed",
                    "blocked",
                }:
                    # A validated flow/interaction tool result is the business
                    # checkpoint. Provider completion after that boundary is
                    # diagnostic only and must not discard the saved asset.
                    terminal_finish_warning = _finish_error(
                        events,
                        finish_reason,
                    )
                elif finish_reason and finish_reason != "completed" and not (
                    terminal_tool_result and finish_reason == "blocked"
                ):
                    error = _finish_error(events, finish_reason)
                final_response = _trim(result.final_response)
                if not final_response:
                    final_response = (
                        "需求、设计和流程资产已经形成，正在进入实现与验证。"
                        if implementation_requested
                        else "还有一项会影响核心结果的信息需要补充。"
                        if interaction_requests
                        else "本轮工具资产处理已经完成。"
                    )
                llm_step_usages = _llm_step_usages(events)
                record = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "thread_id": str(thread_id),
                    "turn_id": str(turn_id or ""),
                    "session_id": session_id,
                    "worker_index": worker.index,
                    "queue_wait_ms": queue_wait_ms,
                    "resumed": resumed,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                    "result": final_response,
                    "error": error,
                    "ok": not error,
                    "finish_reason": finish_reason,
                    "diagnostic_warning": terminal_finish_warning,
                    "client_reused": client_reused,
                    "finance_catalog_revision": catalog_revision,
                    "calls": [
                        dict(item)
                        for item in tracker.get("calls") or []
                        if isinstance(item, Mapping)
                    ],
                    "artifact_updates": artifact_updates,
                    "interaction_requests": interaction_requests,
                    "asset_reads": [
                        dict(item)
                        for item in tracker.get("asset_reads") or []
                        if isinstance(item, Mapping)
                    ],
                    "dynamic_runs": [
                        dict(item)
                        for item in tracker.get("dynamic_runs") or []
                        if isinstance(item, Mapping)
                    ],
                    "implementation_runs": [],
                    "implementation_requested": implementation_requested,
                    "result_refs": [
                        dict(item)
                        for item in tracker.get("result_refs") or []
                        if isinstance(item, Mapping)
                    ],
                    "llm_usage": _usage(llm_step_usages),
                    "llm_step_usages": llm_step_usages,
                    "model_name": _model_name(events, self.model),
                    "reasoning_effort": self.reasoning_effort,
                    "loop_policy": _custom_loop_observability(
                        events,
                        config=self.loop_policy_config,
                    ),
                    "prompt_assets": dict(self.prompt_assets),
                    "runtime": "dsh_opt",
                    "orchestrator": "dsh_opt",
                }
            except Exception as exc:
                if worker.harness is not None:
                    try:
                        worker.harness.close()
                    except Exception:
                        pass
                    worker.harness = None
                record = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "thread_id": str(thread_id),
                    "turn_id": str(turn_id or ""),
                    "session_id": session_id,
                    "worker_index": worker.index,
                    "queue_wait_ms": queue_wait_ms,
                    "resumed": resumed,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                    "result": "",
                    "error": str(exc)[:1000],
                    "ok": False,
                    "calls": [],
                    "artifact_updates": [],
                    "interaction_requests": [],
                    "asset_reads": [],
                    "dynamic_runs": [],
                    "implementation_runs": [],
                    "implementation_requested": False,
                    "result_refs": [],
                    "llm_usage": {},
                    "llm_step_usages": [],
                    "model_name": self.model,
                    "reasoning_effort": self.reasoning_effort,
                    "loop_policy": {
                        "enabled": bool(self.loop_policy_config.get("enabled")),
                        "config": dict(self.loop_policy_config),
                        "request_count": 0,
                        "requests": [],
                        "final_stage": "",
                    },
                    "prompt_assets": dict(self.prompt_assets),
                    "runtime": "dsh_opt",
                    "orchestrator": "dsh_opt",
                }
            self._append_record(record)
            return record
