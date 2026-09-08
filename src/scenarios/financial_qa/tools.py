from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, List, Mapping, Optional

from src.experiments.staged_data_protocol.phase2.models import ResultHandle
from src.experiments.staged_data_protocol.phase2.catalog import OPERATION_DESCRIPTIONS
from src.experiments.staged_data_protocol.phase2.trade_date_resolver import TradeDateResolver
from src.backtest import BacktestError
from src.scenarios.financial_qa.result_registry import FinanceResultRegistry
from src.scenarios.financial_qa.query_recovery import (
    provider_retry_allowed,
    query_recovery,
)
from src.services.backtest_run_service import BacktestRunService
from src.services.finance_data_tool_catalog_service import FinanceDataToolCatalogService
from src.services.finance_data_tool_runtime_service import FinanceDataToolRuntimeService
from src.services.invocation_input_resolver_service import InvocationInputResolverService
from src.services.session_variable_store_service import SessionVariableStoreService
from src.skill_runtime.tool_adapter import ToolAdapter


def _trim(value: Any) -> str:
    return str(value or "").strip()


def _request_api_name(request: Any) -> str:
    match = re.search(
        r"=\s*([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)\s*\(",
        _trim(request),
    )
    return match.group(1) if match else ""


def _query_progress_title(label: str, step: int, total: int) -> str:
    base = f"查询{label}" if label else "查询金融数据"
    return f"{base} {step}/{total}" if total > 1 else base


def _result_rows(handle: ResultHandle) -> list[dict[str, Any]]:
    data = handle.data
    rows = data.get("rows") if isinstance(data, Mapping) else data
    if not isinstance(rows, list):
        return []
    return [dict(item) for item in rows if isinstance(item, Mapping)]


_OPERATION_SELECTION_DESCRIPTION = "\n".join(
    f"{name}: {description}" for name, description in OPERATION_DESCRIPTIONS.items()
) + "\n按本次所需数据形态选择；省略时查看该视图的全部方法。"


def _canonical_skill_id(value: Any) -> str:
    normalized = _trim(value)
    return normalized.rsplit(":", 1)[-1] if normalized else ""


def _tool_result(payload: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(dict(payload), ensure_ascii=False, default=str),
            }
        ]
    }


def _model_step_summary(summary: Mapping[str, Any]) -> Dict[str, Any]:
    """Project one persisted query summary to the smaller model-facing contract.

    The tracker keeps the richer record for presentation and observability.  The
    model already has the submitted request in its tool call and the stable
    recovery rules in the tool instructions, so neither needs to be repeated in
    every successful tool result.
    """

    evidence = (
        summary.get("step_evidence")
        if isinstance(summary.get("step_evidence"), Mapping)
        else {}
    )
    compact_evidence = {
        key: evidence.get(key)
        for key in (
            "execution_completed",
            "selection_applied",
            "populated_columns",
            "available_refs",
            "unavailable_columns",
            "sample_complete",
            "guidance",
        )
        if evidence.get(key) not in (None, "", [], {})
    }
    projected: Dict[str, Any] = {}
    for key in (
        "flow_step",
        "goal",
        "api",
        "result_name",
        "result_ref",
        "data_type",
        "row_count",
        "depends_on",
        "schema",
        "sample",
        "sample_complete",
        "warnings",
        "recovery",
        "provider_retry_count",
    ):
        value = summary.get(key)
        if value not in (None, "", [], {}):
            projected[key] = value
        elif key in {"row_count", "sample_complete"}:
            projected[key] = value
    if compact_evidence:
        projected["step_evidence"] = compact_evidence
    return projected


