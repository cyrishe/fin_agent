from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import shutil
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from src.scenarios.financial_qa.tools import FinanceDataQueryCcTools


_REASONING_EFFORTS = frozenset({"off", "low", "high", "max"})

_DEFAULT_LOOP_POLICY_CONFIG: dict[str, Any] = {
    "enabled": True,
    "preserveRequestPrefix": True,
    "maxCatalogAttempts": 6,
    "maxQueryAttempts": 3,
    "maxQueryRepairs": 1,
    "maxLoadAttempts": 2,
    "duplicateCallLimit": 1,
    "maxRequiredStageSteers": 1,
    "businessHint": "",
    "budgets": {
        "catalog": {"reasoningEffort": "low", "maxTokens": 1536},
        "query": {"reasoningEffort": "low", "maxTokens": 3072},
        "repair": {"reasoningEffort": "low", "maxTokens": 3072},
        "details": {"reasoningEffort": "off", "maxTokens": 2048},
        "final": {"reasoningEffort": "off", "maxTokens": 2048},
    },
}

_LOOP_MARKER = re.compile(
    r"\[FINANCE_LOOP\s+stage=(?P<stage>[a-z]+)\s+reason=(?P<reason>[a-z_]+)\]"
)


def _trim(value: Any) -> str:
    return str(value or "").strip()


def _flag(name: str, default: bool = False) -> bool:
    value = _trim(os.environ.get(name)).lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _reasoning_effort(value: Any) -> str:
    normalized = _trim(value or "low").lower()
    if normalized not in _REASONING_EFFORTS:
        raise ValueError(
            "FINANCE_DSH_REASONING_EFFORT 仅支持 off、low、high 或 max"
        )
    return normalized


def _merge_loop_policy_config(
    supplied: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    config = {
        **_DEFAULT_LOOP_POLICY_CONFIG,
        "budgets": {
            key: dict(value)
            for key, value in _DEFAULT_LOOP_POLICY_CONFIG["budgets"].items()
        },
    }
    env_value = _trim(os.environ.get("FINANCE_DSH_LOOP_POLICY_CONFIG"))
    layers: list[Mapping[str, Any]] = []
    if env_value:
        try:
            parsed = json.loads(env_value)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "FINANCE_DSH_LOOP_POLICY_CONFIG 必须是 JSON object"
            ) from exc
        if not isinstance(parsed, Mapping):
            raise ValueError("FINANCE_DSH_LOOP_POLICY_CONFIG 必须是 JSON object")
        layers.append(parsed)
    if supplied is not None:
        layers.append(supplied)
    for layer in layers:
        budgets = layer.get("budgets")
        config.update({key: value for key, value in layer.items() if key != "budgets"})
        if isinstance(budgets, Mapping):
            for stage, value in budgets.items():
                if isinstance(value, Mapping):
                    current = dict(config["budgets"].get(str(stage), {}))
                    current.update(dict(value))
                    config["budgets"][str(stage)] = current
    return config


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    temporary.replace(path)


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    temporary.replace(path)


def _event_data(event: Mapping[str, Any]) -> dict[str, Any]:
    value = event.get("data")
    return dict(value) if isinstance(value, Mapping) else {}


def _json_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _load_sdk_class() -> type[Any]:
    try:
        module = importlib.import_module("deepseek_harness")
    except ImportError:
        configured = _trim(os.environ.get("FINANCE_DSH_SDK_SOURCE"))
        adjacent = Path(__file__).resolve().parents[4] / "deepseek-harness" / "python" / "sdk" / "src"
        sdk_source = Path(configured).expanduser().resolve() if configured else adjacent.resolve()
        if not (sdk_source / "deepseek_harness" / "__init__.py").is_file():
            raise RuntimeError(
                "DeepSeek Harness Python SDK 不可用；请安装 deepseek-harness-sdk，"
                "或设置 FINANCE_DSH_SDK_SOURCE"
            )
        sys.path.insert(0, str(sdk_source))
        module = importlib.import_module("deepseek_harness")
    harness_class = getattr(module, "DeepSeekHarness", None)
    if harness_class is None:
        raise RuntimeError("DeepSeek Harness Python SDK 缺少 DeepSeekHarness")
    return harness_class


