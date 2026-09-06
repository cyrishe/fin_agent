from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from urllib.parse import urlparse
import uuid

from src.scenarios.financial_qa.dsh_service import (
    FinanceDeepSeekHarnessSessionService,
    _DshWorker,
    _llm_step_usages,
    _load_sdk_class,
    _model_name,
    _trim,
    _usage,
)


def _extract_json_object(text: str) -> dict[str, Any]:
    source = _trim(text)
    start = source.find("{")
    if start < 0:
        raise ValueError("DSH 入口路由没有返回 JSON object")
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(source)):
        char = source[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                value = json.loads(source[start : index + 1])
                if not isinstance(value, dict):
                    break
                return value
    raise ValueError("DSH 入口路由返回的 JSON object 不完整")


class CustomToolIntentDshRouter(FinanceDeepSeekHarnessSessionService):
    """Route custom-tool intent through DSH without another model framework."""

    def __init__(
        self,
        *,
        enabled: Optional[bool] = None,
        root_dir: str | Path = "data/custom_tool_intent_dsh",
        log_path: str | Path = "outputs/custom_tool_intent_dsh/events.jsonl",
        worker_count: Optional[int] = None,
        harness_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        resolved_enabled = (
            bool(enabled)
            if enabled is not None
            else _trim(
                os.environ.get("FINANCE_DSH_CUSTOM_TOOL_INTENT_ENABLED") or "1"
            ).lower()
            in {"1", "true", "yes", "on"}
        )
        super().__init__(
            enabled=resolved_enabled,
            root_dir=root_dir,
            log_path=log_path,
            worker_count=(
                worker_count
                if worker_count is not None
                else int(
                    os.environ.get("FINANCE_DSH_CUSTOM_TOOL_INTENT_WORKERS") or 2
                )
            ),
            harness_factory=harness_factory,
            loop_policy_config={"enabled": False},
        )
        self.provider = _trim(
            os.environ.get("FINANCE_DSH_CUSTOM_TOOL_PROVIDER")
            or "deepseek-official"
        )
        self.model = _trim(
            os.environ.get("FINANCE_DSH_CUSTOM_TOOL_MODEL")
            or "deepseek-v4-flash"
        )
        self.reasoning_effort = "off"
        self.max_tokens = max(
            256,
            int(
                os.environ.get("FINANCE_DSH_CUSTOM_TOOL_INTENT_MAX_TOKENS")
                or 768
            ),
        )
        self.turn_timeout_seconds = max(
            30.0,
            float(
                os.environ.get("FINANCE_DSH_CUSTOM_TOOL_INTENT_TIMEOUT_SECONDS")
                or 90
            ),
        )
        self.base_url = _trim(
            os.environ.get("FINANCE_DSH_CUSTOM_TOOL_BASE_URL")
            or os.environ.get("DASHSCOPE_BASE_URL")
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        self.api_key = _trim(os.environ.get("DASHSCOPE_API_KEY"))
        host = (urlparse(self.base_url).hostname or "").lower()
        if "dashscope" not in host or not host.endswith("aliyuncs.com"):
            raise ValueError(
                "自定义工具入口 DSH 必须连接阿里云 DashScope MaaS"
            )
        self.patch_path = (
            self.repo_root
            / "config"
            / "deepseek_harness"
            / "custom_tool_intent.patch.yml"
        ).resolve()
        self.system_prompt_path = (
            self.repo_root
            / "src"
            / "scenarios"
            / "custom_tool"
            / "dsh_intent_system.md"
        ).resolve()
        self.system_prompt = self.system_prompt_path.read_text(encoding="utf-8")

    def _catalog_revision(self) -> str:
        return ""

    def _create_harness(self, worker: _DshWorker) -> Any:
        if not self.api_key:
            raise RuntimeError(
                "DASHSCOPE_API_KEY 未配置，自定义工具入口不会回退到个人模型服务"
            )
        factory = self._harness_factory or _load_sdk_class()
        worker.home.mkdir(parents=True, exist_ok=True)
        env = {
            "DSH_SYSTEM_PROMPT": self.system_prompt,
            "FIN_AGENT_ROOT": str(self.repo_root),
        }
        source_root = _trim(os.environ.get("FINANCE_DSH_SOURCE_ROOT"))
        if source_root:
            env["FINANCE_DSH_SOURCE_ROOT"] = source_root
        return factory(
            provider=self.provider,
            model=self.model,
            reasoning_effort=self.reasoning_effort,
            max_tokens=self.max_tokens,
            cwd=str(self.repo_root),
            runtime_cwd=str(self.repo_root),
            dsh_bin=str(self._dsh_bin()),
            profile="sdk-minimal",
            patches=(str(self.patch_path),),
            dsh_home=str(worker.home),
            env=env,
            base_url=self.base_url,
            api_key=self.api_key,
            initialize_timeout_seconds=120.0,
            request_timeout_seconds=self.turn_timeout_seconds,
            shutdown_timeout_seconds=3.0,
        )

    @staticmethod
    def _active_tool_summary(
        thread_context: Optional[Mapping[str, Any]],
    ) -> dict[str, Any]:
        context = thread_context if isinstance(thread_context, Mapping) else {}
        state = (
            context.get("custom_tool_state")
            if isinstance(context.get("custom_tool_state"), Mapping)
            else {}
        )
        design = (
            state.get("design_contract")
            if isinstance(state.get("design_contract"), Mapping)
            else {}
        )
        return {
            "active": bool(state),
            "tool_name": _trim(state.get("tool_name")),
            "requirement_brief": _trim(state.get("requirement_brief"))[:500],
            "display_name": _trim(design.get("display_name"))[:160],
            "has_design": bool(design),
            "has_implementation": bool(
                state.get("implementation_revision") or state.get("code")
            ),
            "questions": [
                _trim(item.get("question"))[:240]
                for item in state.get("questions") or []
                if isinstance(item, Mapping) and _trim(item.get("question"))
            ][:3],
        }

    def route(
        self,
        *,
        text: str,
        thread_context: Optional[Mapping[str, Any]] = None,
        application_context: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        del application_context
        if not self.enabled:
            raise RuntimeError("自定义工具 DSH 入口路由未启用")
        user_text = _trim(text)
        if not user_text:
            raise ValueError("自定义工具入口路由缺少用户文本")
        prompt = (
            f"[用户本轮输入]\n{user_text}\n\n"
            "[系统持有的当前工具摘要]\n"
            f"{json.dumps(self._active_tool_summary(thread_context), ensure_ascii=False)}"
        )
        key = hashlib.sha256(uuid.uuid4().bytes).hexdigest()[:32]
        with self._worker(key, retain_affinity=False) as (worker, queue_wait_ms):
            client_reused = worker.harness is not None
            if worker.harness is None:
                worker.harness = self._create_harness(worker)
                worker.catalog_revision = ""
            session_id = f"custom-tool-intent-{uuid.uuid4().hex}"
            result = worker.harness.run(prompt, session_id=session_id)
            finish_reason = _trim(result.finish_reason)
            if finish_reason and finish_reason != "completed":
                raise RuntimeError(
                    f"自定义工具 DSH 入口路由未完成：{finish_reason}"
                )
            payload = _extract_json_object(result.final_response)
            if not isinstance(payload.get("is_custom_tool"), bool):
                raise ValueError("DSH 入口路由缺少 boolean is_custom_tool")
            resolved_question = _trim(payload.get("resolved_question")) or user_text
            events = [dict(item) for item in result.events]
            step_usages = _llm_step_usages(events)
            return {
                "is_custom_tool": payload["is_custom_tool"],
                "resolved_question": resolved_question,
                "reason": _trim(payload.get("reason"))[:500],
                "runtime": "dsh_opt",
                "model_name": _model_name(events, self.model),
                "llm_usage": _usage(step_usages),
                "queue_wait_ms": queue_wait_ms,
                "client_reused": client_reused,
            }