def _backtest_presentation(comparison: Mapping[str, Any]) -> Dict[str, Any]:
    raw_series = [
        item
        for item in comparison.get("series") or []
        if isinstance(item, Mapping)
        and isinstance(item.get("normalized_curve"), list)
        and item.get("normalized_curve")
    ]
    date_sets = [
        {
            _trim(point.get("date"))
            for point in item.get("normalized_curve") or []
            if isinstance(point, Mapping) and _trim(point.get("date"))
        }
        for item in raw_series
    ]
    common_dates = sorted(set.intersection(*date_sets)) if date_sets else []
    if len(common_dates) > 500:
        step = max(1, len(common_dates) // 499)
        sampled_dates = common_dates[::step]
        if sampled_dates[-1] != common_dates[-1]:
            sampled_dates.append(common_dates[-1])
        common_dates = sampled_dates[:500]
    chart_series = []
    for item in raw_series:
        values = {
            _trim(point.get("date")): point.get("value")
            for point in item.get("normalized_curve") or []
            if isinstance(point, Mapping)
        }
        chart_series.append(
            {
                "name": _trim(item.get("name")) or _trim(item.get("series_id")),
                "data": [values[date] for date in common_dates],
            }
        )
    summary = (
        comparison.get("summary")
        if isinstance(comparison.get("summary"), Mapping)
        else {}
    )
    primary = (
        comparison.get("primary_benchmark")
        if isinstance(comparison.get("primary_benchmark"), Mapping)
        else {}
    )

    def percent_item(key: str, label: str) -> Dict[str, Any] | None:
        value = summary.get(key)
        if not isinstance(value, (int, float)):
            return None
        return {"label": label, "value": round(float(value) * 100, 2), "unit": "%"}

    metric_items = [
        percent_item("portfolio_total_return", "组合收益"),
        percent_item(
            "benchmark_total_return",
            f"{_trim(primary.get('name')) or '基准'}收益",
        ),
        percent_item("excess_return", "超额收益"),
        percent_item("portfolio_max_drawdown", "组合最大回撤"),
        percent_item("benchmark_max_drawdown", "基准最大回撤"),
    ]
    information_ratio = summary.get("information_ratio")
    if isinstance(information_ratio, (int, float)):
        metric_items.append(
            {
                "label": "信息比率",
                "value": round(float(information_ratio), 2),
            }
        )
    return {
        "metrics": [item for item in metric_items if item is not None],
        "chart": {
            "x_axis": common_dates,
            "series": chart_series,
        },
    }


def _result_handle(payload: Mapping[str, Any]) -> Optional[ResultHandle]:
    result = payload.get("result") if isinstance(payload.get("result"), Mapping) else {}
    name = _trim(result.get("name"))
    api = _trim(result.get("api"))
    if not name or not api:
        return None
    return ResultHandle(
        name=name,
        api=api,
        columns=[_trim(item) for item in result.get("columns") or [] if _trim(item)],
        data=result.get("data"),
        step_id=_trim(result.get("step_id")),
        task=_trim(result.get("task")),
    )


def _is_successful_query_payload(payload: Mapping[str, Any]) -> bool:
    if payload.get("ok") is False:
        return False
    execution = (
        payload.get("execution")
        if isinstance(payload.get("execution"), Mapping)
        else {}
    )
    if execution and not bool(execution.get("ok")):
        return False
    result = (
        payload.get("result")
        if isinstance(payload.get("result"), Mapping)
        else {}
    )
    data = result.get("data") if isinstance(result.get("data"), Mapping) else {}
    status = _trim(data.get("status")).lower()
    return not status or status == "ok"


class FinanceDataQueryToolRuntime:
    """Conversation-owned query handles and observable turn evidence."""

    def __init__(self, *, result_store: SessionVariableStoreService) -> None:
        self.result_store = result_store
        self.owner_ids: List[str] = []
        self.tool_context: Dict[str, Any] = {}
        self.event_sink: Optional[Callable[[Dict[str, Any]], None]] = None
        self.result_handles: Dict[str, ResultHandle] = {}
        self.result_metadata: Dict[str, Dict[str, Any]] = {}
        self.result_registry = FinanceResultRegistry()
        self._restored_scope = ""
        self.finance_catalog_revision = ""
        self.tracker: Dict[str, Any] = {}
        self.begin_turn(owner_ids=[], tool_context={})

    def begin_turn(
        self,
        *,
        owner_ids: List[str],
        tool_context: Mapping[str, Any],
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        self.owner_ids = list(owner_ids)
        self.tool_context = dict(tool_context)
        self.event_sink = event_sink
        self._restore_results()
        self.tracker = {
            "finance_catalog_revision": self.finance_catalog_revision,
            "calls": [],
            "catalog_reads": [],
            "result_refs": [],
            "active_skill_ids": [],
            "skill_entries": [],
            "skill_results": [],
            "restored_result_names": sorted(self.result_handles),
            "interaction_requests": [],
            "artifact_updates": [],
            "asset_reads": [],
            "dynamic_runs": [],
            "implementation_runs": [],
        }
        snapshot = self.tool_context.get("_finance_skill_snapshot")
        if isinstance(snapshot, Mapping):
            methods = snapshot.get("skills") or {}
            for skill_id in self.tool_context.get("_finance_explicit_skill_ids") or []:
                if skill_id in methods:
                    self.record_method_load(skill_id, methods[skill_id], snapshot["revision"])
        return self.tracker

    def record_method_load(self, skill_id: str, method: Mapping[str, Any], revision: str) -> None:
        self.activate_skill(skill_id)
        if any(item.get("skill_id") == skill_id for item in self.tracker["skill_entries"]):
            return
        self.tracker["skill_entries"].append({
            "skill_id": skill_id,
            "revision": revision,
            "content_hash": _trim(method.get("content_hash")),
        })
        self.tracker["skill_results"].append(_trim(method.get("method"))[:5000])

    def activate_skill(self, skill_id: str) -> None:
        normalized = _trim(skill_id)
        active = self.tracker.get("active_skill_ids")
        if not normalized or not isinstance(active, list) or normalized in active:
            return
        active.append(normalized)

    def configured_tool_allowed(self, tool_name: str) -> bool:
        """Methods guide tool choice; only the caller's runtime grants authorize it."""

        return _trim(tool_name) in {
            _trim(item) for item in self.tool_context.get("allowed_agent_tools") or []
            if _trim(item)
        }

    @property
    def runtime_scope(self) -> str:
        return _trim(self.tool_context.get("_agent_runtime_scope"))

    @property
    def result_scope(self) -> str:
        return self.runtime_scope or (_trim(self.owner_ids[0]) if self.owner_ids else "financial_qa")

    def remember(
        self,
        payload: Mapping[str, Any],
        *,
        goal: str = "",
        variable: Optional[Mapping[str, Any]] = None,
    ) -> Optional[ResultHandle]:
        handle = _result_handle(payload)
        if handle is not None:
            if _trim(goal):
                handle.task = _trim(goal)
            self.result_handles[handle.name] = handle
            request = _trim(payload.get("request"))
            call = (
                payload.get("call")
                if isinstance(payload.get("call"), Mapping)
                else {}
            )
            call_args = (
                call.get("args")
                if isinstance(call.get("args"), Mapping)
                else {}
            )
            selection_applied = self.result_registry.selection_applied(call_args)
            if not selection_applied:
                selection_applied = self.result_registry.selection_from_request(request)
            self.result_metadata[handle.name] = {
                "goal": _trim(handle.task),
                "api": _trim(handle.api),
                "request": request,
                "depends_on": self.result_registry.dependencies(request),
                "selection_applied": selection_applied,
            }
            if isinstance(variable, Mapping):
                self.attach_variable(handle.name, variable)
        return handle

    def attach_variable(
        self,
        result_name: str,
        variable: Mapping[str, Any],
    ) -> None:
        metadata = self.result_metadata.setdefault(_trim(result_name), {})
        metadata.update(
            {
                "goal": _trim(variable.get("task") or metadata.get("goal")),
                "result_ref": _trim(variable.get("data_ref")),
                "schema": dict(variable.get("schema") or {}),
                "sample": dict(variable.get("sample") or {}),
                "row_count": variable.get("row_count"),
            }
        )
        runtime = (
            variable.get("runtime")
            if isinstance(variable.get("runtime"), Mapping)
            else {}
        )
        if _trim(runtime.get("api")):
            metadata["api"] = _trim(runtime.get("api"))
        if isinstance(runtime.get("depends_on"), list):
            metadata["depends_on"] = [
                _trim(item) for item in runtime.get("depends_on") or [] if _trim(item)
            ]

    @property
    def next_result_name(self) -> str:
        return self.result_registry.next_result_name(self.result_handles)

    def working_set(self) -> list[Dict[str, Any]]:
        return self.result_registry.entries(
            handles=self.result_handles,
            metadata_by_name=self.result_metadata,
        )

    def working_set_prompt(self) -> str:
        return self.result_registry.prompt_text(
            handles=self.result_handles,
            metadata_by_name=self.result_metadata,
        )

    def resolve_result_ref(self, value: Any) -> str:
        """Resolve either a durable ref or the current working-set alias."""

        candidate = _trim(value)
        if candidate.startswith(SessionVariableStoreService.REF_PREFIX):
            return candidate
        metadata = self.result_metadata.get(candidate)
        if isinstance(metadata, Mapping):
            resolved = _trim(metadata.get("result_ref"))
            if resolved:
                return resolved
        return candidate

    def current_context_prompt(self) -> str:
        if not self.result_handles:
            return ""
        return (
            "以下是系统持有的当前金融查询 working_set。它只包含可寻址索引、"
            "服务端已执行的选择条件和列覆盖，不包含隐藏的全量数据；"
            "优先复用已有 rN.column，不重新获取同一对象或事实。\n"
            + self.working_set_prompt()
        )

    def emit_progress(
        self,
        content: str,
        *,
        progress_id: str = "",
        title: str = "",
        status: str = "running",
    ) -> None:
        if self.event_sink is None or not _trim(content):
            return
        public_status = status if status in {"running", "completed", "error"} else "running"
        try:
            self.event_sink(
                {
                    "source": "claude",
                    "type": "reasoning_summary_delta",
                    "content": _trim(content),
                    "metadata": {
                        "stage": "runtime",
                        "progress_id": _trim(progress_id),
                        "title": _trim(title),
                        "status": public_status,
                    },
                }
            )
        except Exception:
            return

    def _restore_results(self) -> None:
        scope = self.result_scope
        if not scope or scope == self._restored_scope:
            return
        self.result_handles = {}
        self.result_metadata = {}
        for item in self.result_store.list_variables(session_id=scope):
            if (
                _trim(item.get("tool_name")) != "finance_data_query"
                or _trim(item.get("status")) != "ok"
            ):
                continue
            data_ref = _trim(item.get("data_ref"))
            if not data_ref:
                continue
            try:
                payload = self.result_store.load_registered_result(
                    session_id=scope,
                    data_ref=data_ref,
                )
            except Exception:
                continue
            if not _is_successful_query_payload(payload):
                continue
            self.remember(
                payload,
                goal=_trim(item.get("task")),
                variable=item,
            )
        self._restored_scope = scope


class FinanceDataQueryCcTools:
    """Read-only financial catalog, query, and result tools for Financial QA CC."""

    def __init__(
        self,
        *,
        finance_runtime: Optional[FinanceDataToolRuntimeService] = None,
        finance_catalog: Optional[FinanceDataToolCatalogService] = None,
        result_store: Optional[SessionVariableStoreService] = None,
        tool_adapter: Optional[ToolAdapter] = None,
        business_skill_catalog: Any = None,
        backtest_service: Optional[BacktestRunService] = None,
        input_resolver: Optional[InvocationInputResolverService] = None,
    ) -> None:
        if finance_catalog is not None and finance_runtime is None:
            supplied_path = Path(
                str(getattr(finance_catalog, "catalog_path", ""))
            ).resolve()
            canonical_path = Path(
                FinanceDataToolCatalogService.DEFAULT_CATALOG_PATH
            ).resolve()
            if supplied_path != canonical_path:
                raise ValueError(
                    "a custom finance catalog requires a matching finance runtime"
                )
        self.finance_runtime = finance_runtime or FinanceDataToolRuntimeService(
            trade_date_resolver=TradeDateResolver()
        )
        self.finance_catalog = finance_catalog or FinanceDataToolCatalogService()
        self.result_store = result_store or SessionVariableStoreService()
        self.tool_adapter = tool_adapter or ToolAdapter()
        self.business_skill_catalog = business_skill_catalog
        self.backtest_service = backtest_service or BacktestRunService()
        self.input_resolver = input_resolver or InvocationInputResolverService()

    def bind_business_skill_catalog(self, catalog: Any) -> None:
        """Bind the immutable business-Skill snapshot used by this CC."""

        self.business_skill_catalog = catalog

    def create_runtime(self) -> FinanceDataQueryToolRuntime:
        return FinanceDataQueryToolRuntime(result_store=self.result_store)

    def _public_source(
        self,
        *,
        api: str = "",
        subject: str = "",
        dataview: str = "",
    ) -> dict[str, str]:
        resolver = getattr(self.finance_catalog, "get_public_data_source", None)
        if callable(resolver):
            try:
                value = resolver(api=api, subject=subject, dataview=dataview)
                return dict(value) if isinstance(value, Mapping) else {}
            except (KeyError, ValueError):
                return {}
        if api and "." in api:
            subject, dataview = api.split(".", 2)[:2]
        try:
            subject_node = self.finance_catalog.get_subject(subject) if subject else {}
            dataview_node = (
                self.finance_catalog.get_dataview(subject, dataview)
                if subject and dataview
                else {}
            )
        except (KeyError, ValueError):
            return {}
        subject_label = _trim(
            subject_node.get("public_name") or subject_node.get("desc") or subject
        )
        dataview_label = _trim(
            dataview_node.get("public_name")
            or dataview_node.get("desc")
            or dataview
        )
        return {
            "label": " · ".join(
                item for item in (subject_label, dataview_label) if item
            )
        }

    def build_tools(
        self,
        *,
        owner_ids: List[str],
        tool_context: Mapping[str, Any],
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
        runtime: Optional[FinanceDataQueryToolRuntime] = None,
    ) -> tuple[list[Any], list[str], Dict[str, Any]]:
        from claude_agent_sdk import tool

        tool_runtime = runtime or self.create_runtime()
        tracker = tool_runtime.begin_turn(
            owner_ids=owner_ids,
            tool_context=tool_context,
            event_sink=event_sink,
        )
        catalog_revision_reader = getattr(
            self.finance_catalog,
            "catalog_revision",
            None,
        )
        routing_index = ""
        pinned_catalog_revision = ""
        for _ in range(3):
            before_revision = (
                _trim(catalog_revision_reader())
                if callable(catalog_revision_reader)
                else ""
            )
            routing_index = self._catalog_routing_index()
            after_revision = (
                _trim(catalog_revision_reader())
                if callable(catalog_revision_reader)
                else ""
            )
            if before_revision == after_revision:
                pinned_catalog_revision = after_revision
                break
        else:
            raise RuntimeError(
                "finance catalog changed repeatedly while building agent tools"
            )
        tool_runtime.finance_catalog_revision = pinned_catalog_revision
        tracker["finance_catalog_revision"] = pinned_catalog_revision

        def assert_catalog_revision() -> None:
            if not pinned_catalog_revision or not callable(catalog_revision_reader):
                return
            if _trim(catalog_revision_reader()) != pinned_catalog_revision:
                raise RuntimeError(
                    "finance catalog changed during the active agent turn"
                )

        @tool(
            "read_finance_catalog",
            (
                "读取金融数据调用目录。subject 定位对象，dataview 定位数据，operation 选择方法类别。"
                "operation 用于选择执行包；调用使用包内的 api_name、request_pattern、参数和字段。"
                "subject-only 或空参数可用于浏览上层目录。\n\n"
                f"数据范围索引：\n{routing_index}"
            ),
            {
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "maxLength": 100},
                    "dataview": {"type": "string", "maxLength": 100},
                    "operation": {
                        "type": "string",
                        "enum": list(OPERATION_DESCRIPTIONS),
                        "description": _OPERATION_SELECTION_DESCRIPTION,
                    },
                },
                "additionalProperties": False,
            },
        )
        async def read_finance_catalog(args: dict[str, Any]) -> dict[str, Any]:
            subject = _trim(args.get("subject"))
            dataview = _trim(args.get("dataview"))
            operation = _trim(args.get("operation"))
            tool_runtime.tracker["calls"].append(
                {
                    "tool": "read_finance_catalog",
                    "subject": subject,
                    "dataview": dataview,
                    "operation": operation,
                }
            )
            try:
                assert_catalog_revision()
                if dataview and not subject:
                    raise ValueError("subject is required when dataview is provided")
                if operation and not (subject and dataview):
                    raise ValueError(
                        "subject and dataview are required when operation is provided"
                    )
                if subject and dataview:
                    model_dataview = getattr(
                        self.finance_catalog,
                        "get_model_dataview",
                        None,
                    )
                    payload = {
                        "mode": "dataview",
                        "subject": subject,
                        "dataview": (
                            model_dataview(subject, dataview, operation)
                            if callable(model_dataview)
                            else self.finance_catalog.get_dataview(subject, dataview)
                        ),
                    }
                elif subject:
                    payload = {
                        "mode": "subject",
                        "subject": self._subject_summary(subject),
                    }
                else:
                    payload = {
                        "mode": "index",
                        "subjects": self._catalog_index(),
                    }
                assert_catalog_revision()
                tool_runtime.tracker["catalog_reads"].append(
                    {
                        "subject": subject,
                        "dataview": dataview,
                        "operation": operation,
                        "mode": payload["mode"],
                    }
                )
                catalog_index = len(tool_runtime.tracker["catalog_reads"])
                if payload["mode"] == "dataview":
                    public_source = self._public_source(
                        subject=subject,
                        dataview=dataview,
                    )
                    source_label = _trim(public_source.get("label"))
                    progress_text = (
                        f"已确认{source_label}的可用字段、时间范围和查询口径。"
                        if source_label
                        else "已确认本题所需数据的字段与时间口径。"
                    )
                    progress_title = (
                        f"确认{source_label}" if source_label else "确认数据范围"
                    )
                elif payload["mode"] == "subject":
                    progress_text = "已确认该金融主体可查询的数据范围。"
                    progress_title = "确认查询对象"
                else:
                    progress_text = "已读取金融数据目录，正在定位本题所需数据。"
                    progress_title = "确认数据范围"
                tool_runtime.emit_progress(
                    progress_text,
                    progress_id=f"finance_catalog_{catalog_index}",
                    title=progress_title,
                    status="completed",
                )
                return _tool_result(payload)
            except Exception as exc:
                tool_runtime.emit_progress(
                    "暂时无法确认本题所需的数据口径。",
                    progress_id=f"finance_catalog_{len(tool_runtime.tracker['catalog_reads']) + 1}",
                    title="确认数据范围",
                    status="error",
                )
                return _tool_result(
                    {
                        "subject": subject,
                        "dataview": dataview,
                        "operation": operation,
                        "error": str(exc),
                    }
                )

        @tool(
            "finance_query",
            (
                "执行金融数据查询流。围绕本轮数据目标，把已确定的查询及其依赖放入同一个 steps；"
                "需要观察返回值才能决定的后续查询留到下一次。\n"
                "每步包含业务目标 goal 和一条目录定义的只读 request：result = api_name(arguments) -> fields。"
                "筛选范围由用户条件、目录口径和上游对象集合确定。\n"
                "流内用 stepN.column 引用前一步的列集合，跨流用 rN.column；集合条件为 field in stepN.column，"
                "标量条件使用已读取的具体值。系统分配正式 rN 并保存取数范围、来源和列覆盖。\n"
                "响应提供本次新增结果与执行证据。复用成功步骤；失败按 recovery 处理，"
                "口径错误依据目录和执行事实修正。零行和空字段按返回状态作为数据缺口保留。"
            ),
            {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 8,
                        "items": {
                            "type": "object",
                            "properties": {
                                "goal": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 500,
                                    "description": (
                                        "One concise business data goal completed by this step."
                                    ),
                                },
                                "request": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 4_000,
                                    "description": (
                                        "Exactly one executable DSL request. Use `result = ...` on the left. "
                                        "A later step may filter by an earlier column set as `field in step1.column`."
                                    ),
                                },
                            },
                            "required": ["goal", "request"],
                            "additionalProperties": False,
                        },
                    },
                    "data_request_complete": {
                        "type": "boolean",
                        "description": (
                            "仅数据模式必填，也适用于带摘要的查询。"
                            "本 flow 覆盖全部取数目标（含有效空结果）时为 true；"
                            "尚有依赖返回值的后续取数目标时为 false。原始文本也是可交付的数据。"
                        ),
                    },
                },
                "required": ["steps"],
                "additionalProperties": False,
            },
        )
        async def finance_query(args: dict[str, Any]) -> dict[str, Any]:
            if tool_runtime.tool_context.get("_finance_data_only"):
                # Completion is a successful flow outcome, not merely the
                # presence of earlier rows or the caller's proposed boolean.
                tool_runtime.tracker["data_only_complete"] = False
            try:
                assert_catalog_revision()
            except Exception as exc:
                return _tool_result({"error": str(exc)})
            raw_steps = (
                args.get("steps")
                if isinstance(args.get("steps"), list)
                else []
            )
            # Preserve direct Python callers during the contract transition; the
            # model-facing schema exposes only the ordered flow.
            if not raw_steps and (_trim(args.get("goal")) or _trim(args.get("request"))):
                raw_steps = [
                    {
                        "goal": _trim(args.get("goal")),
                        "request": _trim(args.get("request")),
                    }
                ]
            steps = [item for item in raw_steps if isinstance(item, Mapping)]
            if not steps:
                return _tool_result(
                    {
                        "error": "steps must contain at least one financial data step",
                        "next_result_name": tool_runtime.next_result_name,
                    }
                )
            completed_steps: Dict[int, str] = {}
            step_summaries: list[Dict[str, Any]] = []
            for step_number, step in enumerate(steps, start=1):
                try:
                    assert_catalog_revision()
                except Exception as exc:
                    return _tool_result(
                        {
                            "error": str(exc),
                            "failed_step": step_number,
                            "completed_steps": step_summaries,
                            "next_result_name": tool_runtime.next_result_name,
                        }
                    )
                goal = _trim(step.get("goal"))
                submitted_request = _trim(step.get("request"))
                expected_result_name = tool_runtime.next_result_name
                call_record: Dict[str, Any] = {
                    "tool": "finance_query",
                    "flow_step": step_number,
                    "flow_size": len(steps),
                    "goal": goal[:500],
                    "submitted_request": submitted_request[:500],
                    "expected_result_name": expected_result_name,
                }
                if bool(tool_runtime.tool_context.get("_finance_data_only")):
                    call_record["data_request_complete"] = bool(
                        args.get("data_request_complete")
                    )
                tool_runtime.tracker["calls"].append(call_record)
                if not goal or not submitted_request:
                    call_record["error"] = "goal and request are required"
                    return _tool_result(
                        {
                            "error": "each step requires goal and request",
                            "failed_step": step_number,
                            "completed_steps": step_summaries,
                            "next_result_name": expected_result_name,
                        }
                    )
                progress_title = _query_progress_title("", step_number, len(steps))
                try:
                    flow_request = tool_runtime.result_registry.resolve_flow_refs(
                        submitted_request,
                        completed_steps=completed_steps,
                    )
                    request, submitted_result_name = (
                        tool_runtime.result_registry.assign_result_name(
                            flow_request,
                            expected_result_name,
                        )
                    )
                    call_record["request"] = request[:500]
                    if submitted_result_name != expected_result_name:
                        call_record["assigned_result_name"] = expected_result_name
                    public_source = self._public_source(
                        api=_request_api_name(request)
                    )
                    source_label = _trim(public_source.get("label"))
                    progress_title = _query_progress_title(
                        source_label,
                        step_number,
                        len(steps),
                    )
                    tool_runtime.emit_progress(
                        f"正在查询：{goal}",
                        progress_id=f"finance_query_step_{step_number}",
                        title=progress_title,
                    )
                    provider_retries_used = 0
                    allow_provider_retry = (
                        tool_runtime.tool_context.get("_finance_execution_mode") != "fast"
                    )
                    async def execute_attempt():
                        attempt = {"attempt": len(call_record.setdefault("attempts", [])) + 1}
                        call_record["attempts"].append(attempt)
                        started = time.monotonic()
                        try:
                            value = await asyncio.to_thread(
                                self.finance_runtime.execute_request,
                                request=request,
                                previous_results=dict(tool_runtime.result_handles),
                            )
                            timing = value.get("timings") if isinstance(value, Mapping) else None
                            if isinstance(timing, Mapping):
                                attempt.update({k: timing[k] for k in ("static_validation_ms", "api_execution_ms") if k in timing})
                            return value
                        except Exception:
                            attempt["raised_exception"] = True
                            raise
                        finally:
                            attempt["duration_ms"] = round((time.monotonic() - started) * 1000, 3)
                    try:
                        result = await execute_attempt()
                    except Exception:
                        if not allow_provider_retry:
                            raise
                        provider_retries_used = 1
                        call_record["provider_retry_count"] = provider_retries_used
                        tool_runtime.emit_progress(
                            "数据源暂未完成本步查询，正在使用原请求自动重试一次。",
                            progress_id=f"finance_query_step_{step_number}",
                            title=progress_title,
                        )
                        result = await execute_attempt()
                    while allow_provider_retry and provider_retry_allowed(
                        result.get("execution")
                        if isinstance(result, Mapping)
                        else {},
                        retries_used=provider_retries_used,
                    ):
                        provider_retries_used += 1
                        call_record["provider_retry_count"] = provider_retries_used
                        tool_runtime.emit_progress(
                            "数据源暂未完成本步查询，正在使用原请求自动重试一次。",
                            progress_id=f"finance_query_step_{step_number}",
                            title=progress_title,
                        )
                        result = await execute_attempt()
                    timings = (
                        result.get("timings")
                        if isinstance(result, Mapping)
                        and isinstance(result.get("timings"), Mapping)
                        else {}
                    )
                    call_record["static_validation_ms"] = float(
                        timings.get("static_validation_ms") or 0
                    )
                    call_record["api_execution_ms"] = float(
                        timings.get("api_execution_ms") or 0
                    )
                    assert_catalog_revision()
                except Exception as exc:
                    call_record["error"] = str(exc)[:1_000]
                    tool_runtime.emit_progress(
                        "当前查询未完成，已保留此前取得的有效结果。",
                        progress_id=f"finance_query_step_{step_number}",
                        title=progress_title,
                        status="error",
                    )
                    return _tool_result(
                        {
                            "error": str(exc),
                            "failed_step": step_number,
                            "completed_steps": step_summaries,
                            "next_result_name": expected_result_name,
                        }
                    )

                validation = (
                    result.get("validation")
                    if isinstance(result.get("validation"), Mapping)
                    else {}
                )
                if not bool(validation.get("ok")):
                    call_record["validation_errors"] = [
                        _trim(item)
                        for item in validation.get("errors") or []
                        if _trim(item)
                    ]
                    tool_runtime.emit_progress(
                        f"第 {step_number} 步的数据请求与目录口径不一致，正在修正。",
                        progress_id=f"finance_query_step_{step_number}",
                        title=progress_title,
                        status="error",
                    )
                    return _tool_result(
                        {
                            "failed_step": step_number,
                            "goal": goal,
                            "request": request,
                            "validation": dict(validation),
                            "recovery": query_recovery(validation=validation),
                            "completed_steps": step_summaries,
                            "next_result_name": expected_result_name,
                        }
                    )

                execution = (
                    result.get("execution")
                    if isinstance(result.get("execution"), Mapping)
                    else {}
                )
                date_warnings = [
                    _trim(item)
                    for item in execution.get("warnings") or []
                    if _trim(item)
                ]
                if date_warnings:
                    for warning in date_warnings:
                        tool_runtime.emit_progress(
                            warning,
                            progress_id=f"finance_query_date_{step_number}",
                            title="日期口径",
                            status="completed",
                        )
                if execution and not bool(execution.get("ok")):
                    status = _trim(execution.get("status")) or "provider_error"
                    reason = _trim(execution.get("reason"))
                    message = (
                        f"provider execution failed with status={status}"
                        + (f": {reason}" if reason else "")
                    )
                    call_record["execution_error"] = message[:1_000]
                    tool_runtime.emit_progress(
                        f"第 {step_number} 步的数据源查询未完成。",
                        progress_id=f"finance_query_step_{step_number}",
                        title=progress_title,
                        status="error",
                    )
                    return _tool_result(
                        {
                            "failed_step": step_number,
                            "goal": goal,
                            "request": request,
                            "execution": dict(execution),
                            "recovery": query_recovery(
                                execution=execution,
                                provider_retries_used=provider_retries_used,
                            ),
                            "completed_steps": step_summaries,
                            "next_result_name": expected_result_name,
                        }
                    )

                result_payload = (
                    result.get("result")
                    if isinstance(result.get("result"), dict)
                    else {}
                )
                result_payload["task"] = goal
                result_payload["step_id"] = expected_result_name
                handle = _result_handle(result)
                if handle is not None:
                    handle.task = goal
                if handle is None or handle.name != expected_result_name:
                    message = (
                        "query runtime returned an invalid result handle"
                        if handle is None
                        else (
                            "query runtime returned an unexpected result handle: "
                            f"expected {expected_result_name}, got {handle.name}"
                        )
                    )
                    call_record["error"] = message
                    tool_runtime.emit_progress(
                        "当前查询返回了不可用的结果引用。",
                        progress_id=f"finance_query_step_{step_number}",
                        title=progress_title,
                        status="error",
                    )
                    return _tool_result(
                        {
                            "error": message,
                            "failed_step": step_number,
                            "completed_steps": step_summaries,
                            "next_result_name": expected_result_name,
                        }
                    )
                dependencies = tool_runtime.result_registry.dependencies(request)
                try:
                    assert_catalog_revision()
                except Exception as exc:
                    call_record["error"] = str(exc)[:1_000]
                    return _tool_result(
                        {
                            "error": str(exc),
                            "failed_step": step_number,
                            "completed_steps": step_summaries,
                            "next_result_name": expected_result_name,
                        }
                    )
                variable = self.result_store.register_tool_result(
                    session_id=tool_runtime.result_scope,
                    tool_name="finance_data_query",
                    result=result,
                    task=goal,
                    runtime_ctx={
                        "conversation_id": tool_runtime.runtime_scope,
                        "goal": goal,
                        "api": handle.api,
                        "depends_on": dependencies,
                    },
                    local_alias=handle.name,
                )
                if not variable:
                    return _tool_result(
                        {
                            "error": "query result could not be registered",
                            "failed_step": step_number,
                            "completed_steps": step_summaries,
                            "next_result_name": tool_runtime.next_result_name,
                        }
                    )
                remembered = tool_runtime.remember(
                    result,
                    goal=goal,
                    variable=variable,
                )
                if remembered is None:
                    call_record["error"] = "registered query result could not be restored"
                    return _tool_result(
                        {
                            "error": "registered query result could not be restored",
                            "failed_step": step_number,
                            "completed_steps": step_summaries,
                            "next_result_name": expected_result_name,
                        }
                    )
                working_set = tool_runtime.working_set()
                current_entry = next(
                    (
                        item
                        for item in working_set
                        if _trim(item.get("result_name")) == handle.name
                    ),
                    {},
                )
                model_sample = (
                    dict(variable.get("sample"))
                    if isinstance(variable.get("sample"), Mapping)
                    else {}
                )
                try:
                    model_sample_rows = max(
                        0,
                        min(
                            50,
                            int(
                                tool_runtime.tool_context.get(
                                    "_finance_model_sample_rows"
                                )
                                or 0
                            ),
                        ),
                    )
                except (TypeError, ValueError):
                    model_sample_rows = 0
                try:
                    model_sample_max_chars = max(
                        0,
                        int(tool_runtime.tool_context.get("_finance_model_sample_max_chars") or 0),
                    )
                except (TypeError, ValueError):
                    model_sample_max_chars = 0
                result_rows = _result_rows(handle)
                row_count = int(variable.get("row_count") or len(result_rows))
                if (
                    model_sample_rows
                    and row_count <= model_sample_rows
                    and len(result_rows) >= row_count
                    and (
                        not model_sample_max_chars
                        or len(json.dumps(
                            result_rows, ensure_ascii=False, separators=(",", ":"), default=str,
                        ))
                        <= model_sample_max_chars
                    )
                ):
                    model_sample = {"rows": result_rows[:model_sample_rows]}
                model_sample_rows_value = (
                    model_sample.get("rows")
                    if isinstance(model_sample.get("rows"), list)
                    else []
                )
                model_sample_complete = row_count <= len(model_sample_rows_value)
                call = (
                    result.get("call")
                    if isinstance(result.get("call"), Mapping)
                    else {}
                )
                call_args = (
                    call.get("args")
                    if isinstance(call.get("args"), Mapping)
                    else {}
                )
                if not call_args:
                    call_args = tool_runtime.result_registry.selection_from_request(
                        request
                    )
                evidence_entry = dict(current_entry)
                evidence_entry["sample_complete"] = model_sample_complete
                step_evidence = tool_runtime.result_registry.step_evidence(
                    evidence_entry,
                    call_args=call_args,
                )
                summary = {
                    "flow_step": step_number,
                    "goal": goal,
                    "request": request,
                    "api": handle.api,
                    "finance_catalog_revision": pinned_catalog_revision,
                    "result_name": variable.get("local_alias"),
                    "result_ref": variable.get("data_ref"),
                    "data_type": variable.get("data_type"),
                    "row_count": variable.get("row_count"),
                    "depends_on": dependencies,
                    "step_evidence": step_evidence,
                    "schema": variable.get("schema"),
                    "sample": model_sample,
                    "sample_complete": model_sample_complete,
                    "warnings": [
                        *[
                            _trim(item)
                            for item in validation.get("warnings") or []
                            if _trim(item)
                        ],
                        *date_warnings,
                    ],
                }
                recovery = query_recovery(
                    validation=validation,
                    execution=execution,
                    result_data=(
                        result_payload.get("data")
                        if isinstance(result_payload.get("data"), Mapping)
                        else {}
                    ),
                    provider_retries_used=provider_retries_used,
                )
                if recovery is not None:
                    summary["recovery"] = recovery
                if provider_retries_used:
                    summary["provider_retry_count"] = provider_retries_used
                step_summaries.append(_model_step_summary(summary))
                tool_runtime.tracker["result_refs"].append(dict(summary))
                call_record["result_name"] = variable.get("local_alias")
                call_record["row_count"] = variable.get("row_count")
                completed_steps[step_number] = handle.name
                row_count = int(variable.get("row_count") or 0)
                result_summary = (
                    f"已完成：{goal}，取得 {row_count} 条记录。"
                    if row_count
                    else f"已完成：{goal}，当前条件下没有匹配记录。"
                )
                tool_runtime.emit_progress(
                    result_summary,
                    progress_id=f"finance_query_step_{step_number}",
                    title=progress_title,
                    status="completed",
                )

            response: Dict[str, Any] = {
                "ok": True,
                "next_result_name": tool_runtime.next_result_name,
            }
            if isinstance(args.get("data_request_complete"), bool):
                response["data_request_complete"] = args["data_request_complete"]
            if bool(tool_runtime.tool_context.get("_finance_data_only")):
                # The explicit completion handshake prevents an optimized
                # harness from stopping after an intermediate adaptive query.
                # Full rows remain server-owned and are projected only after
                # the agent turn.
                response["data_only_mode"] = True
                response["data_only_complete"] = bool(
                    args.get("data_request_complete")
                )
                tool_runtime.tracker["data_only_complete"] = response["data_only_complete"]
            if len(step_summaries) == 1:
                response.update(step_summaries[0])
            else:
                response["steps"] = step_summaries
            return _tool_result(response)

        @tool(
            "load_finance_result",
            (
                "按 result_ref 读取本会话已保存结果的一页数据，选择本次分析或后续查询所需的列。"
                "sample_complete=true 表示摘要样例已包含全部记录，可直接使用；其余结果按需分页读取。"
            ),
            {
                "type": "object",
                "properties": {
                    "result_ref": {"type": "string", "minLength": 1, "maxLength": 200},
                    "offset": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    "columns": {
                        "type": "array",
                        "maxItems": 20,
                        "items": {"type": "string", "minLength": 1, "maxLength": 100},
                    },
                },
                "required": ["result_ref"],
                "additionalProperties": False,
            },
        )
        async def load_finance_result(args: dict[str, Any]) -> dict[str, Any]:
            requested_ref = _trim(args.get("result_ref"))
            result_ref = tool_runtime.resolve_result_ref(requested_ref)
            tool_runtime.tracker["calls"].append(
                {
                    "tool": "load_finance_result",
                    "result_ref": result_ref,
                    **(
                        {"result_name": requested_ref}
                        if requested_ref and requested_ref != result_ref
                        else {}
                    ),
                }
            )
            try:
                try:
                    default_limit = max(
                        1,
                        min(
                            50,
                            int(
                                tool_runtime.tool_context.get(
                                    "_finance_detail_default_limit"
                                )
                                or 10
                            ),
                        ),
                    )
                except (TypeError, ValueError):
                    default_limit = 10
                payload = self.result_store.load_data_ref(
                    session_id=tool_runtime.result_scope,
                    data_ref=result_ref,
                    offset=int(args.get("offset") or 0),
                    limit=int(args.get("limit") or default_limit),
                )
                raw_columns = (
                    args.get("columns")
                    if isinstance(args.get("columns"), list)
                    else []
                )
                columns = [
                    _trim(item)
                    for item in raw_columns
                    if _trim(item)
                ]
                result_payload: Dict[str, Any] = {"result_ref": result_ref}
                page = payload.get("page")
                if isinstance(page, Mapping):
                    result_payload["page"] = dict(page)
                rows = payload.get("rows")
                if isinstance(rows, list):
                    result_payload["rows"] = [
                        {
                            key: row.get(key)
                            for key in columns
                            if key in row
                        }
                        if columns and isinstance(row, Mapping)
                        else dict(row)
                        for row in rows
                        if isinstance(row, Mapping)
                    ]
                    if columns:
                        result_payload["columns"] = columns
                elif "text" in payload:
                    result_payload["text"] = payload.get("text")
                elif payload.get("data") is not None:
                    result_payload["data"] = payload.get("data")
                return _tool_result(result_payload)
            except Exception as exc:
                return _tool_result({"result_ref": result_ref, "error": str(exc)})

        @tool(
            "run_backtest",
            (
                "Run the simple historical backtest for a fixed basket of A-share stocks. Use it when the user "
                "asks to observe how a supplied stock list performed over a date range. The default is equal-weight "
                "buy once and hold; if the user supplied weights, pass every stock's weight. Stocks may come either "
                "from direct holdings or from one authenticated table attachment already shown in the conversation. "
                "The server automatically compares against CSI 300 when benchmark is omitted. Set benchmark only "
                "when the user explicitly names a primary benchmark. Before seeing performance, you may add at most "
                "two context_benchmarks when the holdings have a clear board, size, or industry concentration and a "
                "real matching index is known; include a short holding-based reason and omit them when uncertain. "
                "Do not invent a strategy, rebalance frequency, index, attachment id, file path, or missing weight."
            ),
            {
                "type": "object",
                "properties": {
                    "holdings": {
                        "type": "array",
                        "maxItems": 10,
                        "items": {
                            "type": "object",
                            "properties": {
                                "stock": {"type": "string", "minLength": 1, "maxLength": 100},
                                "weight": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
                            },
                            "required": ["stock"],
                            "additionalProperties": False,
                        },
                    },
                    "attachment_source": {
                        "type": "object",
                        "properties": {
                            "attachment_id": {"type": "string", "minLength": 1, "maxLength": 200},
                            "table_index": {"type": "integer", "minimum": 0},
                            "stock_column": {"type": ["string", "integer"]},
                            "weight_column": {"type": ["string", "integer"]},
                        },
                        "required": ["attachment_id", "stock_column"],
                        "additionalProperties": False,
                    },
                    "start_date": {"type": "string", "format": "date"},
                    "end_date": {"type": "string", "format": "date"},
                    "initial_cash": {"type": "number", "exclusiveMinimum": 0},
                    "benchmark": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 100,
                        "description": (
                            "Optional user-specified primary index name or code. Omit to use 沪深300."
                        ),
                    },
                    "context_benchmarks": {
                        "type": "array",
                        "maxItems": 2,
                        "description": (
                            "Optional additional indices selected from clear holding characteristics before execution."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "subject": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 100,
                                },
                                "reason": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 300,
                                },
                            },
                            "required": ["subject", "reason"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["start_date", "end_date"],
                "additionalProperties": False,
            },
        )
        async def run_backtest(args: dict[str, Any]) -> dict[str, Any]:
            call_record: Dict[str, Any] = {
                "tool": "run_backtest",
                "start_date": _trim(args.get("start_date")),
                "end_date": _trim(args.get("end_date")),
                "benchmark": _trim(args.get("benchmark")) or "沪深300",
            }
            tool_runtime.tracker["calls"].append(call_record)
            direct_holdings = args.get("holdings") if isinstance(args.get("holdings"), list) else []
            attachment_source = (
                args.get("attachment_source")
                if isinstance(args.get("attachment_source"), Mapping)
                else {}
            )
            if bool(direct_holdings) == bool(attachment_source):
                return _tool_result(
                    {"ok": False, "error": "请在直接股票列表和附件来源中选择一种输入方式。"}
                )
            try:
                holdings: list[dict[str, Any]]
                if attachment_source:
                    columns: Dict[str, Any] = {
                        "stock": attachment_source.get("stock_column")
                    }
                    if attachment_source.get("weight_column") not in (None, ""):
                        columns["weight"] = attachment_source.get("weight_column")
                    records = self.input_resolver.materialize_records(
                        attachment_source,
                        [
                            dict(item)
                            for item in tool_runtime.tool_context.get("_backtest_attachments") or []
                            if isinstance(item, Mapping)
                        ],
                        columns,
                    )
                    holdings = [
                        {
                            "stock": row.get("stock"),
                            **({"weight": row.get("weight")} if "weight" in row else {}),
                        }
                        for row in records
                        if _trim(row.get("stock"))
                    ]
                    call_record["attachment_id"] = _trim(
                        attachment_source.get("attachment_id")
                    )
                else:
                    holdings = [dict(item) for item in direct_holdings if isinstance(item, Mapping)]
                call_record["stock_count"] = len(holdings)
                tool_runtime.emit_progress(
                    f"正在回测 {len(holdings)} 只股票的买入并持有组合。",
                    progress_id="run_backtest",
                    title="组合回测",
                )
                result = await asyncio.to_thread(
                    self.backtest_service.run,
                    {
                        "holdings": holdings,
                        "start_date": args.get("start_date"),
                        "end_date": args.get("end_date"),
                        "initial_cash": args.get("initial_cash"),
                        "benchmark": args.get("benchmark"),
                        "context_benchmarks": (
                            [
                                dict(item)
                                for item in args.get("context_benchmarks") or []
                                if isinstance(item, Mapping)
                            ]
                            if isinstance(args.get("context_benchmarks"), list)
                            else []
                        ),
                    },
                )
                variable = self.result_store.register_tool_result(
                    session_id=tool_runtime.result_scope,
                    tool_name="backtest_run",
                    result={"ok": True, "data": result},
                    task=(
                        f"回测 {len(holdings)} 只股票，"
                        f"{_trim(args.get('start_date'))} 至 {_trim(args.get('end_date'))}"
                    ),
                    runtime_ctx={"conversation_id": tool_runtime.runtime_scope},
                )
                summary = result.get("summary") if isinstance(result.get("summary"), Mapping) else {}
                period = result.get("period") if isinstance(result.get("period"), Mapping) else {}
                comparison = (
                    result.get("comparison")
                    if isinstance(result.get("comparison"), Mapping)
                    else {}
                )
                primary_benchmark = (
                    comparison.get("primary_benchmark")
                    if isinstance(comparison.get("primary_benchmark"), Mapping)
                    else {}
                )
                compact = {
                    "ok": True,
                    "backtest_type": result.get("backtest_type"),
                    "strategy": result.get("strategy"),
                    "period": period,
                    "stock_count": len(result.get("stocks") or []),
                    "summary": summary,
                    "benchmark_comparison": {
                        "primary_benchmark": {
                            key: primary_benchmark.get(key)
                            for key in (
                                "subject",
                                "code",
                                "name",
                                "source",
                                "reason",
                                "period",
                            )
                            if primary_benchmark.get(key) not in (None, "")
                        },
                        "summary": dict(comparison.get("summary") or {}),
                        "contextual_benchmarks": [
                            {
                                key: item.get(key)
                                for key in ("subject", "code", "name", "reason", "period")
                                if item.get(key) not in (None, "")
                            }
                            for item in comparison.get("contextual_benchmarks") or []
                            if isinstance(item, Mapping)
                        ],
                        "warnings": list(comparison.get("warnings") or [])[:5],
                    },
                    "warnings": list(result.get("warnings") or [])[:5],
                    "result_ref": variable.get("data_ref") if variable else "",
                }
                if variable:
                    result_ref = {
                        "tool": "backtest_run",
                        "result_ref": variable.get("data_ref"),
                        "data_type": variable.get("data_type"),
                        "row_count": variable.get("row_count"),
                        "schema": variable.get("schema"),
                        "sample": variable.get("sample"),
                        "semantic": "finance.backtest",
                        "backtest_presentation": _backtest_presentation(comparison),
                    }
                    tool_runtime.tracker["result_refs"].append(result_ref)
                    call_record["result_ref"] = variable.get("data_ref")
                tool_runtime.emit_progress(
                    "组合回测已完成。",
                    progress_id="run_backtest",
                    title="组合回测",
                    status="completed",
                )
                return _tool_result(compact)
            except BacktestError as exc:
                call_record["error"] = exc.code
                tool_runtime.emit_progress(
                    "组合回测未完成，请检查股票、权重和日期。",
                    progress_id="run_backtest",
                    title="组合回测",
                    status="error",
                )
                return _tool_result(
                    {"ok": False, "error_code": exc.code, "error": exc.message, "details": exc.details}
                )
            except Exception as exc:
                call_record["error"] = str(exc)[:1000]
                return _tool_result({"ok": False, "error": str(exc)})

        tools = [read_finance_catalog, finance_query, load_finance_result, run_backtest]
        fallback_snapshot = (
            self.business_skill_catalog.method_snapshot()
            if self.business_skill_catalog is not None else {"revision": "", "skills": {}}
        )

        def available_method(skill_id: str) -> tuple[Mapping[str, Any], str]:
            snapshot = tool_runtime.tool_context.get("_finance_skill_snapshot", fallback_snapshot)
            if not isinstance(snapshot, Mapping):
                raise ValueError("本轮业务 Skill 快照不可用。")
            revision = _trim(snapshot.get("revision"))
            expected = _trim(tool_runtime.tool_context.get("_finance_skill_catalog_revision"))
            if expected and expected != revision:
                raise ValueError("本轮业务 Skill 快照不一致，请使用同一修订。")
            allowed = tool_runtime.tool_context.get("allowed_finance_skills")
            methods = snapshot.get("skills") or {}
            if (isinstance(allowed, list) and skill_id not in allowed) or skill_id not in methods:
                raise ValueError("该业务 Skill 未注册、未授权或当前不可用。")
            return methods[skill_id], revision

        @tool(
            "read_finance_skill",
            "按当前授权目录中的 skill_id 加载业务方法。匹配问题时优先读取方法，再据其指导取数和分析；"
            "已加载方法可直接复用，没有匹配或覆盖不全时可使用数据工具与通用能力继续处理。",
            {
                "type": "object",
                "properties": {"skill_id": {"type": "string", "minLength": 1, "maxLength": 100}},
                "required": ["skill_id"],
                "additionalProperties": False,
            },
        )
        async def read_finance_skill(args: dict[str, Any]) -> dict[str, Any]:
            skill_id = _canonical_skill_id(args.get("skill_id"))
            call_record = {"tool": "read_finance_skill", "skill_id": skill_id}
            tool_runtime.tracker["calls"].append(call_record)
            try:
                method, revision = available_method(skill_id)
                tool_runtime.record_method_load(skill_id, method, revision)
                call_record.update({"status": "completed", "catalog_revision": revision,
                                    "content_hash": method.get("content_hash", "")})
                return _tool_result({
                    "skill_id": skill_id, "revision": revision,
                    "description": method.get("description", ""),
                    "method": method.get("method", ""),
                    "content_hash": method.get("content_hash", ""),
                })
            except ValueError as exc:
                call_record.update({"status": "error", "error": str(exc)})
                return _tool_result({"skill_id": skill_id, "error": str(exc)})

        tools.append(read_finance_skill)
        @tool(
            "read_finance_skill_reference",
            (
                "Load one progressive reference explicitly linked by an already loaded Finance business Skill. "
                "Use the exact skill_id and references/... path from that Skill. This reads only the immutable "
                "in-memory Skill snapshot; it is not a general filesystem search tool. Load the parent Skill "
                "first, and read only references that materially change the current analysis."
            ),
            {
                "type": "object",
                "properties": {
                    "skill_id": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 100,
                    },
                    "reference": {
                        "type": "string",
                        "pattern": "^references/[A-Za-z0-9_.\\-/]+$",
                        "maxLength": 240,
                    },
                },
                "required": ["skill_id", "reference"],
                "additionalProperties": False,
            },
        )
        async def read_finance_skill_reference(
            args: dict[str, Any],
        ) -> dict[str, Any]:
            requested_skill_id = _trim(args.get("skill_id"))
            skill_id = _canonical_skill_id(requested_skill_id)
            reference = _trim(args.get("reference"))
            call_record = {
                "tool": "read_finance_skill_reference",
                "skill_id": skill_id,
                "requested_skill_id": requested_skill_id,
                "reference": reference,
            }
            tool_runtime.tracker["calls"].append(call_record)
            active_skill_ids = {
                _trim(item)
                for item in tool_runtime.tracker.get("active_skill_ids") or []
                if _trim(item)
            }
            if skill_id not in active_skill_ids:
                call_record["error"] = "parent_skill_not_loaded"
                return _tool_result(
                    {
                        "skill_id": skill_id,
                        "reference": reference,
                        "error": "请先加载对应的业务 Skill，再读取其参考。",
                    }
                )
            try:
                method, revision = available_method(skill_id)
                path = PurePosixPath(reference)
                if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "references":
                    raise ValueError("业务 Skill 参考路径无效。")
                resource = (method.get("references") or {}).get(path.as_posix())
                if not isinstance(resource, Mapping):
                    raise ValueError("该参考未包含在本轮业务 Skill 快照中。")
                payload = {"skill_id": skill_id, "reference": path.as_posix(),
                           "revision": revision, "content": resource.get("content", ""),
                           "content_hash": resource.get("content_hash", "")}
            except ValueError as exc:
                payload = {"skill_id": skill_id, "reference": reference, "error": str(exc)}
            if _trim(payload.get("error")):
                call_record["error"] = _trim(payload.get("error"))[:500]
                call_record["status"] = "error"
            else:
                call_record.update(
                    {
                        "status": "completed",
                        "catalog_revision": _trim(
                            payload.get("revision")
                        ),
                        "content_hash": _trim(
                            payload.get("content_hash")
                        ),
                        "size": len(str(payload.get("content") or "")),
                    }
                )
            return _tool_result(payload)

        tools.append(read_finance_skill_reference)
        configured_tool_names = [
            _trim(item)
            for item in tool_context.get("allowed_agent_tools") or []
            if _trim(item)
        ]
        configured_specs = (
            self.tool_adapter.list_tool_specs(configured_tool_names)
            if configured_tool_names
            else []
        )
        for spec in configured_specs:
            if spec.name in {item.name for item in tools}:
                continue
            description = _trim(spec.description) or f"Run the configured financial tool {spec.name}."
            if spec.usage_notes:
                description = "\n".join([description, *spec.usage_notes])
            description = "\n".join(
                [
                    description,
                    (
                        "根据本工具声明的数据范围处理尚未完成的数据目标，复用本轮已取得的结果。"
                    ),
                ]
            )
            input_schema = (
                spec.schema
                if isinstance(spec.schema, dict) and spec.schema
                else {"type": "object", "properties": {}, "additionalProperties": True}
            )

            async def run_configured_tool(
                args: dict[str, Any],
                *,
                _tool_name: str = spec.name,
            ) -> dict[str, Any]:
                call_record: Dict[str, Any] = {
                    "tool": _tool_name,
                    "arguments": dict(args or {}),
                }
                tool_runtime.tracker["calls"].append(call_record)
                if not tool_runtime.configured_tool_allowed(_tool_name):
                    call_record["error"] = "caller_tool_not_allowed"
                    return _tool_result(
                        {
                            "tool": _tool_name,
                            "ok": False,
                            "error": "当前用户或运行策略未授权该补充工具。",
                        }
                    )
                try:
                    raw_result = await asyncio.to_thread(
                        self.tool_adapter.execute,
                        _tool_name,
                        dict(args or {}),
                    )
                except Exception as exc:
                    call_record["error"] = str(exc)[:1_000]
                    return _tool_result({"tool": _tool_name, "error": str(exc)})
                result = (
                    dict(raw_result)
                    if isinstance(raw_result, Mapping)
                    else {"ok": True, "data": raw_result}
                )
                if result.get("ok") is False:
                    call_record["error"] = _trim(result.get("error"))[:1_000]
                    return _tool_result(result)
                variable = self.result_store.register_tool_result(
                    session_id=tool_runtime.result_scope,
                    tool_name=_tool_name,
                    result=result,
                    task=json.dumps(dict(args or {}), ensure_ascii=False, default=str),
                    runtime_ctx={"conversation_id": tool_runtime.runtime_scope},
                )
                if not variable:
                    return _tool_result(result)
                summary = {
                    "tool": _tool_name,
                    "result_ref": variable.get("data_ref"),
                    "data_type": variable.get("data_type"),
                    "row_count": variable.get("row_count"),
                    "schema": variable.get("schema"),
                    "sample": variable.get("sample"),
                }
                tool_runtime.tracker["result_refs"].append(dict(summary))
                call_record["result_ref"] = variable.get("data_ref")
                call_record["row_count"] = variable.get("row_count")
                return _tool_result(summary)

            tools.append(
                tool(spec.name, description, input_schema)(run_configured_tool)
            )
        names = [f"mcp__finance__{item.name}" for item in tools]
        return tools, names, tracker

    def _catalog_index(self) -> list[dict[str, Any]]:
        return [
            {
                "name": row.get("name"),
                "desc": row.get("desc"),
                "dataviews": [
                    {
                        "name": item.get("name"),
                        "desc": item.get("desc"),
                        "operations": self._view_operations(item),
                    }
                    for item in row.get("dataviews") or []
                    if isinstance(item, Mapping)
                ],
            }
            for row in self.finance_catalog.build_tree().get("subjects") or []
            if isinstance(row, Mapping)
        ]

    def _catalog_routing_index(self) -> str:
        lines: list[str] = []
        for row in self.finance_catalog.build_tree().get("subjects") or []:
            if not isinstance(row, Mapping):
                continue
            subject = _trim(row.get("name"))
            if not subject:
                continue
            subject_description = _trim(row.get("desc"))
            # Subject is the visual parent, not another copy of all its views.
            label = _trim(row.get("public_name")) or subject_description
            lines.append(f"- {subject}（{label}）" if label else f"- {subject}")
            for item in row.get("dataviews") or []:
                if not isinstance(item, Mapping):
                    continue
                name = _trim(item.get("name"))
                if not name:
                    continue
                description = _trim(item.get("desc"))
                operations = "/".join(self._view_operations(item))
                suffix = f" [{operations}]" if operations else ""
                lines.append(f"  - {name}{suffix}：{description}")
        return "\n".join(lines)

    @staticmethod
    def _view_operations(view: Mapping[str, Any]) -> list[str]:
        return list(dict.fromkeys(
            _trim(function.get("operation"))
            for function in view.get("functions") or []
            if isinstance(function, Mapping) and _trim(function.get("operation"))
        ))

    def _subject_summary(self, subject: str) -> dict[str, Any]:
        row = self.finance_catalog.get_subject(subject)
        return {
            "name": row.get("name"),
            "desc": row.get("desc"),
            "dataviews": [
                {
                    "name": item.get("name"),
                    "desc": item.get("desc"),
                    "operations": self._view_operations(item),
                }
                for item in row.get("dataviews") or []
                if isinstance(item, Mapping)
            ],
        }