def _llm_step_usages(events: list[dict[str, Any]]) -> list[dict[str, int]]:
    steps: list[dict[str, int]] = []
    for event in events:
        if event.get("type") != "assistant/message":
            continue
        data = _event_data(event)
        usage = data.get("usage")
        if not isinstance(usage, Mapping):
            continue
        input_tokens = int(usage.get("inputTokens") or 0)
        cached = int(usage.get("cacheReadTokens") or 0)
        output_tokens = int(usage.get("outputTokens") or 0)
        reasoning_tokens = int(usage.get("reasoningTokens") or 0)
        steps.append(
            {
                "request_index": len(steps) + 1,
                "turn": int(data.get("turn") or 0),
                "step": int(data.get("step") or 0),
                "input_tokens": input_tokens,
                "cache_read_tokens": cached,
                "context_tokens": input_tokens + cached,
                "output_tokens": output_tokens,
                "reasoning_tokens": reasoning_tokens,
                "non_reasoning_output_tokens": max(
                    0, output_tokens - reasoning_tokens
                ),
            }
        )
    return steps


def _usage(
    steps: list[dict[str, int]],
) -> dict[str, int | float]:
    prompt_tokens = sum(item["input_tokens"] for item in steps)
    completion_tokens = sum(item["output_tokens"] for item in steps)
    cache_read_tokens = sum(item["cache_read_tokens"] for item in steps)
    reasoning_tokens = sum(item["reasoning_tokens"] for item in steps)
    context_tokens = sum(item["context_tokens"] for item in steps)
    call_count = len(steps)
    # DSH reports cache misses and cache reads separately.  Keep prompt_tokens
    # aligned with the existing CC field while making full per-request context
    # processing observable without counting cache reads as uncached billing.
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cumulative_context_tokens": context_tokens,
        "mean_context_tokens_per_call": (
            round(context_tokens / call_count, 2) if call_count else 0.0
        ),
        "max_context_tokens_per_call": max(
            (item["context_tokens"] for item in steps),
            default=0,
        ),
        "final_context_tokens": (
            steps[-1]["context_tokens"] if steps else 0
        ),
        "reasoning_tokens": reasoning_tokens,
        "non_reasoning_completion_tokens": max(
            0, completion_tokens - reasoning_tokens
        ),
        "call_count": call_count,
    }


