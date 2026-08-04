from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

import fastjsonschema

from src.services.custom_tool_service import CustomToolRuntimeService, CustomToolStoreService
from src.services.agent_providers.runtime_context import AgentRuntimeContextAdapter
from src.services.finance_data_tool_catalog_service import FinanceDataToolCatalogService
from src.services.finance_data_tool_runtime_service import FinanceDataToolRuntimeService
from src.experiments.staged_data_protocol.phase2.trade_date_resolver import TradeDateResolver
from src.services.session_variable_store_service import SessionVariableStoreService


def _trim(value: Any) -> str:
    return str(value or "").strip()


def _tool_result(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Return one MCP tool payload without adding a workflow status.

    The wrapped service's result is the fact provided to Finance CC.  This
    adapter deliberately does not manufacture ``ok`` / ``is_error`` fields or
    turn a business result into a control signal for the conversation.
    """
    normalized = dict(payload)
    text = json.dumps(normalized, ensure_ascii=False, default=str)
    truncated = len(text) > 50_000
    if truncated:
        text = text[:50_000] + "…"
    return {"content": [{"type": "text", "text": text}]}


class FinanceCcToolRuntime:
    """Mutable per-conversation tool bridge for one long-lived CC client."""

    def __init__(self) -> None:
        self.owner_ids: List[str] = []
        self.tool_context: Dict[str, Any] = {}
        self.event_sink: Optional[Callable[[Dict[str, Any]], None]] = None
        self.working_state: Dict[str, Any] = {}
        self.tracker: Dict[str, Any] = {}
        self.implementation_response: Optional[Dict[str, Any]] = None
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
        incoming_state = (
            tool_context.get("custom_tool_state")
            if isinstance(tool_context.get("custom_tool_state"), Mapping)
            else {}
        )
        if incoming_state:
            self.working_state = dict(incoming_state)
        self.tracker = {
            "calls": [],
            "interaction_requests": [],
            "artifact_updates": [],
            "asset_reads": [],
            "dynamic_runs": [],
            "implementation_runs": [],
            "result_refs": [],
        }
        self.implementation_response = None
        return self.tracker

    @property
    def runtime_scope(self) -> str:
        return _trim(self.tool_context.get("_agent_runtime_scope"))

    @property
    def result_scope(self) -> str:
        return self.runtime_scope or (_trim(self.owner_ids[0]) if self.owner_ids else "finance_cc")

    @property
    def live_tool_context(self) -> Dict[str, Any]:
        return {**self.tool_context, "custom_tool_state": self.working_state}


class FinanceCcSystemTools:
    """Narrow application tools exposed to Finance CC through in-process MCP."""

    def __init__(
        self,
        *,
        custom_tool_store: Optional[CustomToolStoreService] = None,
        custom_tool_runtime: Optional[CustomToolRuntimeService] = None,
        finance_runtime: Optional[FinanceDataToolRuntimeService] = None,
        finance_catalog: Optional[FinanceDataToolCatalogService] = None,
        implementation_runner: Optional[Callable[..., Dict[str, Any]]] = None,
        runtime_context_adapter: Optional[AgentRuntimeContextAdapter] = None,
        result_store: Optional[SessionVariableStoreService] = None,
    ) -> None:
        self.custom_tool_store = custom_tool_store or CustomToolStoreService()
        self.custom_tool_runtime = custom_tool_runtime or CustomToolRuntimeService(store=self.custom_tool_store)
        self.finance_runtime = finance_runtime or FinanceDataToolRuntimeService(
            trade_date_resolver=TradeDateResolver()
        )
        self.finance_catalog = finance_catalog or FinanceDataToolCatalogService()
        self.implementation_runner = implementation_runner
        self.runtime_context_adapter = runtime_context_adapter or AgentRuntimeContextAdapter()
        self.result_store = result_store or SessionVariableStoreService()

    def build_tools(
        self,
        *,
        owner_ids: List[str],
        tool_context: Mapping[str, Any],
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
        runtime: Optional[FinanceCcToolRuntime] = None,
    ) -> tuple[list[Any], list[str], Dict[str, Any]]:
        from claude_agent_sdk import tool

        tool_runtime = runtime or FinanceCcToolRuntime()
        tracker = tool_runtime.begin_turn(
            owner_ids=owner_ids,
            tool_context=tool_context,
            event_sink=event_sink,
        )

        def implementation_terminal_result() -> Optional[Dict[str, Any]]:
            if tool_runtime.implementation_response is None:
                return None
            return _tool_result(
                {
                    "code": "implementation_turn_complete",
                    "message": "本轮编码调用已经完成；结果已由系统保存并直接展示给用户。",
                }
            )

        def compact_execution_result(
            *,
            tool_name: str,
            result: Mapping[str, Any],
            task: str,
        ) -> Dict[str, Any]:
            variable = self.result_store.register_tool_result(
                session_id=tool_runtime.result_scope,
                tool_name=tool_name,
                result=result,
                task=task,
                runtime_ctx={"conversation_id": tool_runtime.runtime_scope},
                include_failed=True,
            )
            if not variable:
                return dict(result)
            execution = (
                result.get("execution")
                if isinstance(result.get("execution"), Mapping)
                else {}
            )
            message = _trim(result.get("message") or result.get("error"))
            if not message and execution and not bool(execution.get("ok")):
                message = _trim(execution.get("reason") or execution.get("status"))
            summary = {
                "message": message,
                "status": variable.get("status"),
                "source_tool": tool_name,
                "result_name": variable.get("local_alias"),
                "result_ref": variable.get("data_ref"),
                "data_type": variable.get("data_type"),
                "row_count": variable.get("row_count"),
                "schema": variable.get("schema"),
                "sample": variable.get("sample"),
            }
            if execution:
                summary["execution"] = dict(execution)
            tool_runtime.tracker["result_refs"].append(dict(summary))
            return summary

        def apply_artifact_to_working_state(artifact_type: str, payload: Mapping[str, Any]) -> None:
            working_state = tool_runtime.working_state
            if artifact_type == "requirement":
                brief = _trim(payload.get("requirement_brief"))
                if brief:
                    working_state["requirement_brief"] = brief
                    working_state.pop("understanding", None)
                notice = payload.get("notice")
                if isinstance(notice, list):
                    working_state["notice"] = [_trim(item) for item in notice if _trim(item)]
                questions = payload.get("questions")
                if isinstance(questions, list):
                    working_state["questions"] = [dict(item) for item in questions if isinstance(item, Mapping)]
                return
            if artifact_type == "design":
                design = payload.get("design")
                if isinstance(design, str) and _trim(design):
                    design_contract: dict[str, Any] = {"document": _trim(design)}
                elif isinstance(design, Mapping) and design:
                    design_contract = dict(design)
                else:
                    return
                finance_tool_profile = payload.get("finance_tool_profile")
                if isinstance(finance_tool_profile, Mapping) and finance_tool_profile:
                    design_contract["finance_tool_profile"] = dict(
                        finance_tool_profile
                    )
                working_state["design_contract"] = design_contract
                working_state["tool_name"] = _trim(design_contract.get("tool_name"))
                return
            if artifact_type == "flow":
                mermaid = _trim(payload.get("mermaid"))
                design = dict(working_state.get("design_contract") or {})
                if mermaid and design:
                    design["mermaid"] = mermaid
                    working_state["design_contract"] = design

        artifact_payload_schema = {
            "oneOf": [
                self._artifact_schema("requirement"),
                self._artifact_schema("design"),
                self._artifact_schema("flow"),
                self._artifact_schema("test_evidence"),
            ]
        }

        @tool(
            "finance_query",
            (
                "Execute one read-only request through Fin Agent's existing finance data protocol. "
                "Read the relevant api_catalog asset first when the request syntax or fields are not already known."
            ),
            {
                "type": "object",
                "properties": {
                    "request": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 4_000,
                        "description": "One request using the finance data protocol documented by api_catalog.",
                    }
                },
                "required": ["request"],
                "additionalProperties": False,
            },
        )
        async def finance_query(args: dict[str, Any]) -> dict[str, Any]:
            request = _trim(args.get("request"))
            tool_runtime.tracker["calls"].append({"tool": "finance_query", "request": request[:500]})
            try:
                result = self.finance_runtime.execute_request(request=request)
                compact = compact_execution_result(
                    tool_name="finance_data_query",
                    result=result,
                    task=request,
                )
                return _tool_result(compact)
            except Exception as exc:
                result = {"error": str(exc)}
                return _tool_result(
                    compact_execution_result(tool_name="finance_data_query", result=result, task=request),
                )

        @tool(
            "load_result",
            (
                "Load one page from a prior execution result in the current conversation. "
                "Execution tools return result_ref with a compact schema and sample; use this only when more rows "
                "or text are actually needed."
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
        async def load_result(args: dict[str, Any]) -> dict[str, Any]:
            result_ref = _trim(args.get("result_ref"))
            tool_runtime.tracker["calls"].append({"tool": "load_result", "result_ref": result_ref})
            try:
                payload = self.result_store.load_data_ref(
                    session_id=tool_runtime.result_scope,
                    data_ref=result_ref,
                    offset=int(args.get("offset") or 0),
                    limit=int(args.get("limit") or 50),
                )
                return _tool_result({"result_ref": result_ref, **payload})
            except Exception as exc:
                return _tool_result(
                    {"result_ref": result_ref, "error": str(exc)},
                )

        @tool(
            "read_finance_asset",
            (
                "Read an existing Fin Agent asset without regenerating it. Use api_catalog for finance data "
                "subjects/dataviews; use requirement, design, flow, code, tests, or tool_contract for the active custom tool."
            ),
            {
                "type": "object",
                "properties": {
                    "asset_type": {
                        "type": "string",
                        "enum": ["api_catalog", "requirement", "design", "flow", "code", "tests", "tool_contract"],
                    },
                    "tool_name": {"type": "string", "maxLength": 200},
                    "subject": {"type": "string", "maxLength": 100},
                    "dataview": {"type": "string", "maxLength": 100},
                },
                "required": ["asset_type"],
                "additionalProperties": False,
            },
        )
        async def read_finance_asset(args: dict[str, Any]) -> dict[str, Any]:
            asset_type = _trim(args.get("asset_type"))
            tool_runtime.tracker["calls"].append({"tool": "read_finance_asset", "asset_type": asset_type})
            try:
                payload = self._read_asset(
                    asset_type=asset_type,
                    tool_name=_trim(args.get("tool_name")),
                    subject=_trim(args.get("subject")),
                    dataview=_trim(args.get("dataview")),
                    owner_ids=tool_runtime.owner_ids,
                    tool_context=tool_runtime.live_tool_context,
                )
                tool_runtime.tracker["asset_reads"].append({"asset_type": asset_type, "payload": payload})
                return _tool_result(
                    {
                        "asset_type": asset_type,
                        "tool_name": (
                            ""
                            if asset_type == "api_catalog"
                            else _trim(
                                args.get("tool_name")
                                or tool_runtime.working_state.get("tool_name")
                                or tool_runtime.tool_context.get("custom_tool_name")
                            )
                        ),
                        "data": payload,
                    }
                )
            except Exception as exc:
                return _tool_result({"asset_type": asset_type, "error": str(exc)})

        @tool(
            "request_user_interaction",
            (
                "Present the saved requirement for confirmation and pause for the user. Questions may be empty; "
                "when present, the first candidate is the default and the frontend also allows a custom answer."
            ),
            {
                "type": "object",
                "properties": {
                    "questions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "question": {"type": "string", "minLength": 1},
                                "candidate": {
                                    "type": "array",
                                    "items": {"type": "string", "minLength": 1},
                                },
                            },
                            "required": ["question", "candidate"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["questions"],
                "additionalProperties": False,
            },
        )
        async def request_user_interaction(args: dict[str, Any]) -> dict[str, Any]:
            request = {
                "questions": [dict(item) for item in args.get("questions") or [] if isinstance(item, Mapping)],
            }
            tool_runtime.tracker["calls"].append({"tool": "request_user_interaction"})
            tool_runtime.tracker["interaction_requests"].append(request)
            return _tool_result({"message": "Interaction request recorded. Stop this turn and wait for the user."})

        @tool(
            "save_finance_artifact",
            (
                "Save one structured result produced by a financial-tool Skill. The payload is validated against "
                "the existing artifact protocol. Use requirement, design, flow, or test_evidence; do not save prose as an artifact."
            ),
            {
                "type": "object",
                "properties": {
                    "artifact_type": {
                        "type": "string",
                        "enum": ["requirement", "design", "flow", "test_evidence"],
                    },
                    "payload": artifact_payload_schema,
                },
                "required": ["artifact_type", "payload"],
                "additionalProperties": False,
            },
        )
        async def save_finance_artifact(args: dict[str, Any]) -> dict[str, Any]:
            artifact_type = _trim(args.get("artifact_type"))
            payload = dict(args.get("payload") or {})
            call_record = {"tool": "save_finance_artifact", "artifact_type": artifact_type}
            tool_runtime.tracker["calls"].append(call_record)
            try:
                schema = self._artifact_schema(artifact_type)
                fastjsonschema.validate(schema, payload)
            except Exception as exc:
                call_record["error"] = str(exc)[:1_000]
                return _tool_result(
                    {"artifact_type": artifact_type, "error": f"artifact validation failed: {exc}"},
                )
            if artifact_type == "flow" and not isinstance(
                tool_runtime.working_state.get("design_contract"), Mapping
            ):
                call_record["error"] = "flow requires a saved design"
                return _tool_result(
                    {
                        "artifact_type": artifact_type,
                        "error": "flow requires a saved design artifact in the current tool conversation",
                    },
                )
            tool_runtime.tracker["artifact_updates"].append({"artifact_type": artifact_type, "payload": payload})
            apply_artifact_to_working_state(artifact_type, payload)
            message = "Artifact recorded."
            if artifact_type == "design":
                message = (
                    "Design draft recorded. Before presenting it for confirmation or starting Coding, "
                    "run financial-tool-flowchart on this saved design and save the resulting flow artifact."
                )
            elif artifact_type == "flow":
                message = "Flow recorded. The current design is now complete for user review."
            return _tool_result(
                {
                    "artifact_type": artifact_type,
                    "tool_name": _trim(tool_runtime.working_state.get("tool_name")),
                    "message": message,
                }
            )

        @tool(
            "run_dynamic_tool",
            (
                "Run an existing saved dynamic financial tool with explicit JSON arguments. "
                "Use read_finance_asset with tool_contract first when its input contract is not already known. "
                "The returned data is objective business output for the user and must not by itself trigger code or design changes."
            ),
            {
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string", "minLength": 1, "maxLength": 200},
                    "arguments": {"type": "object"},
                },
                "required": ["tool_name", "arguments"],
                "additionalProperties": False,
            },
        )
        async def run_dynamic_tool(args: dict[str, Any]) -> dict[str, Any]:
            tool_name = _trim(args.get("tool_name"))
            arguments = dict(args.get("arguments") or {})
            tool_runtime.tracker["calls"].append({"tool": "run_dynamic_tool", "tool_name": tool_name})
            result = self.custom_tool_runtime.run(
                tool_name,
                arguments,
                owner_ids=tool_runtime.owner_ids or None,
                allow_inactive=True,
            )
            compact = compact_execution_result(
                tool_name=tool_name,
                result=result,
                task=f"run {tool_name}",
            )
            tool_runtime.tracker["dynamic_runs"].append(
                {"tool_name": tool_name, "arguments": arguments, "result": compact}
            )
            return _tool_result(compact)

        @tool(
            "implement_dynamic_tool",
            (
                "Implement or repair the active dynamic financial tool with the configured coding Agent. The tool reads the saved design "
                "and isolated Context Bundle itself; do not pass source code or the full design in this call."
            ),
            {
                "type": "object",
                "properties": {
                    "instruction": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 2_000,
                        "description": "The current implementation or repair request in natural language.",
                    }
                },
                "required": ["instruction"],
                "additionalProperties": False,
            },
        )
        async def implement_dynamic_tool(args: dict[str, Any]) -> dict[str, Any]:
            instruction = _trim(args.get("instruction"))
            tool_runtime.tracker["calls"].append({"tool": "implement_dynamic_tool", "instruction": instruction[:500]})
            design_contract = (
                tool_runtime.working_state.get("design_contract")
                if isinstance(tool_runtime.working_state.get("design_contract"), Mapping)
                else {}
            )
            if not _trim(design_contract.get("mermaid")):
                return _tool_result(
                    {
                        "error": "current design is not reviewable because its flow artifact is missing",
                        "message": (
                            "先基于已保存的设计生成并保存流程图，再进入 Coding。"
                        ),
                    },
                )
            finance_tool_profile = (
                design_contract.get("finance_tool_profile")
                if isinstance(design_contract.get("finance_tool_profile"), Mapping)
                else {}
            )
            if _trim(finance_tool_profile.get("family")).lower() == "action":
                return _tool_result(
                    {
                        "error": "action finance Tools are design-only",
                        "message": (
                            "当前方案属于高风险外部动作工具；本系统只保存和展示设计，"
                            "不会进入 Coding、注册或执行。"
                        ),
                    },
                )
            if tool_runtime.implementation_response is not None:
                return implementation_terminal_result() or _tool_result(
                    {"code": "implementation_turn_complete"},
                )
            if self.implementation_runner is None:
                return _tool_result(
                    {"error": "coding Agent implementation runner is unavailable."},
                )
            try:
                result = self.runtime_context_adapter.invoke(
                    scope_id=tool_runtime.runtime_scope,
                    role="tool_implementation",
                    runner=self.implementation_runner,
                    state=dict(tool_runtime.working_state),
                    owner_id=tool_runtime.owner_ids[0] if tool_runtime.owner_ids else "",
                    instruction=instruction,
                    event_sink=tool_runtime.event_sink,
                )
            except Exception as exc:
                tool_runtime.implementation_response = {
                    "message": f"代码实现未完成：{exc}",
                    "coding_error": {
                        "code": "implementation_runtime_error",
                        "summary": str(exc),
                    },
                }
                tool_runtime.tracker["implementation_runs"].append(
                    dict(tool_runtime.implementation_response)
                )
                return _tool_result(tool_runtime.implementation_response)
            tool = result.get("tool") if isinstance(result.get("tool"), Mapping) else {}
            manifest = tool.get("manifest") if isinstance(tool.get("manifest"), Mapping) else {}
            coding_status = _trim(result.get("coding_status"))
            test_result = dict(result.get("test_result") or {}) if isinstance(result.get("test_result"), Mapping) else {}
            final_state = dict(result.get("state") or {}) if isinstance(result.get("state"), Mapping) else {}
            implementation_review = (
                dict(result.get("implementation_review") or {})
                if isinstance(result.get("implementation_review"), Mapping)
                else {}
            )
            implementation_explanation = (
                dict(result.get("implementation_explanation") or {})
                if isinstance(result.get("implementation_explanation"), Mapping)
                else {}
            )
            coding_tests = [
                dict(item)
                for item in result.get("coding_tests") or []
                if isinstance(item, Mapping)
            ]
            safe_tool: dict[str, Any] = {}
            if manifest:
                safe_tool = {
                    "manifest": dict(manifest),
                    "input_schema": dict(tool.get("input_schema") or {}),
                    "output_schema": dict(tool.get("output_schema") or {}),
                    "modules": [
                        {
                            key: item.get(key)
                            for key in (
                                "module_id",
                                "language",
                                "entrypoint",
                                "role",
                                "responsibility",
                            )
                            if item.get(key) not in (None, "")
                        }
                        for item in tool.get("modules") or []
                        if isinstance(item, Mapping)
                    ],
                    "design_contract": dict(tool.get("design_contract") or {}),
                    "finance_tool_profile": dict(
                        tool.get("finance_tool_profile") or {}
                    ),
                    "strategy_runtime_profile": dict(
                        tool.get("strategy_runtime_profile") or {}
                    ),
                    "selection_output_profile": dict(
                        tool.get("selection_output_profile") or {}
                    ),
                    "storage": dict(tool.get("storage") or {}),
                }
            record = {
                "message": _trim(result.get("message")),
                "coding_status": coding_status,
                "coding_error": dict(result.get("coding_error") or {}) if isinstance(result.get("coding_error"), Mapping) else {},
                "test_result": test_result,
                "state": final_state,
                # Keep the saved, source-free revision contract available to the
                # renderer.  The LLM still receives only the terminal message.
                "tool": safe_tool,
                "implementation_review": implementation_review,
                "implementation_explanation": implementation_explanation,
                "coding_tests": coding_tests,
                "implementation_meta": (
                    dict(result.get("implementation_meta") or {})
                    if isinstance(result.get("implementation_meta"), Mapping)
                    else {}
                ),
            }
            tool_runtime.tracker["implementation_runs"].append(record)
            if isinstance(result.get("state"), Mapping):
                tool_runtime.working_state.update(dict(result.get("state") or {}))
            tool_response = {
                "message": (
                    _trim(record.get("message"))
                    or "编码调用已结束；系统将把已保存的产物直接展示给用户。"
                ),
            }
            tool_runtime.implementation_response = dict(tool_response)
            return _tool_result(tool_response)

        tools = [
            finance_query,
            load_result,
            read_finance_asset,
            request_user_interaction,
            save_finance_artifact,
            run_dynamic_tool,
            implement_dynamic_tool,
        ]
        names = [f"mcp__finance__{item.name}" for item in tools]
        return tools, names, tracker

    @staticmethod
    def _artifact_schema(artifact_type: str) -> Dict[str, Any]:
        paths = {
            "requirement": "src/skills/financial-tool-development/skills/financial-tool-requirement/schema.json",
            "design": "src/skills/financial-tool-development/schema.json",
            "flow": "src/skills/financial-tool-development/skills/financial-tool-flowchart/schema.json",
            "test_evidence": "src/skills/financial-tool-development/skills/financial-tool-test-execution/schema.json",
        }
        path = Path(paths.get(artifact_type, ""))
        if not path.is_file():
            raise ValueError(f"unsupported artifact_type: {artifact_type or '-'}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"invalid artifact schema: {artifact_type}")
        return payload

    def _read_asset(
        self,
        *,
        asset_type: str,
        tool_name: str,
        subject: str,
        dataview: str,
        owner_ids: List[str],
        tool_context: Mapping[str, Any],
    ) -> Any:
        if asset_type == "api_catalog":
            if subject and dataview:
                return self.finance_catalog.get_dataview(subject, dataview)
            if subject:
                return self.finance_catalog.get_subject(subject)
            tree = self.finance_catalog.build_tree()
            return {
                "version": tree.get("version"),
                "subjects": [
                    {
                        "name": item.get("name"),
                        "desc": item.get("desc"),
                        "dataviews": [view.get("name") for view in item.get("dataviews") or []],
                    }
                    for item in tree.get("subjects") or []
                    if isinstance(item, Mapping)
                ],
            }
        state = tool_context.get("custom_tool_state") if isinstance(tool_context.get("custom_tool_state"), Mapping) else {}
        if asset_type == "requirement":
            requirement_brief = _trim(state.get("requirement_brief"))
            if not requirement_brief and isinstance(state.get("understanding"), Mapping):
                requirement_brief = json.dumps(
                    dict(state.get("understanding") or {}),
                    ensure_ascii=False,
                )
            return {
                "requirement_brief": requirement_brief,
            }
        design = state.get("design_contract") if isinstance(state.get("design_contract"), Mapping) else {}
        if asset_type == "design":
            return dict(design)
        if asset_type == "flow":
            if _trim(design.get("mermaid")):
                return {"mermaid": _trim(design.get("mermaid"))}
            return dict(design.get("flow") or {}) if isinstance(design.get("flow"), Mapping) else {}
        resolved_tool_name = tool_name or _trim(state.get("tool_name")) or _trim(tool_context.get("custom_tool_name"))
        if not resolved_tool_name:
            raise ValueError("tool_name is required because the current conversation has no saved tool")
        bundle = self.custom_tool_store.load_for_runtime(
            resolved_tool_name,
            owner_ids=owner_ids or None,
            allow_inactive=True,
        )
        if asset_type == "code":
            return {
                "tool_name": resolved_tool_name,
                "modules": [dict(item) for item in bundle.get("modules") or [] if isinstance(item, Mapping)],
                "code": _trim(bundle.get("code")),
            }
        if asset_type == "tests":
            manifest = bundle.get("manifest") if isinstance(bundle.get("manifest"), Mapping) else {}
            return dict(manifest.get("last_test") or {})
        if asset_type == "tool_contract":
            return {
                "manifest": dict(bundle.get("manifest") or {}),
                "input_schema": dict(bundle.get("input_schema") or {}),
                "output_schema": dict(bundle.get("output_schema") or {}),
                "sample_input": dict(bundle.get("sample_input") or {}),
                "finance_tool_profile": dict(
                    bundle.get("finance_tool_profile") or {}
                ),
                "strategy_runtime_profile": dict(
                    bundle.get("strategy_runtime_profile") or {}
                ),
                "selection_output_profile": dict(
                    bundle.get("selection_output_profile") or {}
                ),
            }
        raise ValueError(f"unsupported asset_type: {asset_type or '-'}")
