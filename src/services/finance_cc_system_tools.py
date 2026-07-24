from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

import fastjsonschema

from src.services.custom_tool_service import CustomToolRuntimeService, CustomToolStoreService
from src.services.agent_providers.runtime_context import AgentRuntimeContextAdapter
from src.services.finance_data_tool_catalog_service import FinanceDataToolCatalogService
from src.services.finance_data_tool_runtime_service import FinanceDataToolRuntimeService
from src.services.session_variable_store_service import SessionVariableStoreService


def _trim(value: Any) -> str:
    return str(value or "").strip()


def _tool_result(payload: Mapping[str, Any], *, is_error: bool = False) -> Dict[str, Any]:
    normalized = dict(payload)
    text = json.dumps(normalized, ensure_ascii=False, default=str)
    truncated = len(text) > 50_000
    if truncated:
        text = text[:50_000] + "…"
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": {
            "ok": not is_error,
            "truncated": truncated,
            "summary": _trim(normalized.get("message") or normalized.get("error"))[:1_000],
        },
        **({"isError": True} if is_error else {}),
    }


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
        self.finance_runtime = finance_runtime or FinanceDataToolRuntimeService()
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
    ) -> tuple[list[Any], list[str], Dict[str, Any]]:
        from claude_agent_sdk import tool

        tracker: Dict[str, Any] = {
            "calls": [],
            "interaction_requests": [],
            "artifact_updates": [],
            "asset_reads": [],
            "dynamic_runs": [],
            "implementation_runs": [],
            "result_refs": [],
        }
        initial_state = (
            tool_context.get("custom_tool_state")
            if isinstance(tool_context.get("custom_tool_state"), Mapping)
            else {}
        )
        working_state: Dict[str, Any] = dict(initial_state)
        runtime_scope = _trim(tool_context.get("_agent_runtime_scope"))
        result_scope = runtime_scope or (_trim(owner_ids[0]) if owner_ids else "finance_cc")
        live_tool_context = {**dict(tool_context), "custom_tool_state": working_state}
        implementation_response: Optional[Dict[str, Any]] = None

        def implementation_terminal_result() -> Optional[Dict[str, Any]]:
            if implementation_response is None:
                return None
            return _tool_result(
                {
                    "ok": False,
                    "code": "implementation_turn_complete",
                    "message": (
                        "Codex 已完成本轮实现、执行验证和静态检查。"
                        "Finance CC 不再调用任何工具，请直接向用户总结 Codex 返回的实现事实。"
                    ),
                },
                is_error=True,
            )

        def compact_execution_result(
            *,
            tool_name: str,
            result: Mapping[str, Any],
            task: str,
        ) -> Dict[str, Any]:
            variable = self.result_store.register_tool_result(
                session_id=result_scope,
                tool_name=tool_name,
                result=result,
                task=task,
                runtime_ctx={"conversation_id": runtime_scope},
                include_failed=True,
            )
            if not variable:
                return dict(result)
            summary = {
                "ok": result.get("ok") is not False,
                "message": _trim(result.get("message") or result.get("error")),
                "source_tool": tool_name,
                "result_name": variable.get("local_alias"),
                "result_ref": variable.get("data_ref"),
                "data_type": variable.get("data_type"),
                "row_count": variable.get("row_count"),
                "schema": variable.get("schema"),
                "sample": variable.get("sample"),
            }
            tracker["result_refs"].append(dict(summary))
            return summary

        def apply_artifact_to_working_state(artifact_type: str, payload: Mapping[str, Any]) -> None:
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
                    working_state["design_contract"] = {"document": _trim(design)}
                elif isinstance(design, Mapping) and design:
                    working_state["design_contract"] = dict(design)
                    working_state["tool_name"] = _trim(design.get("tool_name"))
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
            if terminal := implementation_terminal_result():
                return terminal
            request = _trim(args.get("request"))
            tracker["calls"].append({"tool": "finance_query", "request": request[:500]})
            try:
                result = self.finance_runtime.execute_request(request=request)
                compact = compact_execution_result(
                    tool_name="finance_data_query",
                    result=result,
                    task=request,
                )
                return _tool_result(compact, is_error=result.get("validation", {}).get("ok") is False)
            except Exception as exc:
                result = {"ok": False, "error": str(exc)}
                return _tool_result(
                    compact_execution_result(tool_name="finance_data_query", result=result, task=request),
                    is_error=True,
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
            if terminal := implementation_terminal_result():
                return terminal
            result_ref = _trim(args.get("result_ref"))
            tracker["calls"].append({"tool": "load_result", "result_ref": result_ref})
            try:
                payload = self.result_store.load_data_ref(
                    session_id=result_scope,
                    data_ref=result_ref,
                    offset=int(args.get("offset") or 0),
                    limit=int(args.get("limit") or 50),
                )
                return _tool_result({"ok": True, "result_ref": result_ref, **payload})
            except Exception as exc:
                return _tool_result(
                    {"ok": False, "result_ref": result_ref, "error": str(exc)},
                    is_error=True,
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
            if terminal := implementation_terminal_result():
                return terminal
            asset_type = _trim(args.get("asset_type"))
            tracker["calls"].append({"tool": "read_finance_asset", "asset_type": asset_type})
            try:
                payload = self._read_asset(
                    asset_type=asset_type,
                    tool_name=_trim(args.get("tool_name")),
                    subject=_trim(args.get("subject")),
                    dataview=_trim(args.get("dataview")),
                    owner_ids=owner_ids,
                    tool_context=live_tool_context,
                )
                tracker["asset_reads"].append({"asset_type": asset_type, "payload": payload})
                return _tool_result(
                    {
                        "ok": True,
                        "asset_type": asset_type,
                        "tool_name": (
                            ""
                            if asset_type == "api_catalog"
                            else _trim(
                                args.get("tool_name")
                                or working_state.get("tool_name")
                                or tool_context.get("custom_tool_name")
                            )
                        ),
                        "data": payload,
                    }
                )
            except Exception as exc:
                return _tool_result({"ok": False, "asset_type": asset_type, "error": str(exc)}, is_error=True)

        @tool(
            "request_user_interaction",
            (
                "Pause for user input only when a missing decision materially affects the result. "
                "The first candidate is the default; the frontend always also allows a custom answer."
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
            if terminal := implementation_terminal_result():
                return terminal
            request = {
                "questions": [dict(item) for item in args.get("questions") or [] if isinstance(item, Mapping)],
            }
            tracker["calls"].append({"tool": "request_user_interaction"})
            tracker["interaction_requests"].append(request)
            return _tool_result({"ok": True, "message": "Interaction request recorded. Stop this turn and wait for the user."})

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
            if terminal := implementation_terminal_result():
                return terminal
            artifact_type = _trim(args.get("artifact_type"))
            payload = dict(args.get("payload") or {})
            call_record = {"tool": "save_finance_artifact", "artifact_type": artifact_type}
            tracker["calls"].append(call_record)
            try:
                schema = self._artifact_schema(artifact_type)
                fastjsonschema.validate(schema, payload)
            except Exception as exc:
                call_record["ok"] = False
                call_record["error"] = str(exc)[:1_000]
                return _tool_result(
                    {"ok": False, "artifact_type": artifact_type, "error": f"artifact validation failed: {exc}"},
                    is_error=True,
                )
            call_record["ok"] = True
            tracker["artifact_updates"].append({"artifact_type": artifact_type, "payload": payload})
            apply_artifact_to_working_state(artifact_type, payload)
            return _tool_result(
                {
                    "ok": True,
                    "artifact_type": artifact_type,
                    "tool_name": _trim(working_state.get("tool_name")),
                    "message": "Artifact recorded.",
                }
            )

        @tool(
            "run_dynamic_tool",
            (
                "Run an existing saved dynamic financial tool with explicit JSON arguments. "
                "Use read_finance_asset with tool_contract first when its input contract is not already known. "
                "Top-level ok/error describe technical execution only; data is objective business output for the user "
                "and must not by itself trigger code or design changes."
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
            if terminal := implementation_terminal_result():
                return terminal
            tool_name = _trim(args.get("tool_name"))
            arguments = dict(args.get("arguments") or {})
            tracker["calls"].append({"tool": "run_dynamic_tool", "tool_name": tool_name})
            result = self.custom_tool_runtime.run(
                tool_name,
                arguments,
                owner_ids=owner_ids or None,
                allow_inactive=True,
            )
            compact = compact_execution_result(
                tool_name=tool_name,
                result=result,
                task=f"run {tool_name}",
            )
            tracker["dynamic_runs"].append(
                {"tool_name": tool_name, "arguments": arguments, "result": compact}
            )
            return _tool_result(compact, is_error=not bool(result.get("ok")))

        @tool(
            "implement_dynamic_tool",
            (
                "Implement or repair the active dynamic financial tool with Codex mid. The tool reads the saved design "
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
            nonlocal implementation_response
            instruction = _trim(args.get("instruction"))
            tracker["calls"].append({"tool": "implement_dynamic_tool", "instruction": instruction[:500]})
            if implementation_response is not None:
                return implementation_terminal_result() or _tool_result(
                    {"ok": False, "code": "implementation_turn_complete"},
                    is_error=True,
                )
            if self.implementation_runner is None:
                return _tool_result(
                    {"ok": False, "error": "Codex implementation runner is unavailable."},
                    is_error=True,
                )
            active_design = (
                working_state.get("design_contract")
                if isinstance(working_state.get("design_contract"), Mapping)
                else {}
            )
            active_tool_name = _trim(working_state.get("tool_name") or active_design.get("tool_name"))
            delegated_role = f"tool_implementation:{active_tool_name or 'active_tool'}"
            result = self.runtime_context_adapter.invoke(
                scope_id=runtime_scope,
                role=delegated_role,
                runner=self.implementation_runner,
                state=dict(working_state),
                owner_id=owner_ids[0] if owner_ids else "",
                instruction=instruction,
                event_sink=event_sink,
            )
            tool = result.get("tool") if isinstance(result.get("tool"), Mapping) else {}
            manifest = tool.get("manifest") if isinstance(tool.get("manifest"), Mapping) else {}
            coding_status = _trim(result.get("coding_status"))
            test_result = dict(result.get("test_result") or {}) if isinstance(result.get("test_result"), Mapping) else {}
            execution_ok = test_result.get("execution_ok") is True
            final_state = dict(result.get("state") or {}) if isinstance(result.get("state"), Mapping) else {}
            resolved_tool_name = (
                _trim(manifest.get("tool_name"))
                or _trim(final_state.get("tool_name"))
                or active_tool_name
            )
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
            test_evidence = (
                compact_execution_result(
                    tool_name=f"{resolved_tool_name or 'dynamic_tool'}:technical_validation",
                    result=test_result,
                    task="Codex implementation validation",
                )
                if test_result
                else {}
            )
            technical_test = {
                "execution_ok": execution_ok,
                "contract_ok": test_result.get("contract_ok") is True,
                "summary": _trim(test_result.get("summary")),
                "error": _trim(test_result.get("error")),
                "result_ref": test_evidence.get("result_ref"),
            }
            record = {
                "ok": bool(manifest) and coding_status != "coding_failed" and not bool(result.get("coding_error")),
                "message": _trim(result.get("message")),
                "coding_status": coding_status,
                "coding_error": dict(result.get("coding_error") or {}) if isinstance(result.get("coding_error"), Mapping) else {},
                "test_result": technical_test,
                "state": final_state,
                "tool": {"manifest": dict(manifest)} if manifest else {},
                "implementation_review": implementation_review,
                "implementation_explanation": implementation_explanation,
                "coding_tests": coding_tests,
                "implementation_meta": (
                    dict(result.get("implementation_meta") or {})
                    if isinstance(result.get("implementation_meta"), Mapping)
                    else {}
                ),
            }
            tracker["implementation_runs"].append(record)
            if isinstance(result.get("state"), Mapping):
                working_state.update(dict(result.get("state") or {}))
            available_assets = []
            if working_state.get("design_contract"):
                available_assets.append("design")
                design = working_state.get("design_contract") or {}
                if design.get("mermaid") or design.get("flow"):
                    available_assets.append("flow")
            if manifest:
                available_assets.extend(["code", "tool_contract"])
            if test_result:
                available_assets.append("tests")
            tool_response = {
                "ok": bool(record["ok"]),
                "message": record["message"],
                "implementation": {
                    "tool_name": resolved_tool_name,
                    "display_name": _trim(manifest.get("display_name")),
                    "revision": int(manifest.get("current_revision") or final_state.get("implementation_revision") or 0),
                    "available_assets": available_assets,
                },
                "implementation_review": implementation_review,
                "implementation_explanation": implementation_explanation,
                "coding_tests": coding_tests,
                "technical_test": technical_test,
                "coding_error": record["coding_error"],
            }
            implementation_response = dict(tool_response)
            return _tool_result(tool_response, is_error=not bool(record["ok"]))

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
            }
        raise ValueError(f"unsupported asset_type: {asset_type or '-'}")
