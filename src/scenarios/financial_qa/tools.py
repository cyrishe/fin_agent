from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Dict, List, Mapping, Optional

from src.experiments.staged_data_protocol.phase2.models import ResultHandle
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


class FinanceDataQueryToolRuntime:
    """Conversation-owned query handles and observable turn evidence."""

    def __init__(self, *, result_store: SessionVariableStoreService) -> None:
        self.result_store = result_store
        self.owner_ids: List[str] = []
        self.tool_context: Dict[str, Any] = {}
        self.event_sink: Optional[Callable[[Dict[str, Any]], None]] = None
        self.result_handles: Dict[str, ResultHandle] = {}
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

    def remember(self, payload: Mapping[str, Any]) -> Optional[ResultHandle]:
        handle = _result_handle(payload)
        if handle is not None:
            self.result_handles[handle.name] = handle
        return handle

    def _restore_results(self) -> None:
        scope = self.result_scope
        if not scope or scope == self._restored_scope:
            return
        self.result_handles = {}
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
            self.remember(payload)
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
        self.finance_runtime = finance_runtime or FinanceDataToolRuntimeService()
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

        @tool(
            "read_finance_catalog",
            (
                "Read Fin Agent's finance data catalog progressively. With no arguments it returns the compact "
                "subject/dataview index; with subject it returns that subject's dataview summaries; with subject "
                "and dataview it returns the executable fields, methods, rules, and examples."
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
                return _tool_result(payload)
            except Exception as exc:
                return _tool_result(
                    {"subject": subject, "dataview": dataview, "error": str(exc)}
                )

        @tool(
            "finance_query",
            (
                "Execute one read-only request using Fin Agent's finance data protocol. Previous result names such "
                "as r1 are retained by the current financial QA conversation and can be referenced by later requests."
            ),
            {
                "type": "object",
                "properties": {
                    "request": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 4_000,
                        "description": (
                            "Executable DSL only, for example: "
                            "r1 = stock.quote(filter = \"code = 600519.SH\", limit = 1) "
                            "-> code, name, tradedate, open, close. Natural language is not accepted."
                        ),
                    }
                },
                "required": ["request"],
                "additionalProperties": False,
            },
        )
        async def finance_query(args: dict[str, Any]) -> dict[str, Any]:
            request = _trim(args.get("request"))
            call_record: Dict[str, Any] = {"tool": "finance_query", "request": request[:500]}
            tool_runtime.tracker["calls"].append(call_record)
            try:
                result = self.finance_runtime.execute_request(
                    request=request,
                    previous_results=tool_runtime.result_handles,
                )
            except Exception as exc:
                call_record["error"] = str(exc)[:1_000]
                return _tool_result({"request": request, "error": str(exc)})

            validation = (
                result.get("validation")
                if isinstance(result.get("validation"), Mapping)
                else {}
            )
            if not bool(validation.get("ok")):
                call_record["validation_errors"] = [
                    _trim(item) for item in validation.get("errors") or [] if _trim(item)
                ]
                return _tool_result(
                    {
                        "request": request,
                        "validation": dict(validation),
                    }
                )

            handle = tool_runtime.remember(result)
            variable = self.result_store.register_tool_result(
                session_id=tool_runtime.result_scope,
                tool_name="finance_data_query",
                result=result,
                task=request,
                runtime_ctx={"conversation_id": tool_runtime.runtime_scope},
                local_alias=handle.name if handle else "",
            )
            if not variable:
                return _tool_result(result)
            summary = {
                "request": request,
                "result_name": variable.get("local_alias"),
                "result_ref": variable.get("data_ref"),
                "data_type": variable.get("data_type"),
                "row_count": variable.get("row_count"),
                "schema": variable.get("schema"),
                "sample": variable.get("sample"),
                "warnings": [
                    _trim(item) for item in validation.get("warnings") or [] if _trim(item)
                ],
            }
            tool_runtime.tracker["result_refs"].append(dict(summary))
            call_record["result_name"] = variable.get("local_alias")
            call_record["row_count"] = variable.get("row_count")
            return _tool_result(summary)

        @tool(
            "load_finance_result",
            (
                "Load one page from a prior query or configured-tool result_ref in the current conversation. Use only when "
                "the compact schema and sample are insufficient for the answer or a later query."
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
