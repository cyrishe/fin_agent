from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from src.scenarios.financial_qa.tools import FinanceDataQueryCcTools
from src.services.finance_claude_session_service import FinanceClaudeSessionService


def _trim(value: Any) -> str:
    return str(value or "").strip()


def _agent_harness_context(runtime_profile: Mapping[str, Any]) -> str:
    sections = (
        runtime_profile.get("sections")
        if isinstance(runtime_profile.get("sections"), list)
        else []
    )
    blocks: list[str] = []
    for item in sections:
        if not isinstance(item, Mapping):
            continue
        if _trim(item.get("section")) not in {"soul", "responsibilities"}:
            continue
        content = item.get("content")
        if isinstance(content, str) and _trim(content):
            blocks.append(_trim(content))
        elif isinstance(content, list):
            lines = [_trim(value) for value in content if _trim(value)]
            if lines:
                blocks.append("\n".join(f"- {line}" for line in lines))
        elif isinstance(content, Mapping) and content:
            blocks.append(json.dumps(dict(content), ensure_ascii=False))
    return "\n\n".join(blocks)


class FinancialQaCcService:
    """Primary CC conversation for investment_analyst normal_qa turns."""

    def __init__(
        self,
        *,
        enabled: Optional[bool] = None,
        session_service: Optional[FinanceClaudeSessionService] = None,
        system_tools: Optional[FinanceDataQueryCcTools] = None,
        root_dir: str | Path = "data/financial_qa_cc_sessions",
        log_path: str | Path = "outputs/financial_qa_cc/events.jsonl",
    ) -> None:
        enabled_text = _trim(os.environ.get("FINANCE_CC_FINANCIAL_QA_ENABLED")).lower()
        self.enabled = (
            bool(enabled)
            if enabled is not None
            else enabled_text in {"1", "true", "yes", "on"}
        )
        self.system_tools = system_tools or FinanceDataQueryCcTools()
        qa_max_turns = max(
            4,
            int(os.environ.get("FINANCE_CC_FINANCIAL_QA_MAX_TURNS") or 24),
        )
        self.session_service = session_service or FinanceClaudeSessionService(
            enabled=self.enabled,
            root_dir=root_dir,
            log_path=log_path,
            system_tools=self.system_tools,
            system_prompt_path="src/scenarios/financial_qa/system.md",
            skill_root="src",
            skill_names=["fin-agent-skills:stock-deep-dive"],
            runtime_scope_prefix="financial_qa",
            max_turns=qa_max_turns,
            system_context_paths=[
                "src/scenarios/financial_qa/data_query.md"
            ],
            effort=_trim(
                os.environ.get("FINANCE_CC_FINANCIAL_QA_EFFORT") or "medium"
            ),
        )

    def accepts(
        self,
        *,
        dispatch_plan: Mapping[str, Any],
        attachments: Optional[list[dict[str, Any]]] = None,
    ) -> bool:
        if not self.enabled or attachments:
            return False
        return (
            _trim(dispatch_plan.get("selected_agent")) == "investment_analyst"
            and _trim(dispatch_plan.get("turn_mode")) == "normal_qa"
            and _trim(dispatch_plan.get("entry")) == "agent_route"
        )

    def answer(
        self,
        *,
        thread_id: int | str,
        turn_id: int | str,
        owner_id: str,
        user_text: str,
        dispatch_plan: Mapping[str, Any],
        application_context: Optional[Mapping[str, Any]] = None,
        event_sink: Any = None,
    ) -> Dict[str, Any]:
        semantic_turn = (
            dispatch_plan.get("semantic_turn")
            if isinstance(dispatch_plan.get("semantic_turn"), Mapping)
            else {}
        )
        resolved_question = _trim(
            semantic_turn.get("resolved_question")
            or semantic_turn.get("ori_question")
            or user_text
        )
        app_context = (
            application_context
            if isinstance(application_context, Mapping)
            else {}
        )
        default_agent = (
            app_context.get("default_agent")
            if isinstance(app_context.get("default_agent"), Mapping)
            else {}
        )
        selected_agent_name = _trim(dispatch_plan.get("selected_agent"))
        available_agents = (
            app_context.get("available_agents")
            if isinstance(app_context.get("available_agents"), list)
            else []
        )
        selected_agent = next(
            (
                item
                for item in available_agents
                if isinstance(item, Mapping)
                and _trim(item.get("agent_name") or item.get("name"))
                == selected_agent_name
            ),
            default_agent,
        )
        runtime_profile = (
            selected_agent.get("runtime_profile")
            if isinstance(selected_agent.get("runtime_profile"), Mapping)
            else {}
        )
        allowed_agent_tools = [
            _trim(item)
            for item in (
                runtime_profile.get("tools")
                or selected_agent.get("tools")
                or []
            )
            if _trim(item)
        ]
        record = self.session_service.run_turn(
            thread_id=thread_id,
            turn_id=turn_id,
            owner_id=owner_id,
            user_text=resolved_question,
            context={
                "selected_agent": "investment_analyst",
                "turn_mode": "normal_qa",
                "entry": _trim(dispatch_plan.get("entry")) or "agent_route",
                "allowed_agent_tools": allowed_agent_tools,
                "_agent_system_prompt": _agent_harness_context(runtime_profile),
            },
            event_sink=event_sink,
        )
        result_refs = [
            dict(item)
            for item in record.get("result_refs") or []
            if isinstance(item, Mapping)
        ]
        error = _trim(record.get("error"))
        message = _trim(record.get("result")) or (
            f"金融专业问答暂时未完成：{error}" if error else "金融专业问答暂时没有返回内容。"
        )
        return {
            "mode": "financial_qa_cc",
            "message": message,
            "items": [],
            "financial_qa": {
                "session_id": _trim(record.get("session_id")),
                "resumed": bool(record.get("resumed")),
                "duration_ms": int(record.get("duration_ms") or 0),
                "tool_calls": [
                    dict(item)
                    for item in record.get("tool_calls") or []
                    if isinstance(item, Mapping)
                ],
                "agent_tool_names": [
                    _trim(item)
                    for item in record.get("agent_tool_names") or []
                    if _trim(item)
                ],
                "skill_results": [
                    _trim(item)
                    for item in record.get("skill_results") or []
                    if _trim(item)
                ],
                "result_refs": result_refs,
                "error": error,
            },
            "result_refs": result_refs,
        }

    def close(self) -> None:
        self.session_service.close()