def _event_tool_calls(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for event in events:
        if event.get("type") != "tool/call":
            continue
        data = _event_data(event)
        name = _trim(data.get("name"))
        if name.startswith("mcp__finance__"):
            name = name[len("mcp__finance__") :]
        calls.append(
            {
                "tool": name,
                "arguments": _json_arguments(data.get("arguments")),
                "call_id": _trim(data.get("callId")),
            }
        )
    return calls


def _model_name(events: list[dict[str, Any]], fallback: str) -> str:
    for event in reversed(events):
        if event.get("type") != "assistant/message":
            continue
        message = _event_data(event).get("message")
        source = message.get("source") if isinstance(message, Mapping) else {}
        if isinstance(source, Mapping) and _trim(source.get("model")):
            return _trim(source.get("model"))
    return fallback


def _short_tool_name(value: Any) -> str:
    name = _trim(value)
    return name[len("mcp__finance__") :] if name.startswith("mcp__finance__") else name


def _text_blocks(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        direct = value.get("text")
        texts = [direct] if isinstance(direct, str) else []
        content = value.get("content")
        if content is not None:
            texts.extend(_text_blocks(content))
        return texts
    if isinstance(value, (list, tuple)):
        texts: list[str] = []
        for item in value:
            texts.extend(_text_blocks(item))
        return texts
    return []


def _injected_stage_prompt(header: Mapping[str, Any]) -> dict[str, Any]:
    candidates: list[tuple[str, str]] = []
    system = header.get("system")
    if isinstance(system, str):
        candidates.append(("system", system))
    for message in header.get("messages") or []:
        if not isinstance(message, Mapping):
            continue
        for text in _text_blocks(message.get("content")):
            candidates.append(("message", text))
    # Earlier stage steering messages remain in the conversation. The last
    # marker in the effective request is the prompt injected for this step.
    for surface, text in reversed(candidates):
        matches = list(_LOOP_MARKER.finditer(text))
        if not matches:
            continue
        match = matches[-1]
        return {
            "stage": match.group("stage"),
            "stage_reason": match.group("reason"),
            "prompt_injected": True,
            "prompt_surface": surface,
            "prompt_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "prompt_chars": len(text),
        }
    return {
        "stage": "",
        "stage_reason": "",
        "prompt_injected": False,
        "prompt_surface": "",
        "prompt_sha256": "",
        "prompt_chars": 0,
    }


def _loop_policy_observability(
    events: list[dict[str, Any]],
    *,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    header: dict[str, Any] = {}
    active_injected_prompt: dict[str, Any] = {}
    request_index = 0
    requests: list[dict[str, Any]] = []
    calls_by_step: dict[tuple[int, int], list[str]] = {}
    for event in events:
        event_type = _trim(event.get("type"))
        data = _event_data(event)
        if event_type == "user/message":
            candidate = _injected_stage_prompt({"messages": [data]})
            if candidate["prompt_injected"]:
                active_injected_prompt = candidate
            continue
        if event_type == "request/header":
            value = data.get("header")
            if isinstance(value, Mapping):
                header = dict(value)
            continue
        if event_type == "tool/call":
            key = (int(data.get("turn") or 0), int(data.get("step") or 0))
            calls_by_step.setdefault(key, []).append(_short_tool_name(data.get("name")))
            continue
        if event_type != "assistant/message":
            continue
        request_index += 1
        call_config = (
            header.get("config") if isinstance(header.get("config"), Mapping) else {}
        )
        injected_prompt = _injected_stage_prompt(header)
        if (
            not injected_prompt["prompt_injected"]
            and active_injected_prompt
        ):
            injected_prompt = active_injected_prompt
        tools = header.get("tools") if isinstance(header.get("tools"), list) else []
        visible_tools = [
            _short_tool_name(item.get("name"))
            for item in tools
            if isinstance(item, Mapping) and _trim(item.get("name"))
        ]
        turn = int(data.get("turn") or 0)
        step = int(data.get("step") or 0)
        usage = data.get("usage") if isinstance(data.get("usage"), Mapping) else {}
        output_tokens = int(usage.get("outputTokens") or 0)
        reasoning_tokens = int(usage.get("reasoningTokens") or 0)
        max_tokens = int(call_config.get("maxTokens") or 0)
        requests.append(
            {
                "request_index": request_index,
                "turn": turn,
                "step": step,
                "stage": injected_prompt["stage"],
                "stage_reason": injected_prompt["stage_reason"],
                "prompt_injected": injected_prompt["prompt_injected"],
                "prompt_surface": injected_prompt["prompt_surface"],
                "prompt_sha256": injected_prompt["prompt_sha256"],
                "prompt_chars": injected_prompt["prompt_chars"],
                "reasoning_effort": _trim(call_config.get("reasoningEffort")),
                "max_tokens": max_tokens,
                "visible_tools": visible_tools,
                "called_tools": [],
                "context_tokens": int(usage.get("inputTokens") or 0)
                + int(usage.get("cacheReadTokens") or 0),
                "output_tokens": output_tokens,
                "reasoning_tokens": reasoning_tokens,
                "visible_output_tokens": max(0, output_tokens - reasoning_tokens),
                "reasoning_share": (
                    round(reasoning_tokens / output_tokens, 4)
                    if output_tokens
                    else 0.0
                ),
                "max_token_hit": bool(
                    max_tokens and output_tokens >= max_tokens - 1
                ),
            }
        )
    for request in requests:
        request["called_tools"] = calls_by_step.get(
            (int(request["turn"]), int(request["step"])),
            [],
        )
        if not request["stage"]:
            called = set(request["called_tools"])
            request["stage"] = (
                "catalog"
                if "read_finance_catalog" in called
                else "query"
                if "finance_query" in called
                else "details"
                if "load_finance_result" in called
                else "final"
            )
            request["stage_inferred_from_calls"] = True
        else:
            request["stage_inferred_from_calls"] = False
    return {
        "enabled": bool(config.get("enabled")),
        "config": dict(config),
        "request_count": len(requests),
        "requests": requests,
        "final_stage": requests[-1]["stage"] if requests else "",
        "final_stage_reason": requests[-1]["stage_reason"] if requests else "",
    }


@dataclass
class _DshWorker:
    index: int
    home: Path
    context_path: Path
    trace_path: Path
    lock: threading.Lock = field(default_factory=threading.Lock)
    harness: Any = None
    catalog_revision: str = ""
    seen_sessions: set[str] = field(default_factory=set)


class FinanceDeepSeekHarnessSessionService:
    """Run financial-data turns on reusable, isolated DeepSeek Harness workers."""

    def __init__(
        self,
        *,
        enabled: Optional[bool] = None,
        system_tools: Optional[FinanceDataQueryCcTools] = None,
        root_dir: str | Path = "data/financial_qa_dsh",
        log_path: str | Path = "outputs/financial_qa_dsh/events.jsonl",
        worker_count: Optional[int] = None,
        harness_factory: Optional[Callable[..., Any]] = None,
        loop_policy_config: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.enabled = (
            bool(enabled)
            if enabled is not None
            else _flag("FINANCE_DSH_FINANCIAL_QA_ENABLED")
        )
        self.system_tools = system_tools or FinanceDataQueryCcTools()
        self.root_dir = Path(root_dir).resolve()
        self.log_path = Path(log_path).resolve()
        self.worker_count = max(
            1,
            int(
                worker_count
                if worker_count is not None
                else os.environ.get("FINANCE_DSH_WORKERS") or 10
            ),
        )
        self.queue_timeout_seconds = max(
            1.0,
            float(
                os.environ.get("FINANCE_DSH_QUEUE_TIMEOUT_SECONDS")
                or os.environ.get("FINANCE_DSH_TURN_TIMEOUT_SECONDS")
                or 300
            ),
        )
        self.provider = _trim(
            os.environ.get("FINANCE_DSH_PROVIDER") or "deepseek-official"
        )
        self.model = _trim(
            os.environ.get("FINANCE_DSH_MODEL") or "deepseek-v4-flash"
        )
        # Dedicated variables keep the server runtime independent from a
        # developer's personal DeepSeek environment. Legacy DEEPSEEK_* values
        # remain a compatibility fallback for existing deployments.
        self.base_url = _trim(
            os.environ.get("FINANCE_DSH_BASE_URL")
            or os.environ.get("DEEPSEEK_BASE_URL")
        )
        self.api_key = _trim(
            os.environ.get("FINANCE_DSH_API_KEY")
            or os.environ.get("DEEPSEEK_API_KEY")
        )
        self.reasoning_effort = _reasoning_effort(
            os.environ.get("FINANCE_DSH_REASONING_EFFORT")
        )
        self.max_tokens = max(
            1024, int(os.environ.get("FINANCE_DSH_MAX_TOKENS") or 8192)
        )
        self.turn_timeout_seconds = max(
            30.0,
            float(os.environ.get("FINANCE_DSH_TURN_TIMEOUT_SECONDS") or 300),
        )
        self.loop_policy_config = _merge_loop_policy_config(loop_policy_config)
        self.repo_root = Path(__file__).resolve().parents[3]
        self.patch_path = (
            self.repo_root
            / "config"
            / "deepseek_harness"
            / "finance_query.patch.yml"
        ).resolve()
        self.policy_plugin_path = (
            self.repo_root
            / "src"
            / "scenarios"
            / "financial_qa"
            / "dsh_loop_policy.mjs"
        ).resolve()
        self.system_prompt_path = (
            self.repo_root
            / "src"
            / "scenarios"
            / "financial_qa"
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
        }
        self._harness_factory = harness_factory
        self._workers = [
            _DshWorker(
                index=index,
                home=self.root_dir / "workers" / str(index) / "home",
                context_path=self.root_dir / "workers" / str(index) / "turn_context.json",
                trace_path=self.root_dir / "workers" / str(index) / "turn_trace.json",
            )
            for index in range(self.worker_count)
        ]
        self._log_lock = threading.Lock()
        self._worker_condition = threading.Condition()
        self._session_workers: dict[str, int] = {}
        self._next_worker_index = 0

    def _dsh_bin(self) -> Path:
        configured = _trim(os.environ.get("FINANCE_DSH_BIN"))
        if configured:
            path = Path(configured).expanduser().resolve()
        else:
            installed = shutil.which("dsh")
            path = (
                Path(installed).resolve()
                if installed
                else self.repo_root / "scripts" / "dsh_source_runtime.py"
            )
        if not path.is_file():
            raise RuntimeError(
                "DeepSeek Harness runtime 不可用；请安装 dsh 或设置 FINANCE_DSH_BIN"
            )
        return path

    def _create_harness(self, worker: _DshWorker) -> Any:
        factory = self._harness_factory or _load_sdk_class()
        worker.home.mkdir(parents=True, exist_ok=True)
        policy_patch = worker.home / "finance_loop_policy.patch.json"
        _atomic_write_json(
            policy_patch,
            [
                {
                    "insert": [
                        {
                            "id": "fin-agent-finance-loop-policy",
                            "name": str(self.policy_plugin_path),
                            "config": dict(self.loop_policy_config),
                        }
                    ]
                }
            ],
        )
        env = {
            "DSH_SYSTEM_PROMPT": self.system_prompt,
            "FIN_AGENT_ROOT": str(self.repo_root),
            "FIN_AGENT_DSH_PYTHON": sys.executable,
            "FIN_AGENT_DSH_CONTEXT_PATH": str(worker.context_path),
            "FIN_AGENT_DSH_TRACE_PATH": str(worker.trace_path),
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
            patches=(str(self.patch_path), str(policy_patch)),
            dsh_home=str(worker.home),
            env=env,
            base_url=self.base_url or None,
            api_key=self.api_key or None,
            initialize_timeout_seconds=120.0,
            request_timeout_seconds=self.turn_timeout_seconds,
            shutdown_timeout_seconds=3.0,
        )

    @staticmethod
    def _key(*, thread_id: int | str, owner_id: str) -> str:
        raw = f"{_trim(owner_id)}:{_trim(thread_id)}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    @contextmanager
    def _worker(self, key: str, *, retain_affinity: bool = True):
        """Lease one worker while preserving session-to-worker affinity.

        A new session takes any currently available worker instead of being
        hash-pinned to a busy slot.  A resumed session waits for its original
        worker because the DSH session state belongs to that harness process.
        """

        started = time.monotonic()
        deadline = started + self.queue_timeout_seconds
        worker: _DshWorker | None = None
        with self._worker_condition:
            while worker is None:
                preferred = self._session_workers.get(key) if retain_affinity else None
                if preferred is not None:
                    candidate_indexes = [preferred]
                else:
                    candidate_indexes = [
                        (self._next_worker_index + offset) % len(self._workers)
                        for offset in range(len(self._workers))
                    ]
                for index in candidate_indexes:
                    candidate = self._workers[index]
                    if candidate.lock.acquire(blocking=False):
                        worker = candidate
                        if preferred is None and retain_affinity:
                            self._session_workers[key] = index
                        if preferred is None:
                            self._next_worker_index = (index + 1) % len(self._workers)
                        break
                if worker is not None:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        "DeepSeek Harness worker queue timed out after "
                        f"{self.queue_timeout_seconds:g}s"
                    )
                # Timed wakeups also cover a worker held by prewarm/close,
                # which does not participate in the scheduler condition.
                self._worker_condition.wait(timeout=min(remaining, 0.1))
        try:
            yield worker, round((time.monotonic() - started) * 1000)
        finally:
            worker.lock.release()
            with self._worker_condition:
                self._worker_condition.notify_all()

    def _catalog_revision(self) -> str:
        revision_reader = getattr(
            self.system_tools.finance_catalog,
            "catalog_revision",
            None,
        )
        return _trim(revision_reader()) if callable(revision_reader) else ""

    @staticmethod
    def _emit(
        event_sink: Optional[Callable[[dict[str, Any]], None]],
        content: str,
        *,
        progress_id: str,
        title: str,
        status: str,
    ) -> None:
        if event_sink is None:
            return
        event_sink(
            {
                "source": "deepseek_harness",
                "type": "reasoning_summary_delta",
                "content": content,
                "metadata": {
                    "stage": "runtime",
                    "progress_id": progress_id,
                    "title": title,
                    "status": status,
                },
            }
        )

    def _prompt(
        self,
        user_text: str,
        *,
        runtime_context: Mapping[str, Any],
        working_set: str,
    ) -> str:
        sections = [_trim(user_text)]
        if bool(runtime_context.get("_finance_data_only")):
            sections.append(
                "[系统记录的本轮输出模式]\n"
                "仅取数：只查询用户明确要求的原始数据，不添加解释性补充目标；"
                "按 finance_query.data_request_complete 参数说明声明最终取数 flow；"
                "完成后无需生成自然语言回答。"
            )
        mode_prompt = _trim(runtime_context.get("_finance_research_mode_prompt"))
        if mode_prompt:
            sections.append(f"[本轮回答要求]\n{mode_prompt}")
        if working_set:
            sections.append(f"[系统持有的当前金融结果索引]\n{working_set}")
        return "\n\n".join(item for item in sections if item)

    def _trace(self, worker: _DshWorker, revision: str) -> dict[str, Any]:
        if not worker.trace_path.is_file():
            return {}
        try:
            payload = json.loads(worker.trace_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, Mapping) or _trim(payload.get("revision")) != revision:
            return {}
        tracker = payload.get("tracker")
        return dict(tracker) if isinstance(tracker, Mapping) else {}

    def _append_record(self, record: Mapping[str, Any]) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._log_lock, self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(record), ensure_ascii=False, default=str) + "\n")

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
                "DSH 金融查询路径未启用；请设置 FINANCE_DSH_FINANCIAL_QA_ENABLED=1"
            )
        key = self._key(thread_id=thread_id, owner_id=owner_id)
        isolated_request = bool((context or {}).get("_finance_isolated_request"))
        session_id = f"financial-qa-{key}"
        runtime_scope = f"financial_qa_dsh:{key}"
        tool_context = {
            "_agent_runtime_scope": runtime_scope,
            "_finance_data_only": bool((context or {}).get("_finance_data_only")),
        }
        host_runtime = self.system_tools.create_runtime()
        host_runtime.begin_turn(
            owner_ids=[owner_id],
            tool_context=tool_context,
        )
        prompt = self._prompt(
            user_text,
            runtime_context=context or {},
            working_set=host_runtime.current_context_prompt(),
        )
        revision = uuid.uuid4().hex
        started = time.monotonic()
        with self._worker(
            key,
            retain_affinity=not isolated_request,
        ) as (worker, queue_wait_ms):
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
                    "owner_ids": [owner_id],
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
                    call_names[call_id] = name
                    if name.endswith("read_finance_catalog"):
                        self._emit(event_sink, "正在确认数据字段与口径。", progress_id=call_id, title="数据口径", status="running")
                    elif name.endswith("finance_query"):
                        self._emit(event_sink, "正在执行金融数据查询。", progress_id=call_id, title="数据查询", status="running")
                    elif name.endswith("load_finance_result"):
                        self._emit(event_sink, "正在读取必要的结果明细。", progress_id=call_id, title="结果明细", status="running")
                elif event_type == "tool/result":
                    message = data.get("message")
                    source = message.get("source") if isinstance(message, Mapping) else {}
                    call_id = _trim(source.get("callId")) if isinstance(source, Mapping) else ""
                    name = call_names.get(call_id, "")
                    title = "数据查询" if name.endswith("finance_query") else "数据口径"
                    self._emit(event_sink, "本步数据处理已完成。", progress_id=call_id, title=title, status="completed")

            self._emit(
                event_sink,
                "正在理解问题并规划最小数据查询。",
                progress_id=f"dsh_turn_{turn_id}",
                title="金融数据分析",
                status="running",
            )
            try:
                result = worker.harness.run(
                    prompt,
                    session_id=session_id,
                    on_notification=on_notification,
                )
                if not isolated_request:
                    worker.seen_sessions.add(session_id)
                events = [dict(item) for item in result.events]
                llm_step_usages = _llm_step_usages(events)
                tracker = self._trace(worker, revision)
                tool_calls = [
                    dict(item)
                    for item in tracker.get("calls") or []
                    if isinstance(item, Mapping)
                ] or _event_tool_calls(events)
                result_refs = [
                    dict(item)
                    for item in tracker.get("result_refs") or []
                    if isinstance(item, Mapping)
                ]
                finish_reason = _trim(result.finish_reason)
                data_only_requested = bool(
                    (context or {}).get("_finance_data_only")
                )
                data_only_has_results = bool(
                    data_only_requested and result_refs
                )
                data_only_early_stop = bool(
                    data_only_has_results and finish_reason == "blocked"
                )
                error = ""
                if (
                    finish_reason
                    and finish_reason != "completed"
                    and not data_only_early_stop
                ):
                    error = f"DeepSeek Harness turn ended with {finish_reason}"
                final_response = (
                    "" if data_only_has_results else _trim(result.final_response)
                )
                if "<｜｜DSML｜｜tool_calls>" in final_response:
                    error = "DeepSeek Harness emitted an unavailable tool call as text"
                record = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "thread_id": str(thread_id),
                    "turn_id": str(turn_id or ""),
                    "session_id": session_id,
                    "worker_index": worker.index,
                    "queue_wait_ms": queue_wait_ms,
                    "isolated_request": isolated_request,
                    "resumed": resumed,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                    "result": final_response,
                    "error": error,
                    "finish_reason": finish_reason,
                    "assistant_message_count": sum(
                        item.get("type") == "assistant/message" for item in events
                    ),
                    "tool_result_message_count": sum(
                        item.get("type") == "tool/result" for item in events
                    ),
                    "client_reused": client_reused,
                    "client_prewarmed": False,
                    "finance_catalog_revision": catalog_revision,
                    "tool_calls": tool_calls,
                    "agent_tool_names": sorted(
                        {_trim(item.get("tool")) for item in tool_calls if _trim(item.get("tool"))}
                    ),
                    "skill_results": [],
                    "skill_entries": [],
                    "result_refs": result_refs,
                    "llm_usage": _usage(llm_step_usages),
                    "llm_step_usages": llm_step_usages,
                    "model_name": _model_name(events, self.model),
                    "reasoning_effort": self.reasoning_effort,
                    "loop_policy": _loop_policy_observability(
                        events,
                        config=self.loop_policy_config,
                    ),
                    "prompt_assets": dict(self.prompt_assets),
                    "runtime": "dsh",
                    "data_only_complete": bool(
                        data_only_has_results
                        and finish_reason in {"completed", "blocked"}
                    ),
                    "data_only_early_stop": data_only_early_stop,
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
                    "isolated_request": isolated_request,
                    "resumed": resumed,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                    "result": "",
                    "error": str(exc)[:1000],
                    "assistant_message_count": 0,
                    "tool_result_message_count": 0,
                    "client_reused": client_reused,
                    "client_prewarmed": False,
                    "finance_catalog_revision": catalog_revision,
                    "tool_calls": [],
                    "agent_tool_names": [],
                    "skill_results": [],
                    "skill_entries": [],
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
                        "final_stage_reason": "",
                    },
                    "prompt_assets": dict(self.prompt_assets),
                    "runtime": "dsh",
                }
            self._append_record(record)
            return record

    def prewarm(self, *, timeout: float = 120.0) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "enabled": False, "runtime": "dsh"}
        target_count = max(
            1,
            min(
                self.worker_count,
                int(
                    os.environ.get("FINANCE_DSH_PREWARM_WORKERS")
                    or self.worker_count
                ),
            ),
        )

        def start_worker(worker: _DshWorker) -> int:
            with worker.lock:
                revision = uuid.uuid4().hex
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
                        "owner_ids": ["prewarm"],
                        "tool_context": {
                            "_agent_runtime_scope": (
                                f"financial_qa_dsh:prewarm:{worker.index}"
                            )
                        },
                    },
                )
                if worker.harness is None:
                    worker.harness = self._create_harness(worker)
                    worker.catalog_revision = catalog_revision
                worker.harness.start()
                return worker.index

        with ThreadPoolExecutor(
            max_workers=target_count,
            thread_name_prefix="finance-dsh-prewarm",
        ) as executor:
            ready = list(executor.map(start_worker, self._workers[:target_count]))
        return {
            "ok": True,
            "enabled": True,
            "runtime": "dsh",
            "timeout": timeout,
            "workers_ready": len(ready),
            "worker_indexes": ready,
        }

    def close(self) -> None:
        for worker in self._workers:
            with worker.lock:
                if worker.harness is None:
                    continue
                try:
                    worker.harness.close()
                finally:
                    worker.harness = None
