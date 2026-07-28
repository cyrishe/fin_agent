from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Callable, Dict, List, Mapping, Optional

from src.experiments.staged_data_protocol.phase2.models import ResultHandle
from src.experiments.staged_data_protocol.phase2.trade_date_resolver import TradeDateResolver
from src.scenarios.financial_qa.result_registry import FinanceResultRegistry
from src.services.finance_data_tool_catalog_service import FinanceDataToolCatalogService
from src.services.finance_data_tool_runtime_service import FinanceDataToolRuntimeService
from src.services.session_variable_store_service import SessionVariableStoreService
from src.skill_runtime.tool_adapter import ToolAdapter


def _trim(value: Any) -> str:
    return str(value or "").strip()


def _tool_result(payload: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(dict(payload), ensure_ascii=False, default=str),
            }
        ]
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
            "calls": [],
            "catalog_reads": [],
            "result_refs": [],
            "restored_result_names": sorted(self.result_handles),
            "interaction_requests": [],
            "artifact_updates": [],
            "asset_reads": [],
            "dynamic_runs": [],
            "implementation_runs": [],
        }
        return self.tracker

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
    ) -> None:
        self.finance_runtime = finance_runtime or FinanceDataToolRuntimeService(
            trade_date_resolver=TradeDateResolver()
        )
        self.finance_catalog = finance_catalog or FinanceDataToolCatalogService()
        self.result_store = result_store or SessionVariableStoreService()
        self.tool_adapter = tool_adapter or ToolAdapter()

    def create_runtime(self) -> FinanceDataQueryToolRuntime:
        return FinanceDataQueryToolRuntime(result_store=self.result_store)

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
        routing_index = self._catalog_routing_index()
        @tool(
            "read_finance_catalog",
            (
                "Read Fin Agent's finance data catalog. Infer the subject and dataview from the user's request "
                "using the routing index below, then call this tool once with both subject and dataview to obtain "
                "the complete executable fields, methods, rules, and examples for that view. Do not routinely read "
                "the empty index or a subject summary first. Use a subject-only or empty read only when the request "
                "is genuinely ambiguous and the routing index cannot resolve it.\n\n"
                f"Current routing index:\n{routing_index}"
            ),
            {
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "maxLength": 100},
                    "dataview": {"type": "string", "maxLength": 100},
                },
                "additionalProperties": False,
            },
        )
        async def read_finance_catalog(args: dict[str, Any]) -> dict[str, Any]:
            subject = _trim(args.get("subject"))
            dataview = _trim(args.get("dataview"))
            tool_runtime.tracker["calls"].append(
                {"tool": "read_finance_catalog", "subject": subject, "dataview": dataview}
            )
            try:
                if dataview and not subject:
                    raise ValueError("subject is required when dataview is provided")
                if subject and dataview:
                    payload = {
                        "mode": "dataview",
                        "subject": subject,
                        "dataview": self.finance_catalog.get_dataview(subject, dataview),
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
                tool_runtime.tracker["catalog_reads"].append(
                    {"subject": subject, "dataview": dataview, "mode": payload["mode"]}
                )
                catalog_index = len(tool_runtime.tracker["catalog_reads"])
                if payload["mode"] == "dataview":
                    dataview_payload = (
                        payload.get("dataview")
                        if isinstance(payload.get("dataview"), Mapping)
                        else {}
                    )
                    description = (
                        _trim(dataview_payload.get("desc"))
                        .split("。", 1)[0]
                    )
                    description = re.split(r"[，：]", description, maxsplit=1)[0]
                    progress_text = (
                        f"已定位{description}，并确认了可用字段与时间口径。"
                        if description
                        else "已确认本题所需数据的字段与时间口径。"
                    )
                elif payload["mode"] == "subject":
                    progress_text = "已确认该金融主体可查询的数据范围。"
                else:
                    progress_text = "已读取金融数据目录，正在定位本题所需数据。"
                tool_runtime.emit_progress(
                    progress_text,
                    progress_id=f"finance_catalog_{catalog_index}",
                    title="数据口径",
                    status="completed",
                )
                return _tool_result(payload)
            except Exception as exc:
                tool_runtime.emit_progress(
                    "暂时无法确认本题所需的数据口径。",
                    progress_id=f"finance_catalog_{len(tool_runtime.tracker['catalog_reads']) + 1}",
                    title="数据口径",
                    status="error",
                )
                return _tool_result(
                    {"subject": subject, "dataview": dataview, "error": str(exc)}
                )

        @tool(
            "finance_query",
            (
                "Execute one minimal financial-data flow. Before calling: (1) list every business fact explicitly "
                "requested by the user; (2) include one step for every fact whose API can already be selected, including "
                "symbolic dependencies whose actual values need not be inspected; (3) require every filter predicate "
                "to come from an explicit user constraint, a catalog requirement, or an upstream identity scope. "
                "Ordering or ranking never implies an extra positive, non-null, or threshold filter. Each step has one "
                "natural-language goal and exactly one read-only DSL request. Use `step1.column`, `step2.column`, and "
                "so on within this flow; use existing rN.column across flows. Stop early only when the next API truly "
                "cannot be selected without inspecting returned values. The system assigns every formal rN and saves "
                "goals, server-applied selection, lineage, and column coverage. After execution use this decision gate: "
                "(A) validation or provider failure: repair only the failed step; (B) a concrete mismatch between goal "
                "and API, selection_applied, outputs, or time: correct only that mismatch; (C) otherwise the step is "
                "complete, so continue to a different goal or answer. Server-applied filter/order/limit and available "
                "identity refs remain valid even when a projected metric is null. Nulls, zero rows, or returned subsets "
                "must not trigger another API, realtime mode, or tool for the same fact. step_evidence and the compact "
                "working set are authoritative execution facts and contain no hidden full-table data. The live "
                "working set is supplied in the turn context and refreshed in every finance_query response; do not "
                "treat a tool description as mutable runtime state."
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
                                        "A later step may reference an earlier one as step1.column."
                                    ),
                                },
                            },
                            "required": ["goal", "request"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["steps"],
                "additionalProperties": False,
            },
        )
        async def finance_query(args: dict[str, Any]) -> dict[str, Any]:
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
                        "working_set": tool_runtime.working_set(),
                    }
                )
            completed_steps: Dict[int, str] = {}
            step_summaries: list[Dict[str, Any]] = []
            for step_number, step in enumerate(steps, start=1):
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
                tool_runtime.tracker["calls"].append(call_record)
                if not goal or not submitted_request:
                    call_record["error"] = "goal and request are required"
                    return _tool_result(
                        {
                            "error": "each step requires goal and request",
                            "failed_step": step_number,
                            "completed_steps": step_summaries,
                            "next_result_name": expected_result_name,
                            "working_set": tool_runtime.working_set(),
                        }
                    )
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
                    tool_runtime.emit_progress(
                        f"正在查询：{goal}",
                        progress_id=f"finance_query_step_{step_number}",
                        title=f"数据查询 {step_number}/{len(steps)}",
                    )
                    result = await asyncio.to_thread(
                        self.finance_runtime.execute_request,
                        request=request,
                        previous_results=dict(tool_runtime.result_handles),
                    )
                except Exception as exc:
                    call_record["error"] = str(exc)[:1_000]
                    tool_runtime.emit_progress(
                        "当前查询未完成，已保留此前取得的有效结果。",
                        progress_id=f"finance_query_step_{step_number}",
                        title=f"数据查询 {step_number}/{len(steps)}",
                        status="error",
                    )
                    return _tool_result(
                        {
                            "error": str(exc),
                            "failed_step": step_number,
                            "completed_steps": step_summaries,
                            "next_result_name": expected_result_name,
                            "working_set": tool_runtime.working_set(),
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
                        title=f"数据查询 {step_number}/{len(steps)}",
                        status="error",
                    )
                    return _tool_result(
                        {
                            "failed_step": step_number,
                            "goal": goal,
                            "request": request,
                            "validation": dict(validation),
                            "completed_steps": step_summaries,
                            "next_result_name": expected_result_name,
                            "working_set": tool_runtime.working_set(),
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
                        title=f"数据查询 {step_number}/{len(steps)}",
                        status="error",
                    )
                    return _tool_result(
                        {
                            "failed_step": step_number,
                            "goal": goal,
                            "request": request,
                            "execution": dict(execution),
                            "completed_steps": step_summaries,
                            "next_result_name": expected_result_name,
                            "working_set": tool_runtime.working_set(),
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
                        title=f"数据查询 {step_number}/{len(steps)}",
                        status="error",
                    )
                    return _tool_result(
                        {
                            "error": message,
                            "failed_step": step_number,
                            "completed_steps": step_summaries,
                            "next_result_name": expected_result_name,
                            "working_set": tool_runtime.working_set(),
                        }
                    )
                dependencies = tool_runtime.result_registry.dependencies(request)
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
                            "working_set": tool_runtime.working_set(),
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
                            "working_set": tool_runtime.working_set(),
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
                step_evidence = tool_runtime.result_registry.step_evidence(
                    current_entry,
                    call_args=call_args,
                )
                summary = {
                    "flow_step": step_number,
                    "goal": goal,
                    "request": request,
                    "api": handle.api,
                    "result_name": variable.get("local_alias"),
                    "result_ref": variable.get("data_ref"),
                    "data_type": variable.get("data_type"),
                    "row_count": variable.get("row_count"),
                    "step_evidence": step_evidence,
                    "schema": variable.get("schema"),
                    "sample": variable.get("sample"),
                    "sample_complete": bool(current_entry.get("sample_complete")),
                    "warnings": [
                        *[
                            _trim(item)
                            for item in validation.get("warnings") or []
                            if _trim(item)
                        ],
                        *date_warnings,
                    ],
                }
                step_summaries.append(summary)
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
                    title=f"数据查询 {step_number}/{len(steps)}",
                    status="completed",
                )

            response: Dict[str, Any] = {
                "ok": True,
                "steps": step_summaries,
                "next_result_name": tool_runtime.next_result_name,
                "working_set": tool_runtime.working_set(),
            }
            if len(step_summaries) == 1:
                response.update(step_summaries[0])
            return _tool_result(response)

        @tool(
            "load_finance_result",
            (
                "Load one page from a prior query or configured-tool result_ref in the current conversation. Use only when "
                "the compact schema and sample are insufficient for the answer or a later query. Never call this when "
                "the producing step says sample_complete=true; that sample already contains every result row."
            ),
            {
                "type": "object",
                "properties": {
                    "result_ref": {"type": "string", "minLength": 1, "maxLength": 200},
                    "offset": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "required": ["result_ref"],
                "additionalProperties": False,
            },
        )
        async def load_finance_result(args: dict[str, Any]) -> dict[str, Any]:
            result_ref = _trim(args.get("result_ref"))
            tool_runtime.tracker["calls"].append(
                {"tool": "load_finance_result", "result_ref": result_ref}
            )
            try:
                payload = self.result_store.load_data_ref(
                    session_id=tool_runtime.result_scope,
                    data_ref=result_ref,
                    offset=int(args.get("offset") or 0),
                    limit=int(args.get("limit") or 50),
                )
                return _tool_result({"result_ref": result_ref, **payload})
            except Exception as exc:
                return _tool_result({"result_ref": result_ref, "error": str(exc)})

        tools = [read_finance_catalog, finance_query, load_finance_result]
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
                        "Choose this path only when its documented output directly covers a distinct unresolved user "
                        "goal. Do not call it merely as a preflight before finance_query or in addition to "
                        "finance_query for the same fact. Preparing identifiers or inputs is appropriate only when "
                        "that preparation is this tool's documented purpose. A second tool is appropriate only for "
                        "a different user-required evidence type."
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
                "rules": row.get("rules") or [],
                "dataviews": [
                    {
                        "name": item.get("name"),
                        "desc": item.get("desc"),
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
            dataviews = [
                _trim(item.get("name"))
                for item in row.get("dataviews") or []
                if isinstance(item, Mapping) and _trim(item.get("name"))
            ]
            lines.append(f"- {subject}: {', '.join(dataviews)}")
        return "\n".join(lines)

    def _subject_summary(self, subject: str) -> dict[str, Any]:
        row = self.finance_catalog.get_subject(subject)
        return {
            "name": row.get("name"),
            "desc": row.get("desc"),
            "rules": row.get("rules") or [],
            "dataviews": [
                {
                    "name": item.get("name"),
                    "desc": item.get("desc"),
                    "rules": item.get("rules") or [],
                }
                for item in row.get("dataviews") or []
                if isinstance(item, Mapping)
            ],
        }
