import datetime
import datetime as dt
import json
import os
import queue
import re
import threading
import time
import uuid
from collections import defaultdict
from copy import deepcopy
from decimal import Decimal
from itertools import combinations
from pathlib import Path
from urllib.parse import quote, urlencode

import pandas as pd
from dotenv import load_dotenv
from flask import Flask, Response, has_request_context, jsonify, make_response, redirect, render_template, request, send_from_directory

load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)

from src.services.agent_direct_response_service import AgentDirectResponseService
from src.services.agent_execution_service import AgentExecutionError, AgentExecutionService
from src.services.agent_studio_service import AgentStudioError, AgentStudioService
from src.services.answer_summary_service import AnswerSummaryService
from src.services.application_runtime_service import ApplicationRuntimeError, ApplicationRuntimeService
from src.services.application_studio_service import ApplicationStudioError, ApplicationStudioService
from src.services.application_workbench_service import ApplicationWorkbenchService
from src.services.assistant_dispatch_planner import AssistantDispatchPlanner
from src.services.attachment_service import AttachmentService, AttachmentServiceError
from src.services.asset_invocation_service import AssetInvocationError, AssetInvocationService
from src.services.custom_tool_service import (
    CustomToolAgentService,
    CustomToolError,
)
from src.services.custom_tool_run_trace_service import CustomToolRunTrace
from src.services.finance_claude_session_service import FinanceClaudeSessionService
from src.services.finance_cc_system_tools import FinanceCcSystemTools
from src.scenarios.financial_qa import FinancialQaCcService
from src.services.conversation_title_service import ConversationTitleService
from src.services.context_resolution_service import ContextResolutionError
from src.services.llm_stream_block_service import LlmStreamBlockBuilder
from src.services.runtime_conversation_service import RuntimeConversationService
from src.services.skill_blueprint_service import SkillBlueprintError, SkillBlueprintService
from src.services.skill_studio_service import SkillStudioError, SkillStudioService
from src.services.system_command_service import SystemCommandService
from src.services.task_service import AsyncTaskService, TaskCapacityError
from src.services.tool_plan_runtime_service import ToolPlanRuntimeService
from src.services.tool_studio_service import ToolStudioError, ToolStudioService
from src.services.user_session_service import UserSessionService
from src.services.vision_intake_service import VisionIntakeService, VisionIntakeServiceError
from src.skill_runtime import SkillRunner
from src.tools.simple_web_tool import search_simple_web


app = Flask(__name__, static_folder="static", static_url_path="/static")
REACT_FRONTEND_DIST_DIR = Path(
    os.environ.get("FIN_AGENT_FRONTEND_DIST")
    or Path(__file__).resolve().parents[2] / "frontend" / "dist"
).resolve()

stock_deep_dive_runner = SkillRunner()
async_task_service = AsyncTaskService()
application_studio_service = ApplicationStudioService()
agent_studio_service = AgentStudioService()
skill_studio_service = SkillStudioService()
skill_blueprint_service = SkillBlueprintService(skill_studio_service=skill_studio_service)
tool_studio_service = ToolStudioService()
runtime_conversation_service = RuntimeConversationService()
application_runtime_service = ApplicationRuntimeService()
application_workbench_service = ApplicationWorkbenchService(
    application_runtime_service=application_runtime_service,
)
financial_qa_cc_service = FinancialQaCcService()
assistant_dispatch_planner = AssistantDispatchPlanner(
    agent_owned_runtime_names=(
        {"investment_analyst"} if financial_qa_cc_service.enabled else set()
    )
)
user_session_service = UserSessionService()
attachment_service = AttachmentService()
vision_intake_service = VisionIntakeService(attachment_service=attachment_service)
tool_plan_runtime_service = ToolPlanRuntimeService()
system_command_service = SystemCommandService()
answer_summary_service = AnswerSummaryService()
conversation_title_service = ConversationTitleService()
custom_tool_agent_service = CustomToolAgentService()
finance_cc_shadow_service = FinanceClaudeSessionService(
    enabled=any(
        str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}
        for name in ("FINANCE_CC_SHADOW_ENABLED", "FINANCE_CC_TOOL_DEVELOPMENT_ENABLED")
    ),
    system_tools=FinanceCcSystemTools(
        custom_tool_store=custom_tool_agent_service.store,
        custom_tool_runtime=custom_tool_agent_service.runtime,
        implementation_runner=custom_tool_agent_service.implement_dynamic_tool,
    )
)
custom_tool_agent_service.set_finance_cc_service(finance_cc_shadow_service)
asset_invocation_service = AssetInvocationService(
    custom_tool_store=custom_tool_agent_service.store,
    attachment_service=attachment_service,
)
custom_tool_stream_requests: dict[str, dict] = {}
agent_execution_service = AgentExecutionService(
    application_runtime_service=application_runtime_service,
    skill_studio_service=skill_studio_service,
    async_task_service=async_task_service,
)
agent_direct_response_service = AgentDirectResponseService()


@app.route("/", methods=["GET"])
def root_entry():
    return redirect("/assistant")


def _resolve_current_guest_identity() -> dict:
    cookie_user_id = str(request.cookies.get(UserSessionService.GUEST_COOKIE_NAME, "") or "").strip()
    cookie_session_token = str(request.cookies.get("aiia_guest_session_token", "") or "").strip()
    if cookie_user_id and cookie_session_token:
        return {
            "user_id": cookie_user_id,
            "user_type": "guest",
            "session_token": cookie_session_token,
        }
    return user_session_service.resolve_or_create_guest(
        cookie_user_id=cookie_user_id,
        cookie_session_token=cookie_session_token,
        user_agent=request.headers.get("User-Agent", ""),
        remote_addr=request.headers.get("X-Forwarded-For", "") or request.remote_addr or "",
    )


def _make_guest_session_response(target_url: str, *, clear_thread: bool = False):
    guest_identity = _resolve_current_guest_identity()
    response = make_response(redirect(target_url))
    response.set_cookie(
        UserSessionService.GUEST_COOKIE_NAME,
        str(guest_identity.get("user_id") or ""),
        max_age=60 * 60 * 24 * 180,
        samesite="Lax",
    )
    response.set_cookie(
        "aiia_guest_session_token",
        str(guest_identity.get("session_token") or ""),
        max_age=60 * 60 * 24 * 30,
        samesite="Lax",
        httponly=True,
    )
    if clear_thread:
        response.delete_cookie(UserSessionService.THREAD_COOKIE_NAME)
    return response


def _parse_bool_flag(value: str, default: bool = False) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return bool(default)
    return text not in {"0", "false", "no", "off"}


def _schedule_thread_title(*, thread_id: int, user_text: str, expected_title: str) -> None:
    normalized_text = str(user_text or "").strip()
    if not thread_id or not normalized_text:
        return

    def worker() -> None:
        try:
            result = conversation_title_service.generate(user_text=normalized_text)
            runtime_conversation_service.update_thread_title(
                thread_id=int(thread_id),
                title=str(result.get("title") or "").strip(),
                expected_title=str(expected_title or "").strip(),
            )
        except Exception as exc:
            app.logger.warning("thread title generation failed thread_id=%s error=%s", thread_id, exc)

    threading.Thread(target=worker, daemon=True, name=f"thread-title-{int(thread_id)}").start()


def _submit_finance_cc_shadow(
    *,
    thread_id: int,
    turn_id: int,
    owner_id: str,
    user_text: str,
    dispatch_plan: dict,
    application_context: dict,
    thread_context: dict,
) -> None:
    shadow_enabled = str(os.environ.get("FINANCE_CC_SHADOW_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on"}
    if not shadow_enabled or not finance_cc_shadow_service.enabled or not str(user_text or "").strip():
        return
    if financial_qa_cc_service.accepts(dispatch_plan=dispatch_plan):
        return
    default_agent = application_context.get("default_agent") if isinstance(application_context.get("default_agent"), dict) else {}
    selected_agent = str(dispatch_plan.get("selected_agent") or default_agent.get("agent_name") or "").strip()
    if selected_agent != "investment_analyst":
        return
    tool_development_enabled = str(os.environ.get("FINANCE_CC_TOOL_DEVELOPMENT_ENABLED") or "").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if tool_development_enabled and str(dispatch_plan.get("entry") or "").strip() == "custom_tool_flow":
        return
    finance_cc_shadow_service.submit(
        thread_id=thread_id,
        turn_id=turn_id,
        owner_id=owner_id,
        user_text=user_text,
        context={
            "selected_agent": selected_agent,
            "turn_mode": str(dispatch_plan.get("turn_mode") or "").strip(),
            "entry": str(dispatch_plan.get("entry") or "").strip(),
            "has_custom_tool_state": isinstance(thread_context.get("custom_tool_state"), dict)
            and bool(thread_context.get("custom_tool_state")),
            "custom_tool_state": (
                dict(thread_context.get("custom_tool_state") or {})
                if isinstance(thread_context.get("custom_tool_state"), dict)
                else {}
            ),
            "custom_tool_name": str(
                (
                    thread_context.get("custom_tool_state")
                    if isinstance(thread_context.get("custom_tool_state"), dict)
                    else {}
                ).get("tool_name")
                or ""
            ).strip(),
        },
    )


def _with_script_root(path: str) -> str:
    forwarded_prefix = ""
    script_name = ""
    request_script_root = ""
    if has_request_context():
        forwarded_prefix = str(request.headers.get("X-Forwarded-Prefix", "") or "").strip()
        script_name = str(request.headers.get("X-Script-Name", "") or "").strip()
        request_script_root = str(request.script_root or "").strip()
    app_root = str(app.config.get("APPLICATION_ROOT", "") or "").strip()
    candidates = [
        request_script_root,
        forwarded_prefix,
        script_name,
        app_root,
    ]
    base = ""
    for value in candidates:
        normalized = str(value or "").strip().rstrip("/")
        if not normalized:
            continue
        if not normalized.startswith("/"):
            normalized = f"/{normalized}"
        base = normalized
        break
    clean_path = "/" + str(path or "").lstrip("/")
    return f"{base}{clean_path}" if base else clean_path


def _ui_application_context(application_name: str) -> dict:
    return application_workbench_service.get_ui_context(
        application_name,
        url_transform=_with_script_root,
    )


def _apply_application_workspace_orchestration(result: dict, application_context: dict | None = None) -> dict:
    return application_workbench_service.apply_workspace_orchestration(
        result,
        application_context=application_context,
        url_transform=_with_script_root,
    )


def _compact_names(values, *, limit: int = 4) -> list[str]:
    names = [str(item).strip() for item in (values or []) if str(item).strip()]
    if len(names) <= limit:
        return names
    return names[:limit] + [f"...(+{len(names) - limit})"]


def _build_skill_bundle_item(skill_name: str, bundle: dict) -> dict:
    files = bundle.get("files") if isinstance(bundle, dict) else {}
    skill_config = files.get("skill_config") if isinstance(files, dict) else {}
    blueprint_context = skill_config.get("blueprint_context") if isinstance(skill_config, dict) else {}
    if not isinstance(blueprint_context, dict):
        blueprint_context = {}
    return {
        "skill_name": str(skill_name or "").strip() or str(bundle.get("skill_name") or "").strip(),
        "status": str(skill_config.get("status") or "").strip(),
        "created_from": str(skill_config.get("created_from") or "").strip(),
        "source_skill_name": str(blueprint_context.get("source_skill_name") or "").strip(),
        "tool_mode": str(((skill_config.get("tool_policy") or {}).get("mode") or "")).strip(),
        "default_max_steps": int(skill_config.get("default_max_steps", 0) or 0),
        "expected_render_page_type": str(skill_config.get("expected_render_page_type") or "").strip(),
        "tools": _compact_names(skill_config.get("tools") or []),
        "required_tools": _compact_names(skill_config.get("required_tools_before_final") or []),
        "application_name": str(blueprint_context.get("application_name") or "").strip(),
        "agent_name": str(blueprint_context.get("agent_name") or "").strip(),
        "llm_enabled": bool(blueprint_context.get("llm_enabled", False)),
        "example_count": len(bundle.get("examples") or []),
    }


def _build_application_bundle_item(application_name: str, bundle: dict) -> dict:
    files = bundle.get("files") if isinstance(bundle, dict) else {}
    config = files.get("application_config") if isinstance(files, dict) else {}
    return {
        "application_name": str(application_name or "").strip() or str(bundle.get("application_name") or "").strip(),
        "display_name": str(config.get("display_name") or application_name).strip(),
        "status": str(config.get("status") or "").strip(),
        "version": str(config.get("version") or "").strip(),
        "domain": str(config.get("domain") or "").strip(),
        "default_agent": str(config.get("default_agent") or "").strip(),
        "available_agents": _compact_names(config.get("available_agents") or []),
        "default_skills": _compact_names(config.get("default_skills") or []),
        "default_tools": _compact_names(config.get("default_tools") or []),
    }


def _build_agent_bundle_item(agent_name: str, bundle: dict) -> dict:
    files = bundle.get("files") if isinstance(bundle, dict) else {}
    config = files.get("agent_config") if isinstance(files, dict) else {}
    return {
        "agent_name": str(agent_name or "").strip() or str(bundle.get("agent_name") or "").strip(),
        "display_name": str(config.get("display_name") or agent_name).strip(),
        "role": str(config.get("role") or "").strip(),
        "status": str(config.get("status") or "").strip(),
        "version": str(config.get("version") or "").strip(),
        "skills": _compact_names(config.get("skills") or []),
        "tools": _compact_names(config.get("tools") or []),
        "handoff_agents": _compact_names(config.get("handoff_agents") or []),
    }


def _build_tool_bundle_item(tool_name: str, bundle: dict) -> dict:
    files = bundle.get("files") if isinstance(bundle, dict) else {}
    spec = files.get("spec") if isinstance(files, dict) else {}
    definition = files.get("definition") if isinstance(files, dict) else {}
    profiles = definition.get("profiles") if isinstance(definition, dict) else {}
    real_profile = profiles.get("real") if isinstance(profiles, dict) and isinstance(profiles.get("real"), dict) else {}
    mock_profile = profiles.get("mock") if isinstance(profiles, dict) and isinstance(profiles.get("mock"), dict) else {}
    return {
        "tool_name": str(tool_name or "").strip() or str(bundle.get("tool_name") or "").strip(),
        "display_name": str((definition.get("identity") or {}).get("display_name") or tool_name).strip(),
        "status": str(definition.get("status") or "").strip(),
        "version": str(spec.get("version") or "").strip() or str(definition.get("version") or "").strip(),
        "domain": str(((definition.get("identity") or {}).get("domain")) or "").strip(),
        "real_enabled": bool(real_profile.get("enabled", False)),
        "mock_enabled": bool(mock_profile.get("enabled", False)),
    }


def _build_task_job_item(job: dict) -> dict:
    item = dict(job or {})
    job_id = str(item.get("job_id") or "").strip()
    if job_id:
        item["workspace_url"] = _with_script_root(f"/tasks/{quote(job_id)}/view")
        item["display_name"] = str(item.get("task_type") or item.get("job_id") or "task").strip()
    return item


def _build_route_item(
    *,
    application_name: str = "",
    agent_name: str = "",
    route_snapshot: dict | None = None,
    submit_status: str = "",
    selected_skill: str = "",
    job: dict | None = None,
    launchables: list[dict] | None = None,
    clarification_state: dict | None = None,
    input_error: str = "",
) -> dict:
    route = route_snapshot.get("route") if isinstance(route_snapshot, dict) else {}
    normalized_input = route.get("normalized_input") if isinstance(route, dict) else {}
    effective_clarification = clarification_state if isinstance(clarification_state, dict) else {}
    if not effective_clarification and isinstance(route_snapshot, dict):
        snapshot_clarification = route_snapshot.get("clarification_state")
        if isinstance(snapshot_clarification, dict):
            effective_clarification = snapshot_clarification
    first_goal = ""
    if isinstance(launchables, list) and launchables:
        first_goal = str((launchables[0] or {}).get("goal") or "").strip()
    return {
        "display_name": str(submit_status or "route").strip() or "route",
        "application_name": str(application_name or "").strip(),
        "agent_name": str(agent_name or "").strip(),
        "route_type": str((route.get("route_type") or "")).strip(),
        "skill_name": str(selected_skill or route.get("selected_skill") or "").strip(),
        "submit_status": str(submit_status or "").strip(),
        "question": str((normalized_input or {}).get("question") or "").strip(),
        "code": str((normalized_input or {}).get("code") or "").strip(),
        "name": str((normalized_input or {}).get("name") or "").strip(),
        "concept": str((normalized_input or {}).get("concept") or "").strip(),
        "launch_count": len(launchables or []),
        "first_goal": first_goal,
        "clarify_slots": list(effective_clarification.keys()) if isinstance(effective_clarification, dict) else [],
        "input_error": str(input_error or "").strip(),
        "job_id": str((job or {}).get("job_id") or "").strip(),
        "status": str((job or {}).get("status") or "").strip(),
        "current_stage": str((job or {}).get("current_stage") or "").strip(),
        "task_type": str((job or {}).get("task_type") or "").strip(),
    }


def _finalize_planning_task_state(task_state: dict | None, *, turn_id: int | None = None) -> dict:
    state = dict(task_state) if isinstance(task_state, dict) else {}
    job = state.get("job") if isinstance(state.get("job"), dict) else {}
    if not job:
        return {}
    normalized_job = dict(job)
    if turn_id:
        normalized_job["job_id"] = f"plan_{int(turn_id)}"
    elif not str(normalized_job.get("job_id") or "").strip():
        normalized_job["job_id"] = "planning_sync"
    state["job"] = normalized_job
    steps = state.get("steps") if isinstance(state.get("steps"), list) else []
    normalized_steps = []
    for index, item in enumerate(steps, start=1):
        if not isinstance(item, dict):
            continue
        normalized_steps.append(
            {
                "seq": int(item.get("seq") or index),
                "stage": str(item.get("stage") or "planning").strip() or "planning",
                "step_type": str(item.get("step_type") or "planning").strip() or "planning",
                "title": str(item.get("title") or "步骤").strip() or "步骤",
                "message": str(item.get("message") or "").strip(),
                "status": str(item.get("status") or "completed").strip() or "completed",
            }
        )
    state["steps"] = normalized_steps
    return state


def _merge_chat_task_states(planning_state: dict | None, runtime_state: dict | None) -> dict:
    planning = dict(planning_state) if isinstance(planning_state, dict) else {}
    runtime = dict(runtime_state) if isinstance(runtime_state, dict) else {}
    if not planning:
        return runtime
    if not runtime:
        return planning
    planning_steps = planning.get("steps") if isinstance(planning.get("steps"), list) else []
    runtime_steps = runtime.get("steps") if isinstance(runtime.get("steps"), list) else []
    merged_steps = []
    seq = 1
    for item in planning_steps + runtime_steps:
        if not isinstance(item, dict):
            continue
        merged = dict(item)
        merged["seq"] = seq
        seq += 1
        merged_steps.append(merged)
    runtime_job = runtime.get("job") if isinstance(runtime.get("job"), dict) else {}
    planning_job = planning.get("job") if isinstance(planning.get("job"), dict) else {}
    merged_job = dict(runtime_job or planning_job)
    if planning_job and runtime_job:
        merged_job["planning_job_id"] = str(planning_job.get("job_id") or "").strip()
    return {
        "job": merged_job,
        "steps": merged_steps,
    }


def _resolve_blueprint_assets(
    *,
    application_context: dict | None = None,
    selected_tools: list[str] | None = None,
    selected_skills: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    tools = [str(item).strip() for item in (selected_tools or []) if str(item).strip()]
    skills = [str(item).strip() for item in (selected_skills or []) if str(item).strip()]
    if tools or skills:
        return tools, skills
    default_agent = application_context.get("default_agent") if isinstance(application_context, dict) else {}
    if not isinstance(default_agent, dict):
        return tools, skills
    return (
        [str(item).strip() for item in default_agent.get("tools", []) if str(item).strip()],
        [str(item).strip() for item in default_agent.get("skills", []) if str(item).strip()],
    )


def _ensure_skill_draft_overwritable(skill_name: str) -> None:
    normalized = str(skill_name or "").strip()
    if not normalized:
        raise ValueError("skill_name 不能为空")
    try:
        bundle = skill_studio_service.load_skill_bundle(normalized)
    except FileNotFoundError:
        return
    except Exception:
        return
    skill_config = (bundle.get("files") or {}).get("skill_config") or {}
    status = str(skill_config.get("status") or "").strip().lower()
    created_from = str(skill_config.get("created_from") or "").strip().lower()
    if status == "draft" or created_from in {"template", "blueprint"}:
        return
    raise ValueError(f"skill {normalized} 已存在且不是 draft/template/blueprint，默认不允许覆盖；请换一个名字。")


def _resolve_working_skill_name(skill_name: str) -> str:
    normalized = str(skill_name or "").strip()
    if not normalized:
        raise ValueError("skill_name 不能为空")
    return normalized


def _canonical_skill_name(skill_name: str) -> str:
    normalized = str(skill_name or "").strip()
    if normalized.endswith("__refine_draft"):
        return normalized[: -len("__refine_draft")]
    return normalized


def _build_active_skill_context(skill_name: str) -> dict:
    resolved_skill_name = _resolve_working_skill_name(skill_name)
    canonical_skill_name = _canonical_skill_name(skill_name)
    return {
        "active_skill_name": resolved_skill_name,
        "active_skill_canonical_name": canonical_skill_name,
        "active_skill_is_draft": bool(resolved_skill_name.endswith("__refine_draft")),
    }


def _merge_thread_context_patches(*patches: dict | None) -> dict:
    merged: dict = {}
    allowed_top_level = {
        "reference_memory",
        "active_skill_name",
        "active_skill_canonical_name",
        "active_skill_is_draft",
        "active_agent_name",
        "last_image_attachment_ids",
        "last_image_type",
        "last_image_summary",
        "last_visual_subjects",
        "recent_task_type",
        "recent_result_subject",
        "custom_tool_state",
    }
    for patch in patches:
        if not isinstance(patch, dict):
            continue
        for key, value in patch.items():
            normalized_key = str(key)
            if normalized_key not in allowed_top_level:
                continue
            if normalized_key == "reference_memory" and isinstance(value, dict):
                merged[normalized_key] = {
                    "recent_result_subject": str(value.get("recent_result_subject") or "").strip(),
                    "recent_image_attachment_ids": value.get("recent_image_attachment_ids") if isinstance(value.get("recent_image_attachment_ids"), list) else [],
                    "objects": value.get("objects") if isinstance(value.get("objects"), list) else [],
                }
                continue
            merged[normalized_key] = value
    return merged


def _normalize_llm_usage(value: dict | None) -> dict:
    source = value if isinstance(value, dict) else {}
    return {
        "prompt_tokens": int(source.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(source.get("completion_tokens", 0) or 0),
        "total_tokens": int(source.get("total_tokens", 0) or 0),
        "call_count": int(source.get("call_count", 0) or 0),
    }


def _merge_llm_usage(*usages: dict | None) -> dict:
    merged = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "call_count": 0}
    for usage in usages:
        normalized = _normalize_llm_usage(usage)
        if (
            normalized["prompt_tokens"] > 0
            or normalized["completion_tokens"] > 0
            or normalized["total_tokens"] > 0
        ) and normalized["call_count"] <= 0:
            normalized["call_count"] = 1
        merged["prompt_tokens"] += normalized["prompt_tokens"]
        merged["completion_tokens"] += normalized["completion_tokens"]
        merged["total_tokens"] += normalized["total_tokens"]
        merged["call_count"] += normalized["call_count"]
    return merged


def _recent_image_attachment_ids(thread_context: dict | None = None) -> list[str]:
    ctx = thread_context if isinstance(thread_context, dict) else {}
    raw_items = ctx.get("last_image_attachment_ids")
    if not isinstance(raw_items, list):
        return []
    items: list[str] = []
    for item in raw_items:
        normalized = str(item or "").strip()
        if normalized:
            items.append(normalized)
    return items


def _build_stock_deep_dive_input(args) -> dict:
    code = str(args.get("code") or "").strip()
    name = str(args.get("name") or "").strip()
    question = str(args.get("question") or "").strip()
    as_of_date = str(args.get("as_of_date") or "").strip() or datetime.datetime.now().strftime("%Y-%m-%d")
    focus = str(args.get("focus") or "").strip() or "全面分析"
    runtime_mode = str(args.get("runtime_mode") or "").strip() or "interactive"

    return {
        "task_type": "stock_deep_dive",
        "code": code,
        "name": name,
        "runtime_mode": runtime_mode,
        "question": question
        or f"请从行情、资金、研报、新闻和风险角度，对{(name or code)}做一份专业、克制的投顾式分析。",
        "context": {
            "focus": focus,
            "as_of_date": as_of_date,
        },
    }


def _run_stock_deep_dive_from_request(args) -> dict:
    code = str(args.get("code") or "").strip()
    if not code:
        raise ValueError("code 不能为空")

    max_steps_raw = str(args.get("max_steps") or "").strip()
    max_steps = None
    if max_steps_raw:
        max_steps = max(1, int(max_steps_raw))

    input_payload = _build_stock_deep_dive_input(args)
    result = stock_deep_dive_runner.run(
        skill_name="stock_deep_dive",
        input_payload=input_payload,
        max_steps=max_steps,
        enable_think=_parse_bool_flag(args.get("enable_think"), default=False),
    )
    payload = result.to_dict()
    final_output = payload.get("final_output") or {}
    render_payload = final_output.get("render_payload")
    if isinstance(render_payload, dict):
        payload["render_payload"] = render_payload
    payload["input_payload"] = input_payload
    return payload


def _extract_request_payload() -> dict:
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data
    return {key: value for key, value in request.values.items()}


def _parse_chat_command(text: str) -> dict:
    raw = str(text or "").strip()
    if not raw.startswith("/"):
        return {"kind": "free_chat", "raw": raw}
    parts = raw.split()
    command = str(parts[0] or "").strip().lower()
    args = parts[1:]
    return {
        "kind": "slash",
        "command": command,
        "args": args,
        "raw": raw,
    }


def _custom_tool_arg_text(args: list[str]) -> str:
    text = " ".join(str(item or "") for item in (args or [])).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1].strip()
    return text


def _parse_custom_tool_json_arg(text: str) -> tuple[str, dict]:
    raw = str(text or "").strip()
    if not raw:
        return "", {}
    if " " not in raw:
        return raw, {}
    name, rest = raw.split(" ", 1)
    rest = rest.strip()
    if not rest:
        return name.strip(), {}
    try:
        payload = json.loads(rest)
    except Exception as exc:
        raise ValueError(f"custom_tool call 参数必须是 JSON 对象: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("custom_tool call 参数必须是 JSON 对象")
    return name.strip(), payload


def _custom_tool_context_patch(state: dict | None) -> dict:
    return {"custom_tool_state": state if isinstance(state, dict) and state else None}


def _custom_tool_owner_ids(
    *,
    thread_context: dict | None = None,
    user_id: str = "",
    thread_id: int | None = None,
) -> list[str]:
    values = [str(user_id or "").strip(), str(thread_id or "").strip()]
    if isinstance(thread_context, dict):
        values.extend(
            str(item or "").strip()
            for item in (thread_context.get("_custom_tool_owner_ids") or [])
        )
    return list(dict.fromkeys(item for item in values if item))


def _sse_payload(payload: dict) -> str:
    return f"data: {json.dumps(_to_json_safe(payload), ensure_ascii=False)}\n\n"


def _user_facing_implementation_summary(value: object) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\[[^\]]+\.py\]\([^)]*\)", "核心实现模块", text)
    text = re.sub(r"(?:/Volumes|/Users|/private|/tmp)/\S+", "内部实现模块", text)
    text = re.sub(r"\b\d+_[A-Za-z0-9_.-]+\.py\b", "核心实现模块", text)
    return text


def _schema_fields_for_display(schema: object) -> list[dict]:
    if not isinstance(schema, dict):
        return []
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = {
        str(item)
        for item in (schema.get("required") if isinstance(schema.get("required"), list) else [])
    }
    return [
        {
            "name": str(name),
            "type": str(spec.get("type") or ""),
            "required": str(name) in required,
            "description": str(spec.get("description") or ""),
        }
        for name, spec in properties.items()
        if isinstance(spec, dict)
    ]


def _custom_tool_result_blocks(result: dict, builder: LlmStreamBlockBuilder) -> list[dict]:
    blocks: list[dict] = []
    seen: set[str] = set()

    def _add_many(items: list[dict]) -> None:
        for item in items:
            block_id = str(item.get("block_id") or "").strip()
            key = block_id or f"seq:{item.get('seq')}"
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            blocks.append(item)

    tool_turn = result.get("tool_turn") if isinstance(result.get("tool_turn"), dict) else {}
    tool_turn_action = str(tool_turn.get("action") or "").strip()
    tool_turn_actions = [
        str(item or "").strip()
        for item in (tool_turn.get("actions") if isinstance(tool_turn.get("actions"), list) else [tool_turn_action])
        if str(item or "").strip()
    ]
    tool_turn_actions = ["view" if item == "direct" else item for item in tool_turn_actions]
    available_view_assets = result.get("view_assets") or result.get("direct_assets") or []
    if available_view_assets and "view" not in tool_turn_actions:
        tool_turn_actions.append("view")
    view_block_count = 0
    if "view" in tool_turn_actions:
        view_answer = str(result.get("view_answer") or result.get("direct_answer") or result.get("message") or "").strip()
        if view_answer:
            _add_many([builder.make_block(
                block_id="custom_tool_view_answer",
                block_type="markdown",
                mode="replace",
                title="当前工具",
                content=view_answer,
                stage="view",
                data={"role": "view_answer", "state_changed": False},
            )])
        for asset in available_view_assets:
            if not isinstance(asset, dict):
                continue
            asset_type = str(asset.get("type") or "").strip()
            payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
            if asset_type == "design":
                _add_many([builder.make_block(
                    block_id="custom_tool_view_design",
                    block_type="artifact",
                    title="当前设计",
                    stage="view",
                    data={"artifact_type": "finance.tool_spec", "content": payload},
                )])
            elif asset_type == "flow":
                steps = [item for item in payload.get("steps") or [] if isinstance(item, dict)]
                links = [item for item in payload.get("links") or [] if isinstance(item, dict)]
                mermaid = str(payload.get("mermaid") or "").strip()
                _add_many([builder.make_block(
                    block_id="custom_tool_view_flow",
                    block_type="flowchart",
                    title="当前流程",
                    stage="view",
                    data={
                        "source": mermaid,
                        "nodes": [
                            {
                                "id": str(item.get("id") or item.get("step_id") or f"step_{index + 1}"),
                                "label": str(item.get("name") or item.get("title") or item.get("label") or f"步骤 {index + 1}"),
                                "detail": str(item.get("description") or item.get("detail") or ""),
                            }
                            for index, item in enumerate(steps)
                        ],
                        "edges": [
                            {
                                "from": str(item.get("from") or item.get("source") or ""),
                                "to": str(item.get("to") or item.get("target") or ""),
                                "label": str(item.get("label") or item.get("condition") or ""),
                            }
                            for item in links
                        ],
                    },
                )])
            elif asset_type == "code":
                modules = [item for item in payload.get("modules") or [] if isinstance(item, dict)]
                files = [
                    {
                        "id": str(item.get("module_id") or item.get("name") or f"module_{index + 1}"),
                        "name": str(item.get("module_id") or item.get("name") or f"module_{index + 1}"),
                        "language": str(item.get("language") or "python"),
                        "content": str(item.get("source_code") or item.get("code") or item.get("content") or ""),
                    }
                    for index, item in enumerate(modules)
                ]
                if not files and str(payload.get("code") or "").strip():
                    files = [{"id": "main", "name": "main", "language": "python", "content": str(payload.get("code") or "")}]
                _add_many([builder.make_block(
                    block_id="custom_tool_view_code",
                    block_type="code",
                    title="当前核心代码",
                    stage="view",
                    data={"files": files, "runtime": payload.get("runtime") or {"status": "idle"}},
                )])
            elif asset_type == "tests":
                cases = [item for item in payload.get("cases") or [] if isinstance(item, dict)]
                _add_many([builder.make_block(
                    block_id="custom_tool_view_tests",
                    block_type="assessment",
                    title="最近测试结果",
                    stage="view",
                    data={
                        "overall": "pass" if payload.get("execution_ok") is True else "fail",
                        "summary": str(payload.get("summary") or "最近一次测试结果"),
                        "issues": [] if payload.get("execution_ok") is True else [str(payload.get("error") or "技术运行失败")],
                        "details": {"tests": cases},
                    },
                )])
        view_block_count = len(blocks)

    design = result.get("design") if isinstance(result.get("design"), dict) else {}
    understanding = result.get("understanding") if isinstance(result.get("understanding"), dict) else {}
    notice = result.get("notice") if isinstance(result.get("notice"), list) else []
    questions = result.get("questions") if isinstance(result.get("questions"), list) else []
    if design or understanding or notice or questions:
        result_stage = "design" if design else "requirement"
        _add_many(builder.final_to_blocks({
            "source": "model",
            "type": "final",
            "status": str(result.get("design_status") or "review"),
            "message": str(result.get("message") or ""),
            "understanding": understanding,
            "notice": notice,
            "questions": questions,
            "design": design,
            "design_artifact": result.get("design_artifact") if isinstance(result.get("design_artifact"), dict) else {},
            "design_context": result.get("design_context") if isinstance(result.get("design_context"), dict) else {},
            "existing_analysis": result.get("existing_analysis") if isinstance(result.get("existing_analysis"), dict) else {},
        }, stage=result_stage))

    tool = result.get("tool") if isinstance(result.get("tool"), dict) else {}
    manifest = tool.get("manifest") if isinstance(tool.get("manifest"), dict) else {}
    test_result = result.get("test_result") if isinstance(result.get("test_result"), dict) else {}
    implementation_explanation = (
        result.get("implementation_explanation")
        if isinstance(result.get("implementation_explanation"), dict)
        else {}
    )
    implementation_review = (
        result.get("implementation_review")
        if isinstance(result.get("implementation_review"), dict)
        else {}
    )
    implementation_summary = _user_facing_implementation_summary(
        implementation_explanation.get("summary")
    )
    alignment_summary = str(
        implementation_review.get("summary")
        or implementation_review.get("conclusion")
        or ""
    ).strip()
    coding_status = str(result.get("coding_status") or "").strip()
    if coding_status == "coding_failed":
        coding_error = result.get("coding_error") if isinstance(result.get("coding_error"), dict) else {}
        error_summary = str(coding_error.get("summary") or "本次没有取得有效实现结果。").strip()
        _add_many([
            builder.make_block(
                block_id="custom_tool_coding_failure",
                block_type="assessment",
                mode="replace",
                title="实现未完成",
                data={
                    "overall": "fail",
                    "summary": error_summary,
                    "issues": [error_summary],
                    "details": {
                        "error_code": str(coding_error.get("code") or "coding_runtime_failed"),
                        "trace_id": str((result.get("diagnostic_trace") or {}).get("run_id") or ""),
                    },
                },
                stage="coding",
            ),
            builder.make_block(
                block_id="custom_tool_coding_retry",
                block_type="interaction",
                mode="replace",
                title="重试实现",
                content="设计稿没有被修改。可以直接重试 Coding，或在对话中补充实现反馈。",
                data={
                    "interaction_id": "custom_tool.coding_failure",
                    "intent": "retry",
                    "submission_mode": "action",
                    "prompt": "是否重新执行实现阶段？",
                    "actions": [{
                        "action_id": "custom_tool.retry_coding",
                        "label": "重试实现",
                        "intent": "accept",
                        "style": "primary",
                    }],
                },
                stage="coding",
            ),
        ])
    if manifest:
        storage = tool.get("storage") if isinstance(tool.get("storage"), dict) else {}
        is_active = str(manifest.get("status") or "").strip() == "active"
        input_fields = _schema_fields_for_display(tool.get("input_schema"))
        output_fields = _schema_fields_for_display(tool.get("output_schema"))
        logical_modules = [
            {
                "name": str(item.get("title") or item.get("name") or item.get("module_id") or ""),
                "description": str(item.get("description") or item.get("responsibility") or item.get("role") or ""),
            }
            for item in (tool.get("modules") or [])
            if isinstance(item, dict)
            and (
                str(item.get("module_id") or "").strip().lower() not in {"", "main"}
                or str(item.get("role") or item.get("responsibility") or "").strip()
            )
        ]
        _add_many([builder.make_block(
            block_id="custom_tool_draft_summary",
            block_type="artifact",
            mode="replace",
            title=str(manifest.get("display_name") or "工具草稿"),
            data={
                "artifact_type": "finance.custom_tool_implementation",
                "lifecycle": "active" if is_active else "draft",
                "version": str(manifest.get("current_revision") or "0.1"),
                "summary": str(manifest.get("description") or "").strip(),
                "items": [
                    {"label": "当前状态", "value": "已启用" if is_active else "待确认"},
                    {"label": "版本", "value": manifest.get("current_revision") or "0.1"},
                    {"label": "运行方式", "value": "动态加载" if storage else "动态执行"},
                ],
                "details": {
                    "inputs": input_fields,
                    "outputs": output_fields,
                    "modules": logical_modules,
                },
            },
            stage="coding",
        )])
        if implementation_summary:
            _add_many([builder.make_block(
                block_id="custom_tool_implementation_summary",
                block_type="narrative",
                mode="replace",
                title="实现与验证说明",
                content=implementation_summary,
                stage="coding",
            )])
        if alignment_summary and alignment_summary != implementation_summary:
            _add_many([builder.make_block(
                block_id="custom_tool_implementation_alignment",
                block_type="narrative",
                mode="replace",
                title="需求、设计与实现对照",
                content=alignment_summary,
                stage="coding",
            )])
        if test_result:
            cases = [item for item in test_result.get("cases") or [] if isinstance(item, dict)]
            execution_ok = test_result.get("execution_ok") is True
            _add_many([builder.make_block(
                block_id="custom_tool_test_result",
                block_type="assessment",
                mode="replace",
                title="样例测试",
                data={
                    "overall": "pass" if execution_ok else "fail",
                    "summary": str(test_result.get("summary") or ("样例技术运行成功。" if execution_ok else "样例技术运行失败。")),
                    "issues": [] if execution_ok else [str(test_result.get("error") or "技术运行失败")],
                    "details": {
                        "tests": [
                            {
                                "name": str(item.get("test_id") or ""),
                                "status": str(item.get("status") or ""),
                                "summary": str(item.get("purpose") or item.get("error") or ""),
                                "input": item.get("input") if isinstance(item.get("input"), dict) else {},
                                "expected": item.get("expected") if isinstance(item.get("expected"), dict) else {},
                                "actual": item.get("actual") if isinstance(item.get("actual"), dict) else {},
                                "key_process_info": (
                                    item.get("actual", {}).get("key_process_info")
                                    if isinstance(item.get("actual"), dict)
                                    and isinstance(item.get("actual", {}).get("key_process_info"), dict)
                                    else {}
                                ),
                                "logs": [dict(log) for log in (item.get("logs") or []) if isinstance(log, dict)],
                            }
                            for item in cases
                        ]
                    },
                },
                stage="coding",
            )])
        if not is_active and coding_status == "implemented":
            revision = int(manifest.get("current_revision") or 0)
            _add_many([builder.make_block(
                block_id="custom_tool_coding_review",
                block_type="interaction",
                mode="replace",
                title="确认实现",
                content="实现和 Coding 检查结果已保存。确认后启用当前版本；如果不符合预期，直接说明修改点。",
                data={
                    "interaction_id": "custom_tool.coding_review",
                    "intent": "confirm",
                    "submission_mode": "action",
                    "prompt": "是否启用这个动态工具版本？",
                    "subject_ref": str(manifest.get("tool_name") or ""),
                    "subject_revision": revision,
                    "actions": [
                        {
                            "action_id": "custom_tool.activate_draft",
                            "label": "确认并启用",
                            "intent": "accept",
                            "style": "primary",
                            "expected_revision": revision,
                        },
                        {
                            "action_id": "custom_tool.revise_implementation",
                            "label": "继续修改",
                            "intent": "edit",
                            "style": "default",
                            "expected_revision": revision,
                        },
                    ],
                },
                stage="coding",
            )])
    work_positions = [
        tool_turn_actions.index(action)
        for action in ("design", "coding")
        if action in tool_turn_actions
    ]
    if view_block_count and work_positions and tool_turn_actions.index("view") > min(work_positions):
        blocks = blocks[view_block_count:] + blocks[:view_block_count]
    return blocks


def _custom_tool_interaction_text(text: str, response: dict) -> str:
    raw = str(text or "").strip()
    action_id = str(response.get("action_id") or "").strip()
    if action_id == "custom_tool.submit_clarification":
        lines = []
        for item in response.get("answers") if isinstance(response.get("answers"), list) else []:
            if not isinstance(item, dict):
                continue
            question = str(item.get("question") or item.get("question_id") or "待确认项").strip()
            value = str(item.get("answer") or item.get("value") or "").strip()
            if not value:
                continue
            lines.append(f"关于「{question}」，我的回答是：{value}。")
        if raw:
            lines.append(raw)
        return "\n".join(lines) or "我确认当前需求理解，请继续形成设计方案。"
    if raw:
        return raw
    if action_id in {"custom_tool.revise_design", "custom_tool.revise_implementation"}:
        return str(response.get("feedback_text") or "").strip()
    return ""


def _resolved_dispatch_question(dispatch_plan: dict, fallback: str) -> str:
    resolution = dispatch_plan.get("context_resolution") if isinstance(dispatch_plan.get("context_resolution"), dict) else {}
    return str(resolution.get("resolved_question") or fallback or "").strip()


def _run_custom_tool_stream_payload(payload: dict, *, emit) -> None:
    text = str(payload.get("text") or "").strip()
    interaction_response = payload.get("interaction_response") if isinstance(payload.get("interaction_response"), dict) else {}
    text = _custom_tool_interaction_text(text, interaction_response)
    interaction_action_id = str(interaction_response.get("action_id") or "").strip()
    if interaction_response and not text and interaction_action_id in {
        "custom_tool.submit_clarification",
        "custom_tool.revise_design",
        "custom_tool.revise_implementation",
    }:
        raise CustomToolError("请先填写本轮反馈")
    direct_interaction = bool(interaction_response and not text)
    application_name = str(payload.get("application_name") or "investment_workbench").strip() or "investment_workbench"
    thread_id_payload = payload.get("thread_id")
    guest_identity = payload.get("guest_identity") if isinstance(payload.get("guest_identity"), dict) else {}
    builder = LlmStreamBlockBuilder(run_id=str(payload.get("run_id") or ""))
    run_trace = CustomToolRunTrace(run_id=builder.run_id)
    run_trace.snapshot("incoming_request", payload, section="request")
    raw_events: list[dict] = []
    thread_id = None
    turn_id = None
    raw_event_count = 0
    event_type_counts: dict[str, int] = defaultdict(int)
    persisted_event_types = {
        "stage_start",
        "context_ready",
        "tool_call",
        "turn_started",
        "turn_completed",
        "tool_result",
        "stage_result",
        "error",
    }

    def event_sink(event: dict) -> None:
        nonlocal raw_event_count
        run_trace.record(event)
        raw_event_count += 1
        event_type = str(event.get("type") or "unknown").strip() or "unknown"
        source = str(event.get("source") or "unknown").strip() or "unknown"
        event_type_counts[f"{source}:{event_type}"] += 1
        if event_type in persisted_event_types and len(raw_events) < 200:
            raw_events.append(event)
        if str(event.get("source") or "") == "model" and str(event.get("type") or "") == "final":
            return
        for block in builder.event_to_blocks(event):
            emit(block)

    try:
        app_ctx = application_runtime_service.get_application_context(application_name)
        initial_thread_title = str(app_ctx.get("display_name") or application_name)
        requested_thread_id = (
            int(thread_id_payload)
            if str(thread_id_payload or "").strip().isdigit()
            else UserSessionService._safe_int(str(payload.get("cookie_thread_id") or ""))
        )
        thread_id = runtime_conversation_service.ensure_thread(
            thread_id=requested_thread_id,
            title=initial_thread_title,
            owner_type="user",
            owner_id=str(guest_identity.get("user_id") or ""),
            context_summary=f"{application_name} 会话",
        )
        thread_context = runtime_conversation_service.get_thread_context(thread_id=thread_id)
        context_window = runtime_conversation_service.get_context_window(thread_id=thread_id, max_rounds=5)
        if context_window:
            thread_context = {**thread_context, "context_window": context_window}
        owner_ids = _custom_tool_owner_ids(
            thread_context=thread_context,
            user_id=str(guest_identity.get("user_id") or ""),
            thread_id=thread_id,
        )
        thread_context = {**thread_context, "_custom_tool_owner_ids": owner_ids}
        active_state = thread_context.get("custom_tool_state") if isinstance(thread_context.get("custom_tool_state"), dict) else {}
        run_trace.snapshot(
            "resolved_runtime_context",
            {
                "application": app_ctx,
                "thread_id": thread_id,
                "turn_input": text,
                "thread_context": thread_context,
                "active_custom_tool_state": active_state,
            },
            section="request",
        )
        action_id = str(interaction_response.get("action_id") or "").strip()
        interaction_id = str(interaction_response.get("interaction_id") or "").strip()
        expected_revision = interaction_response.get("expected_revision")
        expected_revision = expected_revision if isinstance(expected_revision, int) else None
        routing_thread_context = dict(thread_context)
        if interaction_response and text:
            routing_thread_context["_current_ui_action"] = {
                "interaction_id": interaction_id,
                "action_id": action_id,
            }
        dispatch_plan = assistant_dispatch_planner.plan_turn(
            text=text,
            attachments=[],
            thread_context=routing_thread_context,
            application_context=app_ctx,
            interaction_response=interaction_response if direct_interaction else None,
        )
        shortcut = dispatch_plan.get("shortcut") if isinstance(dispatch_plan.get("shortcut"), dict) else {}
        display_text = text or str(interaction_response.get("label") or action_id or "确认当前内容").strip()
        turn_id = runtime_conversation_service.create_turn(
            thread_id=thread_id,
            user_input_text=display_text,
            input_payload=_to_json_safe({
                **payload,
                "text": text,
                "application_name": application_name,
            }),
        )
        if not requested_thread_id:
            _schedule_thread_title(
                thread_id=thread_id,
                user_text=text,
                expected_title=initial_thread_title,
            )
        _submit_finance_cc_shadow(
            thread_id=thread_id,
            turn_id=turn_id,
            owner_id=owner_ids[0] if owner_ids else "",
            user_text=_resolved_dispatch_question(dispatch_plan, text),
            dispatch_plan=dispatch_plan,
            application_context=app_ctx,
            thread_context=thread_context,
        )
        parsed = _parse_chat_command(text)
        owner_id = owner_ids[0] if owner_ids else ""
        routed_outside_custom_tool = False
        if direct_interaction:
            if not active_state:
                raise ValueError("当前没有可继续的自定义工具流程。")
            shortcut_handler = str(shortcut.get("handler") or "").strip()
            if shortcut_handler == "custom_tool.action" and action_id == "custom_tool.activate_draft":
                result = custom_tool_agent_service.continue_flow_action(
                    action_id,
                    state=active_state,
                    expected_revision=expected_revision,
                    owner_id=owner_id,
                    turn_id=turn_id,
                    event_sink=event_sink,
                )
            elif shortcut_handler == "custom_tool.action" and action_id == "custom_tool.confirm_design":
                if isinstance(active_state.get("design_contract"), dict) and active_state.get("design_contract"):
                    result = custom_tool_agent_service.continue_flow_action(
                        action_id,
                        state=active_state,
                        expected_revision=expected_revision,
                        owner_id=owner_id,
                        turn_id=turn_id,
                        event_sink=event_sink,
                    )
                else:
                    result = custom_tool_agent_service.handle_turn(
                        "我确认当前需求理解，请继续形成设计方案。",
                        state=active_state,
                        ui_action=interaction_response,
                        owner_id=owner_id,
                        thread_id=thread_id,
                        turn_id=turn_id,
                        event_sink=event_sink,
                    )
            elif shortcut_handler == "custom_tool.action" and custom_tool_agent_service.finance_cc_enabled:
                result = custom_tool_agent_service.handle_turn(
                    text or str(interaction_response.get("label") or action_id or "确认当前内容").strip(),
                    state=active_state,
                    ui_action=interaction_response,
                    owner_id=owner_id,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    event_sink=event_sink,
                )
            elif shortcut_handler == "custom_tool.action":
                result = custom_tool_agent_service.continue_flow_action(
                    action_id,
                    state=active_state,
                    expected_revision=expected_revision,
                    owner_id=owner_id,
                    turn_id=turn_id,
                    event_sink=event_sink,
                )
            else:
                raise CustomToolError(f"unsupported custom tool shortcut: {shortcut_handler or '-'}")
        elif parsed.get("kind") == "slash" and str(parsed.get("command") or "").lower() == "/custom_tool":
            args = parsed.get("args") or []
            sub_action = str(args[0] if args else "").strip().lower()
            rest_args = args[1:] if args else []
            if sub_action == "create":
                result = custom_tool_agent_service.start_create(
                    _custom_tool_arg_text(rest_args),
                    owner_id=owner_id,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    event_sink=event_sink,
                )
            elif sub_action == "edit":
                edit_text = _custom_tool_arg_text(rest_args)
                if not edit_text:
                    raise ValueError("请使用 /custom_tool edit <修改要求>")
                if not active_state:
                    active_state = {"owner_id": owner_id, "requirement_text": ""}
                result = custom_tool_agent_service.start_create(
                    edit_text,
                    state=active_state,
                    owner_id=owner_id,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    event_sink=event_sink,
                )
            else:
                raise ValueError("流式 custom_tool 仅支持 create/edit 和进行中流程的继续。")
        elif active_state:
            if str(dispatch_plan.get("entry") or "").strip() != "custom_tool_flow":
                routed_outside_custom_tool = True
                result = _build_chat_dispatch_payload(
                    text,
                    application_context=app_ctx,
                    thread_context=thread_context,
                    attachments=[],
                    thread_id=thread_id,
                    turn_id=turn_id,
                    owner_id=owner_id,
                    precomputed_plan=dispatch_plan,
                )
            else:
                result = custom_tool_agent_service.handle_turn(
                    _resolved_dispatch_question(dispatch_plan, text),
                    state=active_state,
                    ui_action=interaction_response if interaction_response else None,
                    owner_id=owner_id,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    event_sink=event_sink,
                )
                app.logger.info(
                    "custom_tool turn routed thread_id=%s turn_id=%s action=%s protocol=%s",
                    thread_id,
                    turn_id,
                    str((result.get("tool_turn") or {}).get("action") or ""),
                    str((result.get("tool_turn") or {}).get("version") or ""),
                )
        elif str(dispatch_plan.get("entry") or "").strip() == "custom_tool_flow":
            result = custom_tool_agent_service.start_create(
                text,
                owner_id=owner_id,
                thread_id=thread_id,
                turn_id=turn_id,
                event_sink=event_sink,
            )
        else:
            raise ValueError("当前没有可流式执行的 custom_tool 流程。")

        if not routed_outside_custom_tool:
            result.setdefault("mode", "custom_tool_flow")
        if dispatch_plan:
            result["dispatch_plan"] = dispatch_plan
        if routed_outside_custom_tool:
            result.setdefault("events", [])
        else:
            result["events"] = raw_events
            result["event_summary"] = {
                "total": raw_event_count,
                "persisted": len(raw_events),
                "by_type": dict(sorted(event_type_counts.items())),
            }
        result["diagnostic_trace"] = {
            "run_id": builder.run_id,
            "format": "timed_business_sections_text_v1",
            "path": str(run_trace.path),
        }
        if routed_outside_custom_tool:
            result["thread_context_patch"] = (
                result.get("thread_context_patch")
                if isinstance(result.get("thread_context_patch"), dict)
                else {}
            )
        else:
            result["thread_context_patch"] = _custom_tool_context_patch(result.get("state") if isinstance(result.get("state"), dict) else {})
        if result["thread_context_patch"]:
            runtime_conversation_service.update_thread_context(thread_id=thread_id, patch=result["thread_context_patch"])
        assistant_message = str(result.get("message") or "已处理。").strip()
        final_events = (
            [dict(item) for item in result.get("surface_blocks") or [] if isinstance(item, dict)]
            if routed_outside_custom_tool
            else _custom_tool_result_blocks(result, builder)
        )
        result["surface_blocks"] = final_events
        runtime_conversation_service.complete_turn(
            thread_id=thread_id,
            turn_id=turn_id,
            assistant_output_text=assistant_message,
            output_payload=_to_json_safe(result),
            model_name=str(result.get("model_name") or "").strip(),
            token_usage=result.get("llm_usage") if isinstance(result.get("llm_usage"), dict) else None,
        )
        run_trace.finish(result)
        for block in final_events:
            emit(block)
        emit({
            "event": "done",
            "run_id": builder.run_id,
            "thread_id": thread_id,
            "turn_id": turn_id,
            "message": "任务已完成",
            "result": result,
        })
    except ContextResolutionError as exc:
        technical_detail = str(exc.technical_detail or exc)
        app.logger.warning(
            "context resolution failed after correction thread_id=%s detail=%s raw_response=%r",
            thread_id,
            technical_detail,
            str(exc.raw_response or "")[:2000],
        )
        user_message = str(exc.user_message or "").strip()
        if thread_id is not None and turn_id is None:
            turn_id = runtime_conversation_service.create_turn(
                thread_id=thread_id,
                user_input_text=text,
                input_payload=_to_json_safe({
                    **payload,
                    "text": text,
                    "application_name": application_name,
                }),
            )
        block = builder.make_block(
            block_id="context_resolution_message",
            block_type="markdown",
            mode="replace",
            content=user_message,
            stage="conversation",
            data={"role": "assistant_message"},
        )
        result = {
            "mode": "conversation",
            "message": user_message,
            "surface_blocks": [block],
            "diagnostic_trace": {
                "run_id": builder.run_id,
                "format": "timed_business_sections_text_v1",
                "path": str(run_trace.path),
            },
        }
        run_trace.finish(result, error=technical_detail)
        if thread_id is not None and turn_id is not None:
            runtime_conversation_service.complete_turn(
                thread_id=thread_id,
                turn_id=turn_id,
                assistant_output_text=user_message,
                output_payload=_to_json_safe(result),
            )
        emit(block)
        emit({
            "event": "done",
            "run_id": builder.run_id,
            "thread_id": thread_id,
            "turn_id": turn_id,
            "message": user_message,
            "result": result,
        })
    except Exception as exc:
        run_trace.finish({}, error=str(exc))
        if thread_id is not None and turn_id is not None:
            runtime_conversation_service.complete_turn(
                thread_id=thread_id,
                turn_id=turn_id,
                assistant_output_text="本轮处理失败，已保留上一轮状态。",
                output_payload={"ok": False, "error": str(exc), "run_id": builder.run_id},
                status="failed",
            )
        emit({
            "event": "error",
            "run_id": builder.run_id,
            "message": str(exc),
        })


def _run_asset_invocation_stream_payload(payload: dict, *, emit) -> None:
    run_id = str(payload.get("run_id") or "").strip()
    text = str(payload.get("text") or "").strip()
    selected_asset = payload.get("selected_asset") if isinstance(payload.get("selected_asset"), dict) else {}
    attachment_ids = payload.get("attachment_ids") if isinstance(payload.get("attachment_ids"), list) else []
    application_name = str(payload.get("application_name") or "investment_workbench").strip() or "investment_workbench"
    guest_identity = payload.get("guest_identity") if isinstance(payload.get("guest_identity"), dict) else {}
    thread_id = None
    turn_id = None
    try:
        attachments = attachment_service.list_attachments(attachment_ids)
        app_ctx = application_runtime_service.get_application_context(application_name)
        requested_thread_id = (
            int(payload.get("thread_id"))
            if str(payload.get("thread_id") or "").strip().isdigit()
            else UserSessionService._safe_int(str(payload.get("cookie_thread_id") or ""))
        )
        thread_id = runtime_conversation_service.ensure_thread(
            thread_id=requested_thread_id,
            title=str(app_ctx.get("display_name") or application_name),
            owner_type="user",
            owner_id=str(guest_identity.get("user_id") or ""),
            context_summary=f"{application_name} 会话",
        )
        thread_context = runtime_conversation_service.get_thread_context(thread_id=thread_id)
        context_window = runtime_conversation_service.get_context_window(thread_id=thread_id, max_rounds=5)
        if context_window:
            thread_context = {**thread_context, "context_window": context_window}
        thread_context = {
            **thread_context,
            "_custom_tool_owner_ids": _custom_tool_owner_ids(
                thread_context=thread_context,
                user_id=str(guest_identity.get("user_id") or ""),
                thread_id=thread_id,
            ),
        }
        display_text = text or f"${str(selected_asset.get('name') or '').strip()}"
        turn_id = runtime_conversation_service.create_turn(
            thread_id=thread_id,
            user_input_text=display_text,
            input_payload=_to_json_safe({**payload, "attachments": attachments}),
        )
        if not requested_thread_id:
            _schedule_thread_title(
                thread_id=thread_id,
                user_text=display_text,
                expected_title=str(app_ctx.get("display_name") or application_name),
            )
        invocation = asset_invocation_service.plan(
            text=text,
            selected_asset=selected_asset,
            attachments=attachments,
            thread_context=thread_context,
            owner_ids=_custom_tool_owner_ids(thread_context=thread_context, thread_id=thread_id),
        )
        if not invocation:
            raise AssetInvocationError("没有选择可调用的 Tool 或 Skill")
        if invocation.get("status") == "ready":
            emit(_asset_invocation_preview_block(invocation))
        result = _execute_asset_invocation_payload(
            invocation,
            text=text,
            application_context=app_ctx,
            thread_context=thread_context,
            thread_id=thread_id,
            turn_id=turn_id,
        )
        assistant_message = str(result.get("message") or "已处理。").strip()
        runtime_conversation_service.complete_turn(
            thread_id=thread_id,
            turn_id=turn_id,
            assistant_output_text=assistant_message,
            output_payload=_to_json_safe(result),
            model_name=str(result.get("model_name") or "").strip(),
            token_usage=result.get("llm_usage") if isinstance(result.get("llm_usage"), dict) else None,
        )
        for block in result.get("surface_blocks") or []:
            if isinstance(block, dict) and str(block.get("block_id") or "") != "asset_invocation_preview":
                emit({"event": "block", **block})
        emit({
            "event": "done",
            "run_id": run_id,
            "thread_id": thread_id,
            "turn_id": turn_id,
            "message": "任务已完成",
            "result": result,
        })
    except Exception as exc:
        if thread_id is not None and turn_id is not None:
            runtime_conversation_service.complete_turn(
                thread_id=thread_id,
                turn_id=turn_id,
                assistant_output_text="本轮处理失败。",
                output_payload={"ok": False, "error": str(exc), "run_id": run_id},
                status="failed",
            )
        emit({"event": "error", "run_id": run_id, "message": str(exc)})


def _build_chat_dispatch_payload(
    text: str,
    *,
    application_context: dict | None = None,
    thread_context: dict | None = None,
    attachments: list[dict] | None = None,
    thread_id: int | None = None,
    turn_id: int | None = None,
    owner_id: str = "",
    precomputed_plan: dict | None = None,
) -> dict:
    parsed = _parse_chat_command(text)
    raw = parsed.get("raw") or ""
    application_context = application_context if isinstance(application_context, dict) else {}
    thread_context = thread_context if isinstance(thread_context, dict) else {}
    attachments = attachments if isinstance(attachments, list) else []
    default_agent = application_context.get("default_agent") if isinstance(application_context.get("default_agent"), dict) else {}
    if parsed["kind"] == "free_chat":
        plan = (
            precomputed_plan
            if isinstance(precomputed_plan, dict) and precomputed_plan
            else assistant_dispatch_planner.plan_turn(
                text=raw,
                attachments=attachments,
                thread_context=thread_context,
                application_context=application_context,
            )
        )
        planning_task_state = _finalize_planning_task_state(
            plan.get("task_state") if isinstance(plan.get("task_state"), dict) else {},
            turn_id=turn_id,
        )

        def _attach_planning_state(payload: dict) -> dict:
            if not planning_task_state or not isinstance(payload, dict):
                return payload
            runtime_task_state = payload.get("task_state") if isinstance(payload.get("task_state"), dict) else {}
            payload["planning_task_state"] = planning_task_state
            if runtime_task_state:
                payload["runtime_task_state"] = runtime_task_state
            payload["task_state"] = _merge_chat_task_states(
                planning_task_state,
                runtime_task_state,
            )
            return payload

        plan_entry = str(plan.get("entry") or "").strip()
        continuity_patch_preview = plan.get("thread_context_patch_preview") if isinstance(plan.get("thread_context_patch_preview"), dict) else {}
        active_skill_name = str((plan.get("work_context") or {}).get("thread_active_skill_name") or (plan.get("work_context") or {}).get("active_skill_name") or "").strip()
        active_skill_canonical_name = str((plan.get("work_context") or {}).get("thread_active_skill_canonical_name") or (plan.get("work_context") or {}).get("active_skill_canonical_name") or "").strip()
        execution_plan = plan.get("execution_plan") if isinstance(plan.get("execution_plan"), dict) else {}
        if plan_entry == "custom_tool_flow":
            active_state = thread_context.get("custom_tool_state") if isinstance(thread_context.get("custom_tool_state"), dict) else {}
            owner_ids = _custom_tool_owner_ids(thread_context=thread_context, thread_id=thread_id)
            if active_state:
                result = custom_tool_agent_service.handle_turn(
                    _resolved_dispatch_question(plan, raw),
                    state=active_state,
                    owner_id=owner_ids[0] if owner_ids else "",
                    thread_id=thread_id,
                    turn_id=turn_id,
                )
            else:
                result = custom_tool_agent_service.start_create(
                    raw,
                    owner_id=owner_ids[0] if owner_ids else "",
                    thread_id=thread_id,
                    turn_id=turn_id,
                )
            result.setdefault("mode", "custom_tool_flow")
            result["dispatch_plan"] = plan
            result["thread_context_patch"] = _merge_thread_context_patches(
                continuity_patch_preview,
                _custom_tool_context_patch(result.get("state") if isinstance(result.get("state"), dict) else {}),
            )
            return _attach_planning_state(_apply_application_workspace_orchestration(result, application_context))
        if plan_entry == "vision_intake":
            plan_target = plan.get("target") if isinstance(plan.get("target"), dict) else {}
            if attachments:
                result = vision_intake_service.analyze_for_assistant(
                    attachments=attachments,
                    user_text=raw,
                    application_context=application_context,
                    thread_context=thread_context,
                )
                result["thread_context_patch"] = _merge_thread_context_patches(
                    continuity_patch_preview,
                    result.get("thread_context_patch") if isinstance(result.get("thread_context_patch"), dict) else {},
                )
                return _attach_planning_state(result)
            recent_attachment_ids = _recent_image_attachment_ids(thread_context)
            recent_attachments = attachment_service.list_attachments(recent_attachment_ids)
            if recent_attachments:
                result = vision_intake_service.analyze_for_assistant(
                    attachments=recent_attachments,
                    user_text=raw,
                    application_context=application_context,
                    thread_context=thread_context,
                )
                result["thread_context_patch"] = _merge_thread_context_patches(
                    continuity_patch_preview,
                    result.get("thread_context_patch") if isinstance(result.get("thread_context_patch"), dict) else {},
                )
                return _attach_planning_state(result)
            return _attach_planning_state({
                "mode": "vision_reupload_required",
                "message": "当前找不到刚才那张图片的可用附件，请重新上传后再继续分析。",
                "items": [
                    {
                        "name": "vision_reupload_required",
                        "display_name": "图片上下文已失效",
                        "status": "needs_reupload",
                    }
                ],
                "dispatch_plan": plan,
                "thread_context_patch": _merge_thread_context_patches(
                    continuity_patch_preview,
                    {
                        "last_image_attachment_ids": None,
                        "last_image_type": None,
                        "last_image_summary": None,
                        "last_visual_subjects": None,
                    },
                ),
            })
        if financial_qa_cc_service.accepts(
            dispatch_plan=plan,
            attachments=attachments,
        ):
            result = financial_qa_cc_service.answer(
                thread_id=thread_id or "",
                turn_id=turn_id or "",
                owner_id=owner_id,
                user_text=raw,
                dispatch_plan=plan,
                application_context=application_context,
            )
            result = _apply_application_workspace_orchestration(
                result,
                application_context,
            )
            result["dispatch_plan"] = plan
            result["thread_context_patch"] = _merge_thread_context_patches(
                continuity_patch_preview,
                {},
            )
            return _attach_planning_state(result)
        if plan_entry == "skill_refine":
            if not (active_skill_name or active_skill_canonical_name):
                plan_entry = "agent_route"
            else:
                canonical_skill_name = _canonical_skill_name(active_skill_canonical_name or active_skill_name)
                resolved_source_skill_name = canonical_skill_name
                target_skill_name = canonical_skill_name
                selected_tools, selected_skills = _resolve_blueprint_assets(application_context=application_context)
                bundle = skill_blueprint_service.refine_bundle(
                    source_skill_name=resolved_source_skill_name,
                    target_skill_name=target_skill_name,
                    refinement_text=raw,
                    selected_tools=selected_tools,
                    selected_skills=selected_skills,
                    application_name=str(application_context.get("application_name") or "").strip(),
                    agent_name=str((application_context.get("default_agent") or {}).get("agent_name") or "").strip(),
                )
                skill_studio_service.save_skill_bundle(
                    skill_name=target_skill_name,
                    skill_md_text=bundle["files"]["skill_md_text"],
                    skill_config_text=bundle["files"]["skill_config_text"],
                    output_schema_text=bundle["files"]["output_schema_text"],
                )
                bundle = skill_studio_service.load_skill_bundle(target_skill_name)
                result = _apply_application_workspace_orchestration({
                    "mode": "skill_refined",
                    "message": f"已直接更新 {canonical_skill_name}。",
                    "skill_name": target_skill_name,
                    "bundle": bundle,
                    "items": [_build_skill_bundle_item(target_skill_name, bundle)],
                    "workspace": {
                        "type": "skill_editor",
                        "title": f"Skill: {target_skill_name}",
                        "url": _with_script_root(f"/skills/studio/{quote(target_skill_name)}"),
                    },
                }, application_context)
                result["dispatch_plan"] = plan
                result["thread_context_patch"] = _merge_thread_context_patches(
                    continuity_patch_preview,
                    _build_active_skill_context(target_skill_name),
                )
                return _attach_planning_state(result)
        if plan_entry == "skill_run":
            target = plan.get("target") if isinstance(plan.get("target"), dict) else {}
            skill_name = str(target.get("name") or active_skill_canonical_name or active_skill_name).strip()
            if skill_name:
                resolved_skill_name = _resolve_working_skill_name(skill_name)
                canonical_skill_name = _canonical_skill_name(skill_name)
                job = _submit_generic_skill_job(
                    resolved_skill_name,
                    {
                        "input_payload": {
                            "question": raw,
                        },
                        "source_type": "assistant_skill_run",
                        "application_name": str(application_context.get("application_name") or "").strip(),
                        "agent_name": str((application_context.get("default_agent") or {}).get("agent_name") or "").strip(),
                        "agent_runtime_profile": (
                            (application_context.get("default_agent") or {}).get("runtime_profile")
                            if isinstance((application_context.get("default_agent") or {}).get("runtime_profile"), dict)
                            else {}
                        ),
                    },
                )
                result = _apply_application_workspace_orchestration({
                    "mode": "run_skill_submitted",
                    "message": (
                        f"已按业务请求提交 skill：{canonical_skill_name}"
                        + (f"（当前执行工作草稿：{resolved_skill_name}）" if resolved_skill_name != canonical_skill_name else "")
                    ),
                    "skill_name": resolved_skill_name,
                    "job": job,
                    "items": [_build_task_job_item(job)],
                    "workspace": {
                        "type": "task_detail",
                        "title": f"Task: {str(job.get('job_id') or '').strip()}",
                        "url": _with_script_root(f"/tasks/{quote(str(job.get('job_id') or '').strip())}/view"),
                    },
                }, application_context)
                result["dispatch_plan"] = plan
                result["execution_plan"] = execution_plan
                result["thread_context_patch"] = _merge_thread_context_patches(
                    continuity_patch_preview,
                    _build_active_skill_context(resolved_skill_name),
                )
                return _attach_planning_state(result)
        if plan_entry in {"tool_plan_run", "planned_run"}:
            result = tool_plan_runtime_service.execute_for_assistant(
                execution_plan=execution_plan,
                user_text=raw,
                application_context=application_context,
                thread_context=thread_context,
                thread_id=thread_id,
                turn_id=turn_id,
            )
            result = _apply_application_workspace_orchestration(result, application_context)
            result["dispatch_plan"] = plan
            result["execution_plan"] = execution_plan
            result["thread_context_patch"] = _merge_thread_context_patches(
                continuity_patch_preview,
                result.get("thread_context_patch") if isinstance(result.get("thread_context_patch"), dict) else {},
            )
            return _attach_planning_state(result)
        if plan_entry == "catalog_browse":
            mode = str(plan.get("browse_mode") or "").strip()
            if mode == "skills_catalog":
                items = skill_studio_service.list_skills()
                result = _apply_application_workspace_orchestration({
                    "mode": "skills_catalog",
                    "message": f"当前共有 {len(items)} 个 skills。",
                    "items": items,
                    "workspace": {
                        "type": "skills_catalog",
                        "title": "Skill Studio",
                        "url": _with_script_root("/skills/studio"),
                    },
                }, application_context)
                result["dispatch_plan"] = plan
                result["thread_context_patch"] = _merge_thread_context_patches(
                    continuity_patch_preview,
                    result.get("thread_context_patch") if isinstance(result.get("thread_context_patch"), dict) else {},
                )
                return _attach_planning_state(result)
            if mode == "tools_catalog":
                items = tool_studio_service.list_tools()
                result = _apply_application_workspace_orchestration({
                    "mode": "tools_catalog",
                    "message": f"当前共有 {len(items)} 个 tools。",
                    "items": items,
                    "workspace": {
                        "type": "tools_catalog",
                        "title": "Tools Catalog",
                        "url": _with_script_root("/tools"),
                    },
                }, application_context)
                result["dispatch_plan"] = plan
                result["thread_context_patch"] = _merge_thread_context_patches(
                    continuity_patch_preview,
                    result.get("thread_context_patch") if isinstance(result.get("thread_context_patch"), dict) else {},
                )
                return _attach_planning_state(result)
            if mode == "applications_catalog":
                items = application_studio_service.list_applications()
                result = _apply_application_workspace_orchestration({
                    "mode": "applications_catalog",
                    "message": f"当前共有 {len(items)} 个 applications。",
                    "items": items,
                    "workspace": {
                        "type": "applications_catalog",
                        "title": "Application Studio",
                        "url": _with_script_root("/applications/studio"),
                    },
                }, application_context)
                result["dispatch_plan"] = plan
                result["thread_context_patch"] = _merge_thread_context_patches(
                    continuity_patch_preview,
                    result.get("thread_context_patch") if isinstance(result.get("thread_context_patch"), dict) else {},
                )
                return _attach_planning_state(result)
            if mode == "agents_catalog":
                items = agent_studio_service.list_agents()
                result = _apply_application_workspace_orchestration({
                    "mode": "agents_catalog",
                    "message": f"当前共有 {len(items)} 个 agents。",
                    "items": items,
                    "workspace": {
                        "type": "agents_catalog",
                        "title": "Agent Studio",
                        "url": _with_script_root("/agents/studio"),
                    },
                }, application_context)
                result["dispatch_plan"] = plan
                result["thread_context_patch"] = _merge_thread_context_patches(
                    continuity_patch_preview,
                    result.get("thread_context_patch") if isinstance(result.get("thread_context_patch"), dict) else {},
                )
                return _attach_planning_state(result)

        if plan_entry == "asset_open":
            asset_type = str(plan.get("asset_type") or "").strip()
            asset_name = str(plan.get("asset_name") or "").strip()
            if asset_type == "skill" and asset_name:
                resolved_skill_name = _resolve_working_skill_name(asset_name)
                canonical_skill_name = _canonical_skill_name(asset_name)
                bundle = skill_studio_service.load_skill_bundle(resolved_skill_name)
                result = {
                    "mode": "skill_detail",
                    "message": (
                        f"已打开 skill：{canonical_skill_name}"
                        + (f"（当前工作草稿：{resolved_skill_name}）" if resolved_skill_name != canonical_skill_name else "")
                    ),
                    "bundle": bundle,
                    "items": [_build_skill_bundle_item(resolved_skill_name, bundle)],
                    "workspace": {
                        "type": "skill_editor",
                        "title": f"Skill: {resolved_skill_name}",
                        "url": _with_script_root(f"/skills/studio/{quote(resolved_skill_name)}"),
                    },
                }
                result["dispatch_plan"] = plan
                result["thread_context_patch"] = _merge_thread_context_patches(
                    continuity_patch_preview,
                    _build_active_skill_context(resolved_skill_name),
                )
                return _attach_planning_state(result)
            if asset_type == "tool" and asset_name:
                bundle = tool_studio_service.load_tool_bundle(asset_name)
                return _attach_planning_state({
                    "mode": "tool_detail",
                    "message": f"已打开 tool：{asset_name}",
                    "bundle": bundle,
                    "items": [_build_tool_bundle_item(asset_name, bundle)],
                    "workspace": {
                        "type": "tool_editor",
                        "title": f"Tool: {asset_name}",
                        "url": _with_script_root(f"/tools/studio/{quote(asset_name)}"),
                    },
                    "dispatch_plan": plan,
                    "thread_context_patch": _merge_thread_context_patches(
                        continuity_patch_preview,
                        {},
                    ),
                })
            if asset_type == "application" and asset_name:
                bundle = application_studio_service.load_application_bundle(asset_name)
                application_context = _ui_application_context(asset_name)
                result = {
                    "mode": "application_detail",
                    "message": f"已切换 application：{asset_name}",
                    "bundle": bundle,
                    "application_context": application_context,
                    "items": [_build_application_bundle_item(asset_name, bundle)],
                    "workspace": {
                        "type": str((application_context.get("workspace") or {}).get("type") or "workspace"),
                        "title": str((application_context.get("workspace") or {}).get("title") or f"Application: {asset_name}"),
                        "url": str((application_context.get("workspace") or {}).get("url") or _with_script_root("/router/studio")),
                    },
                }
                result["dispatch_plan"] = plan
                result["thread_context_patch"] = _merge_thread_context_patches(
                    continuity_patch_preview,
                    {
                        "active_skill_name": None,
                        "active_skill_canonical_name": None,
                        "active_skill_is_draft": None,
                    },
                )
                return _attach_planning_state(result)
            if asset_type == "agent" and asset_name:
                bundle = agent_studio_service.load_agent_bundle(asset_name)
                result = _apply_application_workspace_orchestration({
                    "mode": "agent_detail",
                    "message": f"已打开 agent：{asset_name}",
                    "agent_name": asset_name,
                    "bundle": bundle,
                    "items": [_build_agent_bundle_item(asset_name, bundle)],
                    "workspace": {
                        "type": "agent_editor",
                        "title": f"Agent: {asset_name}",
                        "url": _with_script_root(f"/agents/studio/{quote(asset_name)}"),
                    },
                }, application_context)
                result["dispatch_plan"] = plan
                result["thread_context_patch"] = _merge_thread_context_patches(
                    continuity_patch_preview,
                    result.get("thread_context_patch") if isinstance(result.get("thread_context_patch"), dict) else {},
                )
                return _attach_planning_state(result)

        selected_agent_name = str(plan.get("selected_agent") or "").strip()
        selected_agent = next(
            (
                item for item in (application_context.get("available_agents") or [])
                if isinstance(item, dict) and str(item.get("agent_name") or "").strip() == selected_agent_name
            ),
            {},
        )
        selected_agent_config = selected_agent.get("config") if isinstance(selected_agent.get("config"), dict) else {}
        static_response = str(selected_agent_config.get("static_response") or "").strip()
        if static_response:
            result = _apply_application_workspace_orchestration({
                "mode": "agent_static_response",
                "message": static_response,
                "items": [],
                "dispatch_plan": plan,
                "thread_context_patch": _merge_thread_context_patches(continuity_patch_preview, {}),
            }, application_context)
            return _attach_planning_state(result)

        selected_skills = selected_agent.get("skills") if isinstance(selected_agent.get("skills"), list) else []
        selected_tools = selected_agent.get("tools") if isinstance(selected_agent.get("tools"), list) else []
        if selected_agent and not selected_skills and not selected_tools:
            direct_answer = agent_direct_response_service.answer(
                user_text=raw,
                agent=selected_agent,
            )
            result = _apply_application_workspace_orchestration({
                "mode": "agent_direct_response",
                "message": direct_answer["message"],
                "items": [],
                "agent_llm_usage": direct_answer.get("llm_usage") or {},
                "dispatch_plan": plan,
                "thread_context_patch": _merge_thread_context_patches(continuity_patch_preview, {}),
            }, application_context)
            return _attach_planning_state(result)

        preview = agent_execution_service.preview_route(
            user_text=raw,
            application_name=str(application_context.get("application_name") or "investment_workbench").strip() or "investment_workbench",
            context={
                **thread_context,
                "selected_agent": str(plan.get("selected_agent") or "").strip(),
            },
            execution_profile="real",
        )
        route_snapshot = preview.get("route_snapshot") if isinstance(preview.get("route_snapshot"), dict) else {}
        route = route_snapshot.get("route") if isinstance(route_snapshot.get("route"), dict) else {}
        route_type = str(route.get("route_type") or "").strip()
        reply_lines = [f"已识别为自然语言请求，当前 agent = {str(plan.get('selected_agent') or '').strip() or '-'}，route_type = {route_type or '-'}。"]
        selected_skill = str(route.get("selected_skill") or "").strip()
        if selected_skill:
            reply_lines.append(f"建议 skill: {selected_skill}")
        if route_type == "clarify":
            clarification = route_snapshot.get("clarification_state") or {}
            reply_lines.append(f"需要澄清: {json.dumps(clarification, ensure_ascii=False)}")
        result = _apply_application_workspace_orchestration({
            "mode": "free_chat",
            "message": "\n".join(reply_lines),
            "route_snapshot": route_snapshot,
            "items": [
                _build_route_item(
                    application_name=str(application_context.get("application_name") or "").strip(),
                    agent_name=selected_agent_name or str(default_agent.get("agent_name") or "").strip(),
                    route_snapshot=route_snapshot,
                )
            ],
            "workspace": {
                "type": "router",
                "title": "任务路由",
                "url": _with_script_root("/router/studio"),
            },
        }, application_context)
        result["dispatch_plan"] = plan
        result["thread_context_patch"] = _merge_thread_context_patches(
            continuity_patch_preview,
            result.get("thread_context_patch") if isinstance(result.get("thread_context_patch"), dict) else {},
        )
        return _attach_planning_state(result)

    command = str(parsed.get("command") or "")
    args = parsed.get("args") or []
    normalized_command = system_command_service.normalize(command=command, args=args)
    command_action = str(normalized_command.get("action") or "").strip()
    target_name = str(normalized_command.get("target_name") or "").strip()

    if command_action == "custom_tool":
        sub_action = str(args[0] if args else "").strip().lower()
        rest_args = args[1:] if args else []
        owner_ids = _custom_tool_owner_ids(thread_context=thread_context, thread_id=thread_id)
        owner_id = owner_ids[0] if owner_ids else ""
        active_state = thread_context.get("custom_tool_state") if isinstance(thread_context.get("custom_tool_state"), dict) else {}
        if sub_action == "create":
            result = custom_tool_agent_service.start_create(
                _custom_tool_arg_text(rest_args),
                owner_id=owner_id,
                thread_id=thread_id,
                turn_id=turn_id,
            )
        elif sub_action == "edit":
            edit_text = _custom_tool_arg_text(rest_args)
            if not edit_text:
                raise ValueError("请使用 /custom_tool edit <修改要求>")
            if not active_state:
                active_state = {"owner_id": owner_id, "requirement_text": ""}
            result = custom_tool_agent_service.start_create(
                edit_text,
                state=active_state,
                owner_id=owner_id,
                thread_id=thread_id,
                turn_id=turn_id,
            )
        elif sub_action == "call":
            tool_name, call_args = _parse_custom_tool_json_arg(_custom_tool_arg_text(rest_args))
            if not tool_name:
                raise ValueError("请使用 /custom_tool call <tool_name> {json_args}")
            call_result = tool_plan_runtime_service.runtime_execution_service.execute_tool(
                tool_name=tool_name,
                args={
                    **call_args,
                    "_runtime": {
                        "thread_id": thread_id,
                        "turn_id": turn_id,
                        "thread_type": "chat",
                        "task_type": "custom_tool_call",
                        "goal": raw,
                        "assigned_agent": "custom_tool_runtime",
                        "source_type": "assistant_custom_tool_call",
                    },
                },
                executor=lambda args: custom_tool_agent_service.call(
                    tool_name,
                    args,
                    owner_ids=owner_ids,
                ),
            )
            result = {
                "mode": "custom_tool_call",
                "message": f"自定义工具 {tool_name} 调用{'成功' if call_result.get('ok') else '失败'}。",
                "tool_name": tool_name,
                "result": call_result,
                "items": [{
                    "name": tool_name,
                    "display_name": tool_name,
                    "status": "completed" if call_result.get("ok") else "failed",
                    "data": call_result.get("data"),
                    "error": call_result.get("error"),
                }],
                "thread_context_patch": _custom_tool_context_patch(active_state),
            }
        elif sub_action == "commit":
            tool_name = str(rest_args[0] if rest_args else (active_state or {}).get("tool_name") or "").strip()
            if not tool_name:
                raise ValueError("请使用 /custom_tool commit <tool_name>")
            bundle = custom_tool_agent_service.commit(tool_name, owner_ids=owner_ids)
            result = {
                "mode": "custom_tool_committed",
                "message": f"自定义工具已发布：{bundle['manifest']['tool_name']}。",
                "tool_name": bundle["manifest"]["tool_name"],
                "bundle": bundle,
                "items": [{
                    "name": bundle["manifest"]["tool_name"],
                    "display_name": bundle["manifest"].get("display_name"),
                    "status": bundle["manifest"].get("status"),
                    "description": bundle["manifest"].get("description"),
                }],
                "thread_context_patch": _custom_tool_context_patch({}),
            }
        else:
            result = {
                "mode": "custom_tool_help",
                "message": "支持：/custom_tool create <需求>、/custom_tool call <tool_name> {json_args}、/custom_tool edit <修改要求>、/custom_tool commit <tool_name>。",
                "thread_context_patch": _custom_tool_context_patch(active_state),
            }
        result.setdefault("mode", "custom_tool_flow")
        return _apply_application_workspace_orchestration(result, application_context)

    if command_action == "catalog_skills":
        items = skill_studio_service.list_skills()
        return _apply_application_workspace_orchestration({
            "mode": "skills_catalog",
            "message": f"当前共有 {len(items)} 个 skills。",
            "items": items,
            "workspace": {
                "type": "skills_catalog",
                "title": "Skill Studio",
                "url": _with_script_root("/skills/studio"),
            },
        }, application_context)

    if command_action == "catalog_tools":
        items = tool_studio_service.list_tools()
        return _apply_application_workspace_orchestration({
            "mode": "tools_catalog",
            "message": f"当前共有 {len(items)} 个 tools。",
            "items": items,
            "workspace": {
                "type": "tools_catalog",
                "title": "Tools Catalog",
                "url": _with_script_root("/tools"),
            },
        }, application_context)

    if command_action == "catalog_applications":
        items = application_studio_service.list_applications()
        return _apply_application_workspace_orchestration({
            "mode": "applications_catalog",
            "message": f"当前共有 {len(items)} 个 applications。",
            "items": items,
            "workspace": {
                "type": "applications_catalog",
                "title": "Application Studio",
                "url": _with_script_root("/applications/studio"),
            },
        }, application_context)

    if command_action == "catalog_agents":
        items = agent_studio_service.list_agents()
        return _apply_application_workspace_orchestration({
            "mode": "agents_catalog",
            "message": f"当前共有 {len(items)} 个 agents。",
            "items": items,
            "workspace": {
                "type": "agents_catalog",
                "title": "Agent Studio",
                "url": _with_script_root("/agents/studio"),
            },
        }, application_context)

    if command_action == "catalog_tasks":
        items = async_task_service.list_recent_jobs(limit=20)
        return _apply_application_workspace_orchestration({
            "mode": "tasks_catalog",
            "message": f"最近任务 {len(items)} 条。",
            "items": items,
            "workspace": {
                "type": "router",
                "title": "Task Router",
                "url": _with_script_root("/router/studio"),
            },
        }, application_context)

    if command_action == "new_skill":
        skill_name = str(args[0] if args else "").strip()
        if not skill_name:
            raise ValueError("请提供 skill 名称，例如 /new-skill my_new_skill")
        try:
            bundle = skill_studio_service.load_skill_bundle(skill_name)
            created = False
        except Exception:
            bundle = skill_studio_service.build_skill_template_bundle(skill_name)
            skill_studio_service.save_skill_bundle(
                skill_name=skill_name,
                skill_md_text=bundle["files"]["skill_md_text"],
                skill_config_text=bundle["files"]["skill_config_text"],
                output_schema_text=bundle["files"]["output_schema_text"],
            )
            bundle = skill_studio_service.load_skill_bundle(skill_name)
            created = True
        return _apply_application_workspace_orchestration({
            "mode": "new_skill",
            "message": f"{'已创建' if created else '已加载'} skill 模板：{skill_name}",
            "skill_name": skill_name,
            "bundle": bundle,
            "items": [_build_skill_bundle_item(skill_name, bundle)],
            "workspace": {
                "type": "skill_editor",
                "title": f"Skill: {skill_name}",
                "url": _with_script_root(f"/skills/studio/{quote(skill_name)}"),
            },
        }, application_context)

    if command_action == "draft_skill":
        skill_name = str(args[0] if args else "").strip()
        requirement_text = " ".join(args[1:]).strip()
        if not skill_name or not requirement_text:
            raise ValueError("请使用 /draft-skill <skill_name> <需求描述>")
        _ensure_skill_draft_overwritable(skill_name)
        selected_tools, selected_skills = _resolve_blueprint_assets(application_context=application_context)
        bundle = skill_blueprint_service.generate_bundle(
            skill_name=skill_name,
            requirement_text=requirement_text,
            selected_tools=selected_tools,
            selected_skills=selected_skills,
            application_name=str(application_context.get("application_name") or "").strip(),
            agent_name=str((application_context.get("default_agent") or {}).get("agent_name") or "").strip(),
        )
        skill_studio_service.save_skill_bundle(
            skill_name=skill_name,
            skill_md_text=bundle["files"]["skill_md_text"],
            skill_config_text=bundle["files"]["skill_config_text"],
            output_schema_text=bundle["files"]["output_schema_text"],
        )
        bundle = skill_studio_service.load_skill_bundle(skill_name)
        result = _apply_application_workspace_orchestration({
            "mode": "draft_skill",
            "message": f"已根据需求生成 skill 草稿：{skill_name}（默认继承 {len(selected_skills)} 个 skills、{len(selected_tools)} 个 tools）",
            "skill_name": skill_name,
            "bundle": bundle,
            "items": [_build_skill_bundle_item(skill_name, bundle)],
            "workspace": {
                "type": "skill_editor",
                "title": f"Skill: {skill_name}",
                "url": _with_script_root(f"/skills/studio/{quote(skill_name)}"),
            },
        }, application_context)
        result["thread_context_patch"] = _build_active_skill_context(skill_name)
        return result

    if command_action == "refine_skill":
        source_skill_name = str(args[0] if args else "").strip()
        refinement_text = " ".join(args[1:]).strip()
        if not source_skill_name or not refinement_text:
            raise ValueError("请使用 /refine-skill <已有skill> <优化需求>")
        canonical_skill_name = _canonical_skill_name(source_skill_name)
        resolved_source_skill_name = canonical_skill_name
        target_skill_name = canonical_skill_name
        selected_tools, selected_skills = _resolve_blueprint_assets(application_context=application_context)
        bundle = skill_blueprint_service.refine_bundle(
            source_skill_name=resolved_source_skill_name,
            target_skill_name=target_skill_name,
            refinement_text=refinement_text,
            selected_tools=selected_tools,
            selected_skills=selected_skills,
            application_name=str(application_context.get("application_name") or "").strip(),
            agent_name=str((application_context.get("default_agent") or {}).get("agent_name") or "").strip(),
        )
        skill_studio_service.save_skill_bundle(
            skill_name=target_skill_name,
            skill_md_text=bundle["files"]["skill_md_text"],
            skill_config_text=bundle["files"]["skill_config_text"],
            output_schema_text=bundle["files"]["output_schema_text"],
        )
        bundle = skill_studio_service.load_skill_bundle(target_skill_name)
        result = _apply_application_workspace_orchestration({
            "mode": "skill_refined",
            "message": f"已直接更新 {canonical_skill_name}",
            "skill_name": target_skill_name,
            "bundle": bundle,
            "items": [_build_skill_bundle_item(target_skill_name, bundle)],
            "workspace": {
                "type": "skill_editor",
                "title": f"Skill: {target_skill_name}",
                "url": _with_script_root(f"/skills/studio/{quote(target_skill_name)}"),
            },
        }, application_context)
        result["thread_context_patch"] = _build_active_skill_context(target_skill_name)
        return result

    if command_action == "run_skill":
        skill_name = str(args[0] if args else "").strip()
        requirement_text = " ".join(args[1:]).strip()
        if not skill_name or not requirement_text:
            raise ValueError("请使用 /run-skill <skill_name> <需求描述>")
        resolved_skill_name = _resolve_working_skill_name(skill_name)
        canonical_skill_name = _canonical_skill_name(skill_name)
        job = _submit_generic_skill_job(
            resolved_skill_name,
            {
                "input_payload": {
                    "question": requirement_text,
                },
                "source_type": "assistant_run_skill",
                "application_name": str(application_context.get("application_name") or "").strip(),
                "agent_name": str((application_context.get("default_agent") or {}).get("agent_name") or "").strip(),
                "agent_runtime_profile": (
                    (application_context.get("default_agent") or {}).get("runtime_profile")
                    if isinstance((application_context.get("default_agent") or {}).get("runtime_profile"), dict)
                    else {}
                ),
            },
        )
        result = _apply_application_workspace_orchestration({
            "mode": "run_skill_submitted",
            "message": (
                f"已提交 skill 任务：{canonical_skill_name}"
                + (f"（当前执行工作草稿：{resolved_skill_name}）" if resolved_skill_name != canonical_skill_name else "")
            ),
            "skill_name": resolved_skill_name,
            "job": job,
            "items": [_build_task_job_item(job)],
            "workspace": {
                "type": "task_detail",
                "title": f"Task: {str(job.get('job_id') or '').strip()}",
                "url": _with_script_root(f"/tasks/{quote(str(job.get('job_id') or '').strip())}/view"),
            },
        }, application_context)
        result["thread_context_patch"] = _build_active_skill_context(resolved_skill_name)
        return result

    if command_action == "new_application":
        application_name = str(args[0] if args else "").strip()
        if not application_name:
            raise ValueError("请提供 application 名称，例如 /new-application my_app")
        try:
            bundle = application_studio_service.load_application_bundle(application_name)
            created = False
        except Exception:
            bundle = application_studio_service.build_application_template_bundle(application_name)
            application_studio_service.save_application_bundle(
                application_name=application_name,
                application_md_text=bundle["files"]["application_md_text"],
                application_config_text=bundle["files"]["application_config_text"],
                schema_text=bundle["files"]["schema_text"],
            )
            bundle = application_studio_service.load_application_bundle(application_name)
            created = True
        return _apply_application_workspace_orchestration({
            "mode": "new_application",
            "message": f"{'已创建' if created else '已加载'} application 模板：{application_name}",
            "application_name": application_name,
            "bundle": bundle,
            "items": [_build_application_bundle_item(application_name, bundle)],
            "workspace": {
                "type": "application_editor",
                "title": f"Application: {application_name}",
                "url": _with_script_root(f"/applications/studio/{quote(application_name)}"),
            },
        }, application_context)

    if command_action == "open_application":
        application_name = target_name or str(args[0] if args else "").strip()
        if not application_name:
            raise ValueError("请提供 application 名称，例如 /application investment_workbench")
        bundle = application_studio_service.load_application_bundle(application_name)
        application_context = _ui_application_context(application_name)
        result = {
            "mode": "application_detail",
            "message": f"已切换 application：{application_name}",
            "bundle": bundle,
            "application_context": application_context,
            "items": [_build_application_bundle_item(application_name, bundle)],
            "workspace": {
                "type": str((application_context.get("workspace") or {}).get("type") or "workspace"),
                "title": str((application_context.get("workspace") or {}).get("title") or f"Application: {application_name}"),
                "url": str((application_context.get("workspace") or {}).get("url") or _with_script_root("/router/studio")),
            },
        }
        result["thread_context_patch"] = {
            "active_skill_name": None,
            "active_skill_canonical_name": None,
            "active_skill_is_draft": None,
        }
        return result

    if command_action == "new_agent":
        agent_name = str(args[0] if args else "").strip()
        if not agent_name:
            raise ValueError("请提供 agent 名称，例如 /new-agent my_agent")
        try:
            bundle = agent_studio_service.load_agent_bundle(agent_name)
            created = False
        except Exception:
            bundle = agent_studio_service.build_agent_template_bundle(agent_name)
            agent_studio_service.save_agent_bundle(
                agent_name=agent_name,
                soul_md_text=bundle["files"]["soul_md_text"],
                agent_config_text=bundle["files"]["agent_config_text"],
                schema_text=bundle["files"]["schema_text"],
            )
            bundle = agent_studio_service.load_agent_bundle(agent_name)
            created = True
        return _apply_application_workspace_orchestration({
            "mode": "new_agent",
            "message": f"{'已创建' if created else '已加载'} agent 模板：{agent_name}",
            "agent_name": agent_name,
            "bundle": bundle,
            "items": [_build_agent_bundle_item(agent_name, bundle)],
            "workspace": {
                "type": "agent_editor",
                "title": f"Agent: {agent_name}",
                "url": _with_script_root(f"/agents/studio/{quote(agent_name)}"),
            },
        }, application_context)

    if command_action == "open_agent":
        agent_name = target_name or str(args[0] if args else "").strip()
        if not agent_name:
            raise ValueError("请提供 agent 名称，例如 /agent investment_analyst")
        bundle = agent_studio_service.load_agent_bundle(agent_name)
        return _apply_application_workspace_orchestration({
            "mode": "agent_detail",
            "message": f"已打开 agent：{agent_name}",
            "agent_name": agent_name,
            "bundle": bundle,
            "items": [_build_agent_bundle_item(agent_name, bundle)],
            "workspace": {
                "type": "agent_editor",
                "title": f"Agent: {agent_name}",
                "url": _with_script_root(f"/agents/studio/{quote(agent_name)}"),
            },
        }, application_context)

    if command_action == "open_skill":
        skill_name = target_name or str(args[0] if args else "").strip()
        if not skill_name:
            raise ValueError("请提供 skill 名称，例如 /skill stock_deep_dive")
        resolved_skill_name = _resolve_working_skill_name(skill_name)
        canonical_skill_name = _canonical_skill_name(skill_name)
        bundle = skill_studio_service.load_skill_bundle(resolved_skill_name)
        result = {
            "mode": "skill_detail",
            "message": (
                f"已打开 skill：{canonical_skill_name}"
                + (f"（当前工作草稿：{resolved_skill_name}）" if resolved_skill_name != canonical_skill_name else "")
            ),
            "bundle": bundle,
            "items": [_build_skill_bundle_item(resolved_skill_name, bundle)],
            "workspace": {
                "type": "skill_editor",
                "title": f"Skill: {resolved_skill_name}",
                "url": _with_script_root(f"/skills/studio/{quote(resolved_skill_name)}"),
            },
        }
        result["thread_context_patch"] = _build_active_skill_context(resolved_skill_name)
        return result

    if command_action == "open_tool":
        tool_name = target_name or str(args[0] if args else "").strip()
        if not tool_name:
            raise ValueError("请提供 tool 名称，例如 /tool 个股动量排名")
        bundle = tool_studio_service.load_tool_bundle(tool_name)
        return {
            "mode": "tool_detail",
            "message": f"已打开 tool：{tool_name}",
            "bundle": bundle,
            "items": [_build_tool_bundle_item(tool_name, bundle)],
            "workspace": {
                "type": "tool_editor",
                "title": f"Tool: {tool_name}",
                "url": _with_script_root(f"/tools/studio/{quote(tool_name)}"),
            },
        }

    return {
        "mode": "unknown_command",
        "message": f"暂不支持的指令：{command}",
    }


def _build_asset_invocation_payload(
    text: str,
    *,
    selected_asset: dict | None = None,
    application_context: dict | None = None,
    thread_context: dict | None = None,
    attachments: list[dict] | None = None,
    thread_id: int | None = None,
    turn_id: int | None = None,
) -> dict:
    application_context = application_context if isinstance(application_context, dict) else {}
    thread_context = thread_context if isinstance(thread_context, dict) else {}
    attachments = attachments if isinstance(attachments, list) else []
    invocation = asset_invocation_service.plan(
        text=text,
        selected_asset=selected_asset,
        attachments=attachments,
        thread_context=thread_context,
        owner_ids=_custom_tool_owner_ids(thread_context=thread_context, thread_id=thread_id),
    )
    if not invocation:
        raise AssetInvocationError("没有选择可调用的 Tool 或 Skill")
    return _execute_asset_invocation_payload(
        invocation,
        text=text,
        application_context=application_context,
        thread_context=thread_context,
        thread_id=thread_id,
        turn_id=turn_id,
    )


def _asset_invocation_preview_block(invocation: dict) -> dict:
    preview = invocation.get("preview") if isinstance(invocation.get("preview"), dict) else {}
    return {
        "event": "block",
        "block_id": "asset_invocation_preview",
        "block_type": "structured_text",
        "title": "本次调用",
        "content": str(preview.get("message") or invocation.get("message") or "正在准备调用。").strip(),
        "data": preview,
    }


def _execute_asset_invocation_payload(
    invocation: dict,
    *,
    text: str,
    application_context: dict,
    thread_context: dict,
    thread_id: int | None,
    turn_id: int | None,
) -> dict:
    if invocation.get("status") != "ready":
        message = str(invocation.get("message") or "还需要补充调用参数。").strip()
        return {
            "mode": "asset_invocation_needs_input",
            "message": message,
            "asset_invocation": invocation,
            "surface_blocks": [
                {
                    "block_id": "asset_invocation_needs_input",
                    "block_type": "narrative",
                    "content": message,
                }
            ],
            "llm_usage": invocation.get("llm_usage") or {},
        }

    target = invocation.get("target") if isinstance(invocation.get("target"), dict) else {}
    if str(target.get("kind") or "").strip() == "tool":
        execution_plan = asset_invocation_service.build_tool_execution_plan(invocation)
        result = tool_plan_runtime_service.execute_for_assistant(
            execution_plan=execution_plan,
            user_text=str(invocation.get("user_request") or text).strip(),
            application_context=application_context,
            thread_context=thread_context,
            thread_id=thread_id,
            turn_id=turn_id,
        )
        result = _apply_application_workspace_orchestration(result, application_context)
        result["asset_invocation"] = invocation
        result["surface_blocks"] = [
            _asset_invocation_preview_block(invocation),
            *[item for item in (result.get("surface_blocks") or []) if isinstance(item, dict)],
        ]
        result["llm_usage"] = _merge_llm_usage(
            invocation.get("llm_usage") if isinstance(invocation.get("llm_usage"), dict) else None,
            result.get("llm_usage") if isinstance(result.get("llm_usage"), dict) else None,
        )
        return result

    skill_name = str(target.get("name") or "").strip()
    jobs = [
        _submit_generic_skill_job(
            skill_name,
            {
                "input_payload": call,
                "source_type": "assistant_explicit_skill_invocation",
                "thread_id": thread_id,
                "turn_id": turn_id,
                "application_name": str(application_context.get("application_name") or "").strip(),
                "agent_name": str((application_context.get("default_agent") or {}).get("agent_name") or "").strip(),
                "agent_runtime_profile": (
                    (application_context.get("default_agent") or {}).get("runtime_profile")
                    if isinstance((application_context.get("default_agent") or {}).get("runtime_profile"), dict)
                    else {}
                ),
            },
        )
        for call in invocation.get("calls") or []
        if isinstance(call, dict)
    ]
    return _apply_application_workspace_orchestration(
        {
            "mode": "asset_invocation_submitted",
            "message": f"已提交 {len(jobs)} 项 Skill 任务：{skill_name}",
            "skill_name": skill_name,
            "jobs": jobs,
            "items": [_build_task_job_item(job) for job in jobs],
            "asset_invocation": invocation,
            "surface_blocks": [_asset_invocation_preview_block(invocation)],
            "llm_usage": invocation.get("llm_usage") or {},
        },
        application_context,
    )


def _submit_stock_deep_dive_job(args: dict) -> dict:
    input_payload = args.get("input_payload")
    if not isinstance(input_payload, dict):
        input_payload = _build_stock_deep_dive_input(args)
    route_snapshot = args.get("route_snapshot")
    if not isinstance(route_snapshot, dict):
        route_snapshot = None

    code = str(input_payload.get("code") or "").strip()
    if not code:
        raise ValueError("code 不能为空")

    max_steps_raw = str(args.get("max_steps") or "").strip()
    max_steps = None
    if max_steps_raw:
        max_steps = max(1, int(max_steps_raw))

    return async_task_service.submit_stock_deep_dive(
        input_payload=input_payload,
        max_steps=max_steps,
        enable_think=_parse_bool_flag(args.get("enable_think"), default=False),
        execution_profile=str(args.get("execution_profile") or input_payload.get("_execution_profile") or "real").strip() or "real",
        source_type=str(args.get("source_type") or "api").strip() or "api",
        conversation_id=str(args.get("conversation_id") or "").strip(),
        thread_id=int(args.get("thread_id")) if str(args.get("thread_id") or "").strip().isdigit() else None,
        turn_id=int(args.get("turn_id")) if str(args.get("turn_id") or "").strip().isdigit() else None,
        trigger_message_id=str(args.get("trigger_message_id") or "").strip(),
        route_snapshot=route_snapshot,
        application_name=str(args.get("application_name") or "").strip(),
        agent_name=str(args.get("agent_name") or "").strip(),
        agent_runtime_profile=args.get("agent_runtime_profile") if isinstance(args.get("agent_runtime_profile"), dict) else None,
    )


def _submit_generic_skill_job(skill_name: str, args: dict) -> dict:
    input_payload = args.get("input_payload")
    if not isinstance(input_payload, dict):
        if skill_name == "stock_deep_dive":
            input_payload = _build_stock_deep_dive_input(args)
        else:
            raise ValueError("input_payload 必须是对象")
    route_snapshot = args.get("route_snapshot")
    if not isinstance(route_snapshot, dict):
        route_snapshot = None

    max_steps_raw = str(args.get("max_steps") or "").strip()
    max_steps = None
    if max_steps_raw:
        max_steps = max(1, int(max_steps_raw))

    return async_task_service.submit_skill_job(
        skill_name=skill_name,
        input_payload=input_payload,
        max_steps=max_steps,
        enable_think=_parse_bool_flag(args.get("enable_think"), default=False),
        execution_profile=str(args.get("execution_profile") or input_payload.get("_execution_profile") or "real").strip() or "real",
        source_type=str(args.get("source_type") or "api").strip() or "api",
        conversation_id=str(args.get("conversation_id") or "").strip(),
        thread_id=int(args.get("thread_id")) if str(args.get("thread_id") or "").strip().isdigit() else None,
        turn_id=int(args.get("turn_id")) if str(args.get("turn_id") or "").strip().isdigit() else None,
        trigger_message_id=str(args.get("trigger_message_id") or "").strip(),
        route_snapshot=route_snapshot,
        application_name=str(args.get("application_name") or "").strip(),
        agent_name=str(args.get("agent_name") or "").strip(),
        agent_runtime_profile=args.get("agent_runtime_profile") if isinstance(args.get("agent_runtime_profile"), dict) else None,
    )


def _find_tool_step(skill_run_result: dict, tool_name: str) -> dict:
    steps = skill_run_result.get("steps") or []
    for step in steps:
        tool_call = step.get("tool_call") or {}
        if str(tool_call.get("name") or "").strip() == tool_name:
            return step
    return {}


def _find_section(render_payload: dict, section_id: str) -> dict | None:
    sections = render_payload.get("sections") or []
    for section in sections:
        if str(section.get("section_id") or "").strip() == section_id:
            return section
    return None


def _find_block(section: dict | None, block_id: str) -> dict | None:
    if not isinstance(section, dict):
        return None
    for block in section.get("blocks") or []:
        if str(block.get("block_id") or "").strip() == block_id:
            return block
    return None


def _append_block_if_missing(section: dict | None, block: dict) -> None:
    if not isinstance(section, dict):
        return
    block_id = str(block.get("block_id") or "").strip()
    if not block_id:
        return
    if _find_block(section, block_id):
        return
    section.setdefault("blocks", []).append(block)


def _build_funds_metric_items(snapshot: dict) -> list[dict]:
    today_funds = snapshot.get("today_funds") or {}
    industry_funds = snapshot.get("industry_funds") or {}
    extra_info = today_funds.get("extra_info") or {}
    items = [
        {
            "label": "主力净额",
            "value": str(extra_info.get("today_main_net_inflow") or today_funds.get("main_net_inflow_wan") or "-"),
            "change": "",
        },
        {
            "label": "行业净额",
            "value": str(industry_funds.get("industry_net_inflow_wan") or "-"),
            "change": "",
        },
        {
            "label": "行业名称",
            "value": str(industry_funds.get("industry_name") or "-"),
            "change": "",
        },
        {
            "label": "5日净额",
            "value": str(extra_info.get("five_day_main_net") or "-"),
            "change": "",
        },
    ]
    return items


def _build_historical_table_block(snapshot: dict) -> dict:
    rows = []
    for item in (snapshot.get("historical_table") or [])[:12]:
        if not isinstance(item, dict):
            continue
        rows.append(
            [
                str(item.get("date") or ""),
                str(item.get("close") or ""),
                str(item.get("change_pct") or ""),
                str(item.get("net_inflow_wan") or ""),
                str(item.get("five_day_main_net_wan") or ""),
            ]
        )
    return {
        "block_id": "funds_historical_table",
        "type": "table",
        "title": "近12日主力资金表",
        "span": {"desktop": 12, "tablet": 12, "mobile": 1},
        "height": "tall",
        "data": {
            "headers": ["日期", "收盘价", "涨跌幅", "主力净额(万)", "5日主力净额(万)"],
            "rows": rows,
        },
    }


def _build_series_chart_block(block_id: str, title: str, payload: dict, span_desktop: int = 6) -> dict:
    return {
        "block_id": block_id,
        "type": "series_chart",
        "title": title,
        "span": {"desktop": span_desktop, "tablet": 12, "mobile": 1},
        "height": "tall",
        "data": payload,
    }


def _enhance_stock_deep_dive_render_payload(render_payload: dict, result_map: dict) -> dict:
    enhanced = deepcopy(render_payload if isinstance(render_payload, dict) else {})
    if not enhanced:
        return enhanced
    skill_run_result = result_map.get("skill_run_result") or {}
    if not isinstance(skill_run_result, dict):
        return enhanced

    quote_step = (
        _find_tool_step(skill_run_result, "stock_history_kline")
        or _find_tool_step(skill_run_result, "stock_realtime_quote")
        or _find_tool_step(skill_run_result, "stock_quote")
    )
    quote_data = ((quote_step.get("tool_result") or {}).get("data") or {}) if isinstance(quote_step, dict) else {}
    funds_step = _find_tool_step(skill_run_result, "stock_funds")
    funds_data = ((funds_step.get("tool_result") or {}).get("data") or {}) if isinstance(funds_step, dict) else {}

    market_section = _find_section(enhanced, "market_overview")
    kline_block = _find_block(market_section, "kline_chart")
    if isinstance(kline_block, dict) and isinstance(quote_data, dict):
        daily_kline = quote_data.get("daily_kline") or {}
        if isinstance(daily_kline, dict):
            merged = dict(kline_block.get("data") or {})
            merged["name"] = quote_data.get("name") or merged.get("name")
            merged["symbol"] = quote_data.get("stk_code") or quote_data.get("code") or merged.get("symbol")
            merged["candles"] = daily_kline.get("kline") or merged.get("candles") or []
            merged["lines"] = daily_kline.get("indicators") or merged.get("lines") or {}
            kline_block["data"] = merged

    funds_section = _find_section(enhanced, "capital_flow_section")
    snapshot = funds_data.get("snapshot") or {}
    chart_payloads = snapshot.get("chart_payloads") or {}
    if isinstance(funds_section, dict) and isinstance(snapshot, dict):
        _append_block_if_missing(
            funds_section,
            {
                "block_id": "funds_metrics",
                "type": "metric_strip",
                "title": "资金核心指标",
                "span": {"desktop": 12, "tablet": 12, "mobile": 1},
                "height": "compact",
                "data": {"items": _build_funds_metric_items(snapshot)},
            },
        )
        historical_table = snapshot.get("historical_table") or []
        if isinstance(historical_table, list) and historical_table:
            _append_block_if_missing(funds_section, _build_historical_table_block(snapshot))
        historical_chart = chart_payloads.get("historical_net_inflow_line")
        if isinstance(historical_chart, dict):
            _append_block_if_missing(
                funds_section,
                _build_series_chart_block("funds_history_chart", "历史资金净流入", historical_chart, span_desktop=6),
            )
        recent_chart = chart_payloads.get("recent_main_funds_bar_line")
        if isinstance(recent_chart, dict):
            _append_block_if_missing(
                funds_section,
                _build_series_chart_block("funds_recent_chart", "近30日主力资金", recent_chart, span_desktop=6),
            )
        intraday_chart = chart_payloads.get("intraday_funds_line")
        if isinstance(intraday_chart, dict):
            _append_block_if_missing(
                funds_section,
                _build_series_chart_block("funds_intraday_chart", "当日分时资金线", intraday_chart, span_desktop=12),
            )

    return enhanced


def _format_momentum_group_title(group_key: str) -> str:
    text = str(group_key or "").strip()
    if not text:
        return "动量列表"
    mapping = {
        "full_market_4d": "全市场 4日动量前10",
        "full_market_5d": "全市场 5日动量前10",
        "zz100_4d": "中证100 4日动量前10",
        "zz100_5d": "中证100 5日动量前10",
        "zz_100_4d": "中证100 4日动量前10",
        "zz_100_5d": "中证100 5日动量前10",
        "custom_list_4d": "自选列表 4日动量前10",
        "custom_list_5d": "自选列表 5日动量前10",
    }
    if text in mapping:
        return mapping[text]
    return text.replace("_", " ").strip()


def _normalize_momentum_rows(rows) -> list[list[str]]:
    normalized_rows = []
    for item in (rows or [])[:10]:
        if not isinstance(item, dict):
            continue
        normalized_rows.append(
            [
                str(item.get("rank") or ""),
                str(item.get("stock_code") or item.get("code") or ""),
                str(item.get("stock_name") or item.get("name") or ""),
                str(item.get("momentum_value") or ""),
                str(item.get("current_pct") or ""),
                str(item.get("rank_change") or ""),
            ]
        )
    return normalized_rows


def _build_momentum_render_payload(render_payload: dict) -> dict:
    payload = deepcopy(render_payload if isinstance(render_payload, dict) else {})
    top_momentum_stocks = payload.get("top_momentum_stocks")
    if not isinstance(top_momentum_stocks, dict) or not top_momentum_stocks:
        return payload
    if isinstance(payload.get("sections"), list) and payload.get("sections"):
        return payload

    section_blocks = []
    for group_key, rows in top_momentum_stocks.items():
        normalized_rows = _normalize_momentum_rows(rows)
        if not normalized_rows:
            continue
        section_blocks.append(
            {
                "block_id": f"momentum_table_{str(group_key).strip()}",
                "type": "table",
                "title": _format_momentum_group_title(str(group_key).strip()),
                "span": {"desktop": 12, "tablet": 12, "mobile": 1},
                "height": "tall",
                "data": {
                    "headers": ["排名", "代码", "名称", "动量值", "当日涨幅", "排名变化"],
                    "rows": normalized_rows,
                },
            }
        )

    focus_analysis = payload.get("focus_stock_analysis")
    if isinstance(focus_analysis, dict) and focus_analysis:
        focus_items = []
        for code, value in focus_analysis.items():
            if not value:
                continue
            focus_items.append(
                {
                    "title": str(code or "").strip(),
                    "desc": str(value if not isinstance(value, (dict, list)) else json.dumps(value, ensure_ascii=False)),
                    "time": "",
                }
            )
        if focus_items:
            section_blocks.append(
                {
                    "block_id": "momentum_focus_analysis",
                    "type": "text_list",
                    "title": "重点观察",
                    "span": {"desktop": 12, "tablet": 12, "mobile": 1},
                    "height": "compact",
                    "data": {"items": focus_items[:10]},
                }
            )

    if not section_blocks:
        return payload

    payload.setdefault("page_type", "momentum_dashboard")
    payload["sections"] = [
        {
            "section_id": "momentum_rankings",
            "section_kind": "data",
            "title": "动量排名",
            "description": "按分组与窗口展示当前动量前10列表。",
            "layout": {"desktop_columns": 12, "mobile_columns": 1},
            "blocks": section_blocks,
        }
    ]
    return payload


def _enhance_render_payload(content: dict, result_map: dict) -> dict:
    enhanced = deepcopy(content if isinstance(content, dict) else {})
    if not enhanced:
        return enhanced
    page_type = str(enhanced.get("page_type") or "").strip()
    if page_type == "stock_deep_dive":
        return _enhance_stock_deep_dive_render_payload(enhanced, result_map)
    if page_type == "momentum_dashboard" or isinstance(enhanced.get("top_momentum_stocks"), dict):
        return _build_momentum_render_payload(enhanced)
    return enhanced


def _to_json_safe(obj):
    """
    递归把对象中的 Decimal 转 float，date/datetime 转 ISO 字符串。
    支持 dict / list / tuple / 基础类型。
    """
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (dt.datetime, dt.date)):
        # 或者 .strftime("%Y-%m-%d %H:%M:%S")
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_json_safe(v) for v in obj]
    return obj


@app.route("/stock_deep_dive", methods=["GET"])
@app.route("/skills/stock_deep_dive", methods=["GET"])
def stock_deep_dive_page():
    query = {}
    for key in ("code", "name", "question", "max_steps", "enable_think", "focus", "as_of_date", "runtime_mode"):
        value = (request.args.get(key) or "").strip()
        if value:
            query[key] = value
    payload_api_url = _with_script_root("/api/stock_deep_dive_page_payload")
    if query:
        payload_api_url = f"{payload_api_url}?{urlencode(query)}"
    return render_template(
        "report_protocol_demo.html",
        payload_api_url=payload_api_url,
        async_job_enabled=True,
        async_job_create_url=_with_script_root("/api/skills/stock_deep_dive/jobs"),
        async_job_base_url=_with_script_root("/api/tasks"),
        async_job_request_payload=query,
        )


@app.route("/tasks/<job_id>/view", methods=["GET"])
@app.route("/skills/stock_deep_dive/tasks/<job_id>", methods=["GET"])
def stock_deep_dive_task_view(job_id):
    task_job_id = str(job_id or "").strip()
    if not task_job_id:
        return "job_id 不能为空", 400
    return render_template(
        "report_protocol_demo.html",
        payload_api_url="",
        async_job_enabled=False,
        async_job_create_url="",
        async_job_base_url=_with_script_root("/api/tasks"),
        async_job_request_payload={},
        task_view_enabled=True,
        task_view_job_id=task_job_id,
        task_view_refresh_ms=2500,
    )


@app.route("/skills/studio", methods=["GET"])
@app.route("/skills/studio/", methods=["GET"])
@app.route("/skills/studio/<skill_name>", methods=["GET"])
def skill_studio_page(skill_name: str = ""):
    return render_template(
        "skill_studio.html",
        page_title="Skill Studio",
        initial_skill_name=str(skill_name or "").strip(),
        api_catalog_url=_with_script_root("/api/skills/catalog"),
        api_bundle_base_url=_with_script_root("/api/skills"),
        api_router_preview_url=_with_script_root("/api/router/preview"),
    )


@app.route("/skills", methods=["GET"])
@app.route("/skills/", methods=["GET"])
def skills_catalog_page():
    return render_template(
        "skills_catalog.html",
        page_title="Skills",
        api_skill_catalog_url=_with_script_root("/api/skills/catalog"),
        api_skill_base_url=_with_script_root("/api/skills"),
        skill_studio_base_url=_with_script_root("/skills/studio"),
    )


@app.route("/applications/studio", methods=["GET"])
@app.route("/applications/studio/", methods=["GET"])
@app.route("/applications/studio/<application_name>", methods=["GET"])
def application_studio_page(application_name: str = ""):
    return render_template(
        "application_studio.html",
        page_title="Application Studio",
        initial_application_name=str(application_name or "").strip(),
        api_catalog_url=_with_script_root("/api/applications/catalog"),
        api_bundle_base_url=_with_script_root("/api/applications"),
    )


@app.route("/agents/studio", methods=["GET"])
@app.route("/agents/studio/", methods=["GET"])
@app.route("/agents/studio/<agent_name>", methods=["GET"])
def agent_studio_page(agent_name: str = ""):
    return render_template(
        "agent_studio.html",
        page_title="Agent Studio",
        initial_agent_name=str(agent_name or "").strip(),
        api_catalog_url=_with_script_root("/api/agents/catalog"),
        api_bundle_base_url=_with_script_root("/api/agents"),
    )


@app.route("/studio", methods=["GET"])
@app.route("/studio/", methods=["GET"])
@app.route("/router/studio", methods=["GET"])
@app.route("/router/studio/", methods=["GET"])
@app.route("/tasks/studio", methods=["GET"])
@app.route("/tasks/studio/", methods=["GET"])
def router_studio_page():
    return render_template(
        "router_studio.html",
        page_title="Task Router Studio",
        api_router_preview_url=_with_script_root("/api/router/preview"),
        api_router_submit_url=_with_script_root("/api/router/submit"),
        api_task_base_url=_with_script_root("/api/tasks"),
        api_skill_base_url=_with_script_root("/api/skills"),
    )


@app.route("/tools/simple_web_debug", methods=["GET"])
def simple_web_debug_page():
    return render_template(
        "simple_web_debug.html",
        page_title="Simple Web Debug",
        api_debug_url=_with_script_root("/api/tools/simple_web_debug"),
    )


@app.route("/tools", methods=["GET"])
@app.route("/tools/", methods=["GET"])
def tools_catalog_page():
    return render_template(
        "tools_catalog.html",
        page_title="Tools",
        api_tool_catalog_url=_with_script_root("/api/tools/catalog"),
        api_tool_base_url=_with_script_root("/api/tools"),
        tool_studio_base_url=_with_script_root("/tools/studio"),
        tool_detail_base_url=_with_script_root("/tools"),
    )


@app.route("/tools/<tool_name>/view", methods=["GET"])
@app.route("/tools/<tool_name>/view/", methods=["GET"])
def tool_detail_page(tool_name: str):
    return render_template(
        "tool_detail.html",
        page_title="Tool Detail",
        tool_name=str(tool_name or "").strip(),
        api_tool_bundle_base_url=_with_script_root("/api/tools"),
        tool_studio_base_url=_with_script_root("/tools/studio"),
        tools_base_url=_with_script_root("/tools"),
    )


@app.route("/tools/studio", methods=["GET"])
@app.route("/tools/studio/", methods=["GET"])
@app.route("/tools/studio/<tool_name>", methods=["GET"])
def tool_studio_page(tool_name: str = ""):
    return render_template(
        "tool_studio.html",
        page_title="Tool Studio",
        api_tool_catalog_url=_with_script_root("/api/tools/catalog"),
        api_finance_data_catalog_url=_with_script_root("/api/tools/finance-data/catalog"),
        api_tool_bundle_base_url=_with_script_root("/api/tools"),
        initial_tool_name=str(tool_name or "").strip(),
        create_mode=_parse_bool_flag(request.args.get("new"), default=False),
        template_bundle=tool_studio_service.build_tool_template_bundle(str(tool_name or "new_tool").strip() or "new_tool"),
        tools_base_url=_with_script_root("/tools"),
    )


@app.route("/login", methods=["GET"])
@app.route("/login/", methods=["GET"])
def login_entry_page():
    guest_identity = _resolve_current_guest_identity()
    guest_user_id = str(guest_identity.get("user_id") or "").strip()
    existing_thread_id = UserSessionService._safe_int(
        request.cookies.get(UserSessionService.THREAD_COOKIE_NAME, "")
    )
    recent_threads = runtime_conversation_service.list_threads(
        owner_type="user",
        owner_id=guest_user_id,
        limit=8,
    )
    return render_template(
        "login_entry.html",
        page_title="登录入口",
        guest_user_id=guest_user_id,
        existing_thread_id=existing_thread_id,
        recent_threads=recent_threads,
        assistant_url=_with_script_root("/assistant"),
        guest_entry_url=_with_script_root("/login/guest"),
        new_conversation_url=_with_script_root("/login/new"),
        thread_select_base_url=_with_script_root("/login/thread"),
    )


@app.route("/login/guest", methods=["GET"])
def login_guest_entry():
    return _make_guest_session_response(_with_script_root("/assistant"))


@app.route("/login/new", methods=["GET"])
def login_new_conversation():
    return _make_guest_session_response(_with_script_root("/assistant"), clear_thread=True)


@app.route("/login/thread/<int:thread_id>", methods=["GET"])
def login_resume_thread(thread_id: int):
    guest_identity = _resolve_current_guest_identity()
    recent_threads = runtime_conversation_service.list_threads(
        owner_type="user",
        owner_id=str(guest_identity.get("user_id") or ""),
        limit=50,
    )
    if int(thread_id) not in {int(item.get("thread_id") or 0) for item in recent_threads}:
        return redirect(_with_script_root("/login"))
    response = _make_guest_session_response(_with_script_root("/assistant"))
    response.set_cookie(
        UserSessionService.THREAD_COOKIE_NAME,
        str(int(thread_id)),
        max_age=60 * 60 * 24 * 30,
        samesite="Lax",
    )
    return response


@app.route("/assistant", methods=["GET"])
@app.route("/assistant/", methods=["GET"])
def conversation_workbench_page():
    if (REACT_FRONTEND_DIST_DIR / "index.html").is_file():
        guest_identity = _resolve_current_guest_identity()
        response = make_response(send_from_directory(REACT_FRONTEND_DIST_DIR, "index.html"))
        response.set_cookie(
            UserSessionService.GUEST_COOKIE_NAME,
            str(guest_identity.get("user_id") or ""),
            max_age=60 * 60 * 24 * 180,
            samesite="Lax",
        )
        response.set_cookie(
            "aiia_guest_session_token",
            str(guest_identity.get("session_token") or ""),
            max_age=60 * 60 * 24 * 30,
            samesite="Lax",
            httponly=True,
        )
        return response
    return _legacy_conversation_workbench_page()


@app.route("/assistant/legacy", methods=["GET"])
def legacy_conversation_workbench_page():
    return _legacy_conversation_workbench_page()


@app.route("/assistant/<path:frontend_path>", methods=["GET"])
def react_conversation_workbench_asset(frontend_path: str):
    requested = (REACT_FRONTEND_DIST_DIR / str(frontend_path or "")).resolve()
    if requested.is_file() and requested.is_relative_to(REACT_FRONTEND_DIST_DIR):
        return send_from_directory(REACT_FRONTEND_DIST_DIR, str(frontend_path))
    if (REACT_FRONTEND_DIST_DIR / "index.html").is_file():
        return send_from_directory(REACT_FRONTEND_DIST_DIR, "index.html")
    return redirect(_with_script_root("/assistant/legacy"))


def _legacy_conversation_workbench_page():
    app_ctx = _ui_application_context("investment_workbench")
    guest_identity = _resolve_current_guest_identity()
    initial_thread_id = UserSessionService._safe_int(
        request.cookies.get(UserSessionService.THREAD_COOKIE_NAME, "")
    )
    response = make_response(render_template(
        "conversation_workbench.html",
        page_title="Conversation Workbench",
        application_name=app_ctx.get("application_name"),
        application_display_name=app_ctx.get("display_name"),
        quick_commands=app_ctx.get("quick_commands") or [],
        quick_actions=app_ctx.get("quick_actions") or [],
        chat_placeholder=app_ctx.get("chat_placeholder") or "",
        assistant_intro=app_ctx.get("assistant_intro") or "",
        workspace_links=app_ctx.get("workspace_links") or [],
        default_agent=app_ctx.get("default_agent"),
        api_dispatch_url=_with_script_root("/api/chat/dispatch"),
        api_application_base_url=_with_script_root("/api/applications"),
        api_skill_catalog_url=_with_script_root("/api/skills/catalog"),
        api_tool_catalog_url=_with_script_root("/api/tools/catalog"),
        api_agent_catalog_url=_with_script_root("/api/agents/catalog"),
        api_application_catalog_url=_with_script_root("/api/applications/catalog"),
        api_task_base_url=_with_script_root("/api/tasks"),
        api_attachment_upload_url=_with_script_root("/api/attachments/upload"),
        api_assistant_threads_url=_with_script_root("/api/assistant/threads"),
        api_custom_tool_stream_start_url=_with_script_root("/api/custom_tool/stream/start"),
        script_root=(request.script_root or ""),
        skills_studio_url=_with_script_root("/skills/studio"),
        tools_catalog_url=_with_script_root("/tools"),
        router_studio_url=str((app_ctx.get("workspace") or {}).get("url") or _with_script_root("/router/studio")),
        initial_thread_id=initial_thread_id,
        guest_user_id=guest_identity.get("user_id") or "",
    ))
    response.set_cookie(
        UserSessionService.GUEST_COOKIE_NAME,
        str(guest_identity.get("user_id") or ""),
        max_age=60 * 60 * 24 * 180,
        samesite="Lax",
    )
    response.set_cookie(
        "aiia_guest_session_token",
        str(guest_identity.get("session_token") or ""),
        max_age=60 * 60 * 24 * 30,
        samesite="Lax",
        httponly=True,
    )
    if initial_thread_id:
        response.set_cookie(
            UserSessionService.THREAD_COOKIE_NAME,
            str(initial_thread_id),
            max_age=60 * 60 * 24 * 30,
            samesite="Lax",
        )
    return response


@app.route("/api/stock_deep_dive", methods=["GET"])
@app.route("/api/skills/stock_deep_dive", methods=["GET"])
def api_stock_deep_dive():
    try:
        payload = _run_stock_deep_dive_from_request(request.args)
        return jsonify(_to_json_safe({"ok": True, "result": payload}))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": f"stock_deep_dive 执行失败: {exc}"}), 500


@app.route("/api/stock_deep_dive_page_payload", methods=["GET"])
@app.route("/api/skills/stock_deep_dive/render_payload", methods=["GET"])
def api_stock_deep_dive_page_payload():
    try:
        payload = _run_stock_deep_dive_from_request(request.args)
        render_payload = payload.get("render_payload")
        if not isinstance(render_payload, dict):
            return jsonify(
                {
                    "version": "1.0",
                    "page_id": f"stock-deep-dive-error-{int(time.time())}",
                    "page_type": "stock_deep_dive",
                    "title": "Stock Deep Dive 执行失败",
                    "subtitle": "skill 已执行，但未返回可渲染的 render_payload。",
                    "as_of": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "summary": {"market_phase": "unknown", "tags": ["render_payload_missing"]},
                    "sections": [
                        {
                            "section_id": "error",
                            "section_kind": "error",
                            "title": "错误信息",
                            "layout": {"desktop_columns": 12, "mobile_columns": 1},
                            "blocks": [
                                {
                                    "block_id": "error-text",
                                    "type": "structured_text",
                                    "title": "执行结果",
                                    "span": {"desktop": 12, "tablet": 12, "mobile": 1},
                                    "height": "normal",
                                    "data": {
                                        "lead": "skill 没有生成 render_payload，请检查 schema 收敛和工具输出。",
                                        "bullets": [
                                            {"label": "ok", "text": str(payload.get("ok"))},
                                            {"label": "error", "text": str(payload.get("error") or "")},
                                        ],
                                    },
                                }
                            ],
                        }
                    ],
                }
            ), 500
        return jsonify(_to_json_safe(render_payload))
    except ValueError as exc:
        return jsonify(
            {
                "version": "1.0",
                "page_id": f"stock-deep-dive-bad-request-{int(time.time())}",
                "page_type": "stock_deep_dive",
                "title": "参数错误",
                "subtitle": str(exc),
                "as_of": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "summary": {"market_phase": "unknown", "tags": ["bad_request"]},
                "sections": [],
            }
        ), 400
    except Exception as exc:
        return jsonify(
            {
                "version": "1.0",
                "page_id": f"stock-deep-dive-runtime-error-{int(time.time())}",
                "page_type": "stock_deep_dive",
                "title": "Stock Deep Dive 运行失败",
                "subtitle": f"{exc}",
                "as_of": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "summary": {"market_phase": "unknown", "tags": ["runtime_error"]},
                "sections": [],
            }
        ), 500


@app.route("/api/skills/stock_deep_dive/jobs", methods=["POST"])
def api_create_stock_deep_dive_job():
    try:
        payload = _submit_stock_deep_dive_job(_extract_request_payload())
        return jsonify(_to_json_safe({"ok": True, "job": payload})), 202
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except TaskCapacityError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 429
    except Exception as exc:
        return jsonify({"ok": False, "error": f"创建异步任务失败: {exc}"}), 500


@app.route("/api/chat/dispatch", methods=["POST"])
def api_chat_dispatch():
    thread_id = None
    turn_id = None
    try:
        payload = _extract_request_payload()
        text = str(payload.get("text") or payload.get("message") or "").strip()
        attachment_ids = payload.get("attachment_ids")
        if not isinstance(attachment_ids, list):
            attachment_ids = []
        attachments = attachment_service.list_attachments(attachment_ids)
        selected_asset = payload.get("selected_asset") if isinstance(payload.get("selected_asset"), dict) else {}
        if not text and not attachments and not selected_asset:
            return jsonify({"ok": False, "error": "text、attachments 和 selected_asset 不能同时为空"}), 400
        guest_identity = _resolve_current_guest_identity()
        application_name = str(payload.get("application_name") or "investment_workbench").strip() or "investment_workbench"
        app_ctx = application_runtime_service.get_application_context(application_name)
        initial_thread_title = str(app_ctx.get("display_name") or application_name)
        requested_thread_id = (
            int(payload.get("thread_id"))
            if str(payload.get("thread_id") or "").strip().isdigit()
            else UserSessionService._safe_int(request.cookies.get(UserSessionService.THREAD_COOKIE_NAME, ""))
        )
        thread_id = runtime_conversation_service.ensure_thread(
            thread_id=requested_thread_id,
            title=initial_thread_title,
            owner_type="user",
            owner_id=str(guest_identity.get("user_id") or ""),
            context_summary=f"{application_name} 会话",
        )
        thread_context = runtime_conversation_service.get_thread_context(thread_id=thread_id)
        context_window = runtime_conversation_service.get_context_window(thread_id=thread_id, max_rounds=5)
        if context_window:
            thread_context = {
                **thread_context,
                "context_window": context_window,
            }
        thread_context = {
            **thread_context,
            "_custom_tool_owner_ids": _custom_tool_owner_ids(
                thread_context=thread_context,
                user_id=str(guest_identity.get("user_id") or ""),
                thread_id=thread_id,
            ),
        }
        turn_id = runtime_conversation_service.create_turn(
            thread_id=thread_id,
            user_input_text=text or (f"${str(selected_asset.get('name') or '').strip()}" if selected_asset else f"[attachment:{len(attachments)}]"),
            input_payload=_to_json_safe({**payload, "application_name": application_name, "attachments": attachments}),
        )
        if not requested_thread_id:
            _schedule_thread_title(
                thread_id=thread_id,
                user_text=text or f"分析 {len(attachments)} 个图片附件",
                expected_title=initial_thread_title,
            )
        if selected_asset or text.startswith("$"):
            result = _build_asset_invocation_payload(
                text,
                selected_asset=selected_asset,
                application_context=app_ctx,
                thread_context=thread_context,
                attachments=attachments,
                thread_id=thread_id,
                turn_id=turn_id,
            )
        else:
            result = _build_chat_dispatch_payload(
                text,
                application_context=app_ctx,
                thread_context=thread_context,
                attachments=attachments,
                thread_id=thread_id,
                turn_id=turn_id,
                owner_id=str(guest_identity.get("user_id") or ""),
            )
        dispatch_plan_payload = result.get("dispatch_plan") if isinstance(result.get("dispatch_plan"), dict) else {}
        _submit_finance_cc_shadow(
            thread_id=thread_id,
            turn_id=turn_id,
            owner_id=str(guest_identity.get("user_id") or ""),
            user_text=text,
            dispatch_plan=dispatch_plan_payload,
            application_context=app_ctx,
            thread_context=thread_context,
        )
        merged_llm_usage = _merge_llm_usage(
            result.get("llm_usage") if isinstance(result.get("llm_usage"), dict) else None,
            dispatch_plan_payload.get("llm_usage") if isinstance(dispatch_plan_payload.get("llm_usage"), dict) else None,
            (
                (
                    dispatch_plan_payload.get("preprocess_result")
                    if isinstance(dispatch_plan_payload.get("preprocess_result"), dict)
                    else {}
                ).get("llm_usage")
                if isinstance(dispatch_plan_payload, dict)
                else None
            ),
        )
        if any(int(merged_llm_usage.get(key, 0) or 0) > 0 for key in ("prompt_tokens", "completion_tokens", "total_tokens", "call_count")):
            result["llm_usage"] = merged_llm_usage
        thread_context_patch = result.get("thread_context_patch") if isinstance(result.get("thread_context_patch"), dict) else {}
        if thread_context_patch:
            runtime_conversation_service.update_thread_context(
                thread_id=thread_id,
                patch=thread_context_patch,
            )
        assistant_message = str(result.get("message") or "已处理。").strip()
        answer_summary_result = answer_summary_service.summarize(
            raw_user_text=text or f"[attachment:{len(attachments)}]",
            assistant_output_text=assistant_message,
            output_payload=result,
            enable_llm=False,
        )
        answer_summary = str(answer_summary_result.get("answer_summary") or "").strip()
        if answer_summary:
            result["answer_summary"] = answer_summary
        runtime_conversation_service.complete_turn(
            thread_id=thread_id,
            turn_id=turn_id,
            assistant_output_text=assistant_message,
            output_payload=_to_json_safe(result),
            model_name=str(result.get("model_name") or "").strip(),
            token_usage=result.get("llm_usage") if isinstance(result.get("llm_usage"), dict) else None,
        )
        response = jsonify(_to_json_safe({"ok": True, "thread_id": thread_id, "turn_id": turn_id, **result}))
        response.set_cookie(
            UserSessionService.GUEST_COOKIE_NAME,
            str(guest_identity.get("user_id") or ""),
            max_age=60 * 60 * 24 * 180,
            samesite="Lax",
        )
        response.set_cookie(
            "aiia_guest_session_token",
            str(guest_identity.get("session_token") or ""),
            max_age=60 * 60 * 24 * 30,
            samesite="Lax",
            httponly=True,
        )
        response.set_cookie(
            UserSessionService.THREAD_COOKIE_NAME,
            str(thread_id),
            max_age=60 * 60 * 24 * 30,
            samesite="Lax",
        )
        return response
    except ContextResolutionError as exc:
        technical_detail = str(exc.technical_detail or exc)
        app.logger.warning(
            "chat context resolution failed after correction thread_id=%s detail=%s raw_response=%r",
            thread_id,
            technical_detail,
            str(exc.raw_response or "")[:2000],
        )
        user_message = str(exc.user_message or "").strip()
        result = {
            "mode": "conversation",
            "message": user_message,
            "surface_blocks": [{
                "block_id": "context_resolution_message",
                "block_type": "markdown",
                "type": "markdown",
                "mode": "replace",
                "content": user_message,
                "stage": "conversation",
                "data": {"role": "assistant_message"},
            }],
        }
        if thread_id is not None and turn_id is not None:
            runtime_conversation_service.complete_turn(
                thread_id=thread_id,
                turn_id=turn_id,
                assistant_output_text=user_message,
                output_payload=_to_json_safe(result),
            )
        return jsonify(_to_json_safe({
            "ok": True,
            "thread_id": thread_id,
            "turn_id": turn_id,
            **result,
        }))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except VisionIntakeServiceError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": f"对话分发失败: {exc}"}), 500


@app.route("/api/custom_tool/stream/start", methods=["POST"])
def api_custom_tool_stream_start():
    try:
        payload = _extract_request_payload()
        text = str(payload.get("text") or payload.get("message") or "").strip()
        interaction_response = payload.get("interaction_response") if isinstance(payload.get("interaction_response"), dict) else {}
        selected_asset = payload.get("selected_asset") if isinstance(payload.get("selected_asset"), dict) else {}
        attachment_ids = payload.get("attachment_ids") if isinstance(payload.get("attachment_ids"), list) else []
        if not text and not interaction_response and not selected_asset and not attachment_ids:
            return jsonify({"ok": False, "error": "text、interaction_response、selected_asset 和 attachment_ids 不能同时为空"}), 400
        if interaction_response and not str(interaction_response.get("action_id") or "").strip():
            return jsonify({"ok": False, "error": "interaction_response.action_id 不能为空"}), 400
        action_id = str(interaction_response.get("action_id") or "").strip()
        revision_actions = {"custom_tool.activate_draft"}
        expected_revision = interaction_response.get("expected_revision")
        if interaction_response and action_id in revision_actions and not isinstance(expected_revision, int):
            return jsonify({"ok": False, "error": "启用实现时 expected_revision 必须是整数"}), 400
        if interaction_response and expected_revision is not None and not isinstance(expected_revision, int):
            return jsonify({"ok": False, "error": "interaction_response.expected_revision 必须是整数"}), 400
        guest_identity = _resolve_current_guest_identity()
        run_id = uuid.uuid4().hex
        stored_payload = {
            "run_id": run_id,
            "text": text,
            "interaction_response": interaction_response,
            "selected_asset": selected_asset,
            "attachment_ids": attachment_ids,
            "thread_id": payload.get("thread_id"),
            "application_name": str(payload.get("application_name") or "investment_workbench").strip() or "investment_workbench",
            "guest_identity": guest_identity,
            "cookie_thread_id": request.cookies.get(UserSessionService.THREAD_COOKIE_NAME, ""),
        }
        custom_tool_stream_requests[run_id] = stored_payload
        response = jsonify(_to_json_safe({
            "ok": True,
            "run_id": run_id,
            "stream_url": _with_script_root(f"/api/custom_tool/stream/{run_id}"),
        }))
        response.set_cookie(
            UserSessionService.GUEST_COOKIE_NAME,
            str(guest_identity.get("user_id") or ""),
            max_age=60 * 60 * 24 * 180,
            samesite="Lax",
        )
        response.set_cookie(
            "aiia_guest_session_token",
            str(guest_identity.get("session_token") or ""),
            max_age=60 * 60 * 24 * 30,
            samesite="Lax",
            httponly=True,
        )
        return response
    except Exception as exc:
        return jsonify({"ok": False, "error": f"创建流式任务失败: {exc}"}), 500


@app.route("/api/custom_tool/stream/<run_id>", methods=["GET"])
def api_custom_tool_stream(run_id: str):
    payload = custom_tool_stream_requests.pop(str(run_id or "").strip(), None)
    if not payload:
        return Response(_sse_payload({"event": "error", "message": "stream run 不存在或已过期"}), mimetype="text/event-stream")

    event_queue: queue.Queue = queue.Queue()
    stop_token = object()

    def emit(item: dict) -> None:
        event_queue.put(item)

    def worker() -> None:
        try:
            if payload.get("selected_asset") or str(payload.get("text") or "").lstrip().startswith("$"):
                _run_asset_invocation_stream_payload(payload, emit=emit)
            else:
                _run_custom_tool_stream_payload(payload, emit=emit)
        finally:
            event_queue.put(stop_token)

    threading.Thread(target=worker, daemon=True).start()

    def generate():
        is_asset_invocation = bool(payload.get("selected_asset")) or str(payload.get("text") or "").lstrip().startswith("$")
        yield _sse_payload({
            "event": "run_started",
            "run_id": payload.get("run_id"),
            "message": "正在解析本次工具调用。" if is_asset_invocation else "智能体开始处理自定义工具任务。",
        })
        while True:
            item = event_queue.get()
            if item is stop_token:
                break
            yield _sse_payload(item)

    response = Response(generate(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@app.route("/api/attachments/upload", methods=["POST"])
def api_attachment_upload():
    try:
        guest_identity = _resolve_current_guest_identity()
        uploads = request.files.getlist("files")
        if not uploads:
            single = request.files.get("file")
            if single is not None:
                uploads = [single]
        if not uploads:
            return jsonify({"ok": False, "error": "未接收到上传文件"}), 400
        items = [
            attachment_service.save_upload(upload, owner_id=str(guest_identity.get("user_id") or ""))
            for upload in uploads
        ]
        response = jsonify(_to_json_safe({"ok": True, "items": items}))
        response.set_cookie(
            UserSessionService.GUEST_COOKIE_NAME,
            str(guest_identity.get("user_id") or ""),
            max_age=60 * 60 * 24 * 180,
            samesite="Lax",
        )
        response.set_cookie(
            "aiia_guest_session_token",
            str(guest_identity.get("session_token") or ""),
            max_age=60 * 60 * 24 * 30,
            samesite="Lax",
            httponly=True,
        )
        return response
    except AttachmentServiceError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": f"附件上传失败: {exc}"}), 500


@app.route("/api/assistant/thread/reset", methods=["POST"])
def api_assistant_reset_thread():
    response = jsonify({"ok": True})
    response.delete_cookie(UserSessionService.THREAD_COOKIE_NAME)
    return response


@app.route("/api/assistant/threads", methods=["GET"])
def api_assistant_threads():
    try:
        guest_identity = _resolve_current_guest_identity()
        items = runtime_conversation_service.list_threads(
            owner_type="user",
            owner_id=str(guest_identity.get("user_id") or ""),
            limit=20,
        )
        requested_thread_id = UserSessionService._safe_int(
            request.cookies.get(UserSessionService.THREAD_COOKIE_NAME, "")
        )
        owned_thread_ids = {int(item.get("thread_id") or 0) for item in items}
        active_thread_id = requested_thread_id if requested_thread_id in owned_thread_ids else 0
        response = jsonify(_to_json_safe({
            "ok": True,
            "items": items,
            "active_thread_id": active_thread_id or None,
        }))
        response.set_cookie(
            UserSessionService.GUEST_COOKIE_NAME,
            str(guest_identity.get("user_id") or ""),
            max_age=60 * 60 * 24 * 180,
            samesite="Lax",
        )
        response.set_cookie(
            "aiia_guest_session_token",
            str(guest_identity.get("session_token") or ""),
            max_age=60 * 60 * 24 * 30,
            samesite="Lax",
            httponly=True,
        )
        return response
    except Exception as exc:
        return jsonify({"ok": False, "error": f"读取会话列表失败: {exc}"}), 500


@app.route("/api/assistant/threads/<int:thread_id>", methods=["GET"])
def api_assistant_thread_detail(thread_id: int):
    try:
        guest_identity = _resolve_current_guest_identity()
        thread = runtime_conversation_service.get_thread(thread_id=int(thread_id), include_context=True)
        if not thread:
            return jsonify({"ok": False, "error": "thread 不存在"}), 404
        if thread.get("owner_type") != "user" or str(thread.get("owner_id") or "") != str(guest_identity.get("user_id") or ""):
            return jsonify({"ok": False, "error": "无权访问该 thread"}), 403
        embedded_context = thread.pop("_thread_context", None)
        thread_context = (
            embedded_context
            if isinstance(embedded_context, dict)
            else runtime_conversation_service.get_thread_context(thread_id=int(thread_id))
        )
        custom_tool_state = thread_context.get("custom_tool_state") if isinstance(thread_context.get("custom_tool_state"), dict) else {}
        turns = runtime_conversation_service.list_turns(
            thread_id=int(thread_id),
            limit=100,
            include_output_payload=True,
            history_payload_only=True,
        )
        for turn in turns:
            output_payload = turn.get("output_payload") if isinstance(turn.get("output_payload"), dict) else {}
            retained_fields = {
                key: output_payload[key]
                for key in (
                    "mode",
                    "message",
                    "surface_blocks",
                    "render_blocks",
                    "render_payload",
                    "surface",
                    "items",
                    "workspace",
                    "task_state",
                )
                if key in output_payload
            }
            turn["output_payload"] = retained_fields
        response = jsonify(_to_json_safe({
            "ok": True,
            "thread": thread,
            "turns": turns,
            "custom_tool_active": bool(custom_tool_state),
            "custom_tool_status": str(custom_tool_state.get("status") or "").strip() if custom_tool_state else "",
        }))
        response.set_cookie(
            UserSessionService.THREAD_COOKIE_NAME,
            str(int(thread_id)),
            max_age=60 * 60 * 24 * 30,
            samesite="Lax",
        )
        return response
    except Exception as exc:
        return jsonify({"ok": False, "error": f"读取 thread 详情失败: {exc}"}), 500


@app.route("/api/skills/catalog", methods=["GET"])
def api_skill_catalog():
    try:
        return jsonify(_to_json_safe({"ok": True, "items": skill_studio_service.list_skills()}))
    except Exception as exc:
        return jsonify({"ok": False, "error": f"获取 skills 目录失败: {exc}"}), 500


@app.route("/api/skills/generate_blueprint", methods=["POST"])
def api_generate_skill_blueprint():
    try:
        payload = _extract_request_payload()
        skill_name = str(payload.get("skill_name") or "").strip()
        requirement_text = str(payload.get("requirement_text") or payload.get("user_text") or "").strip()
        application_name = str(payload.get("application_name") or "investment_workbench").strip() or "investment_workbench"
        selected_tools = payload.get("selected_tools")
        selected_skills = payload.get("selected_skills")
        if not isinstance(selected_tools, list):
            selected_tools = []
        if not isinstance(selected_skills, list):
            selected_skills = []
        save_draft = str(payload.get("save_draft") or "").strip().lower() in {"1", "true", "yes", "on"}
        if save_draft:
            _ensure_skill_draft_overwritable(skill_name)
        application_context = None
        try:
            application_context = application_runtime_service.get_application_context(application_name)
        except Exception:
            application_context = None
        selected_tools, selected_skills = _resolve_blueprint_assets(
            application_context=application_context,
            selected_tools=selected_tools,
            selected_skills=selected_skills,
        )
        bundle = skill_blueprint_service.generate_bundle(
            skill_name=skill_name,
            requirement_text=requirement_text,
            selected_tools=selected_tools,
            selected_skills=selected_skills,
            application_name=application_name,
            agent_name=str(((application_context or {}).get("default_agent") or {}).get("agent_name") or "").strip(),
        )
        if save_draft:
            skill_studio_service.save_skill_bundle(
                skill_name=skill_name,
                skill_md_text=bundle["files"]["skill_md_text"],
                skill_config_text=bundle["files"]["skill_config_text"],
                output_schema_text=bundle["files"]["output_schema_text"],
            )
            bundle = skill_studio_service.load_skill_bundle(skill_name)
        return jsonify(_to_json_safe({"ok": True, "bundle": bundle, "saved": save_draft}))
    except SkillBlueprintError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": f"生成 skill blueprint 失败: {exc}"}), 500


@app.route("/api/skills/refine_blueprint", methods=["POST"])
def api_refine_skill_blueprint():
    try:
        payload = _extract_request_payload()
        source_skill_name = _canonical_skill_name(str(payload.get("source_skill_name") or payload.get("skill_name") or "").strip())
        refinement_text = str(payload.get("refinement_text") or payload.get("requirement_text") or payload.get("user_text") or "").strip()
        application_name = str(payload.get("application_name") or "investment_workbench").strip() or "investment_workbench"
        target_skill_name = _canonical_skill_name(str(payload.get("target_skill_name") or "").strip() or source_skill_name)
        selected_tools = payload.get("selected_tools")
        selected_skills = payload.get("selected_skills")
        if not isinstance(selected_tools, list):
            selected_tools = []
        if not isinstance(selected_skills, list):
            selected_skills = []
        save_draft = str(payload.get("save_draft") or "").strip().lower() in {"1", "true", "yes", "on"}
        application_context = None
        try:
            application_context = application_runtime_service.get_application_context(application_name)
        except Exception:
            application_context = None
        selected_tools, selected_skills = _resolve_blueprint_assets(
            application_context=application_context,
            selected_tools=selected_tools,
            selected_skills=selected_skills,
        )
        bundle = skill_blueprint_service.refine_bundle(
            source_skill_name=source_skill_name,
            target_skill_name=target_skill_name,
            refinement_text=refinement_text,
            selected_tools=selected_tools,
            selected_skills=selected_skills,
            application_name=application_name,
            agent_name=str(((application_context or {}).get("default_agent") or {}).get("agent_name") or "").strip(),
        )
        if save_draft:
            skill_studio_service.save_skill_bundle(
                skill_name=target_skill_name,
                skill_md_text=bundle["files"]["skill_md_text"],
                skill_config_text=bundle["files"]["skill_config_text"],
                output_schema_text=bundle["files"]["output_schema_text"],
            )
            bundle = skill_studio_service.load_skill_bundle(target_skill_name)
        return jsonify(_to_json_safe({"ok": True, "bundle": bundle, "target_skill_name": target_skill_name}))
    except (SkillBlueprintError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": f"生成 skill refine blueprint 失败: {exc}"}), 500


@app.route("/api/applications/catalog", methods=["GET"])
def api_application_catalog():
    try:
        return jsonify(_to_json_safe({"ok": True, "items": application_studio_service.list_applications()}))
    except Exception as exc:
        return jsonify({"ok": False, "error": f"获取 applications 目录失败: {exc}"}), 500


@app.route("/api/applications/<application_name>/bundle", methods=["GET"])
def api_get_application_bundle(application_name):
    try:
        payload = application_studio_service.load_application_bundle(str(application_name).strip())
        return jsonify(_to_json_safe({"ok": True, "bundle": payload}))
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"ok": False, "error": f"获取 application bundle 失败: {exc}"}), 500


@app.route("/api/applications/<application_name>/context", methods=["GET"])
def api_get_application_context(application_name):
    try:
        payload = _ui_application_context(str(application_name).strip())
        return jsonify(_to_json_safe({"ok": True, "context": payload}))
    except ApplicationRuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"ok": False, "error": f"获取 application context 失败: {exc}"}), 500


@app.route("/api/applications/<application_name>/bundle", methods=["PUT"])
def api_save_application_bundle(application_name):
    try:
        payload = _extract_request_payload()
        bundle = application_studio_service.save_application_bundle(
            application_name=str(application_name).strip(),
            application_md_text=str(payload.get("application_md_text") or ""),
            application_config_text=str(payload.get("application_config_text") or ""),
            schema_text=str(payload.get("schema_text") or ""),
        )
        return jsonify(_to_json_safe({"ok": True, "bundle": bundle}))
    except ApplicationStudioError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"ok": False, "error": f"保存 application bundle 失败: {exc}"}), 500


@app.route("/api/agents/catalog", methods=["GET"])
def api_agent_catalog():
    try:
        return jsonify(_to_json_safe({"ok": True, "items": agent_studio_service.list_agents()}))
    except Exception as exc:
        return jsonify({"ok": False, "error": f"获取 agents 目录失败: {exc}"}), 500


@app.route("/api/agents/<agent_name>/bundle", methods=["GET"])
def api_get_agent_bundle(agent_name):
    try:
        payload = agent_studio_service.load_agent_bundle(str(agent_name).strip())
        return jsonify(_to_json_safe({"ok": True, "bundle": payload}))
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"ok": False, "error": f"获取 agent bundle 失败: {exc}"}), 500


@app.route("/api/agents/<agent_name>/bundle", methods=["PUT"])
def api_save_agent_bundle(agent_name):
    try:
        payload = _extract_request_payload()
        bundle = agent_studio_service.save_agent_bundle(
            agent_name=str(agent_name).strip(),
            soul_md_text=str(payload.get("soul_md_text") or ""),
            agent_config_text=str(payload.get("agent_config_text") or ""),
            schema_text=str(payload.get("schema_text") or ""),
        )
        return jsonify(_to_json_safe({"ok": True, "bundle": bundle}))
    except AgentStudioError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"ok": False, "error": f"保存 agent bundle 失败: {exc}"}), 500


@app.route("/api/skills/<skill_name>/bundle", methods=["GET"])
def api_get_skill_bundle(skill_name):
    try:
        payload = skill_studio_service.load_skill_bundle_with_fallback(str(skill_name).strip())
        return jsonify(_to_json_safe({"ok": True, "bundle": payload}))
    except Exception as exc:
        return jsonify({"ok": False, "error": f"获取 skill bundle 失败: {exc}"}), 500


@app.route("/api/skills/<skill_name>/bundle", methods=["PUT"])
def api_save_skill_bundle(skill_name):
    try:
        payload = _extract_request_payload()
        bundle = skill_studio_service.save_skill_bundle(
            skill_name=str(skill_name).strip(),
            skill_md_text=str(payload.get("skill_md_text") or ""),
            skill_config_text=str(payload.get("skill_config_text") or ""),
            output_schema_text=str(payload.get("output_schema_text") or ""),
        )
        return jsonify(_to_json_safe({"ok": True, "bundle": bundle}))
    except SkillStudioError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"ok": False, "error": f"保存 skill bundle 失败: {exc}"}), 500


@app.route("/api/skills/<skill_name>/tool_selection", methods=["POST"])
def api_preview_skill_tool_selection(skill_name):
    try:
        payload = _extract_request_payload()
        input_payload = payload.get("input_payload")
        if not isinstance(input_payload, dict):
            return jsonify({"ok": False, "error": "input_payload 必须是对象"}), 400
        result = skill_studio_service.preview_tool_selection(
            skill_name=str(skill_name).strip(),
            input_payload=input_payload,
            tool_mode=str(payload.get("tool_mode") or "").strip(),
        )
        return jsonify(_to_json_safe({"ok": True, **result}))
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"ok": False, "error": f"预览工具选择失败: {exc}"}), 500


@app.route("/api/skills/<skill_name>/availability", methods=["POST"])
def api_update_skill_availability(skill_name):
    try:
        payload = _extract_request_payload()
        bundle = skill_studio_service.update_skill_availability(
            skill_name=str(skill_name).strip(),
            lifecycle=str(payload.get("lifecycle") or "").strip(),
            retrieval_mode=str(payload.get("retrieval_mode") or "").strip(),
        )
        return jsonify(_to_json_safe({"ok": True, "bundle": bundle}))
    except SkillStudioError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"ok": False, "error": f"更新 skill availability 失败: {exc}"}), 500


@app.route("/api/skills/<skill_name>/execution_plan", methods=["POST"])
def api_preview_skill_execution_plan(skill_name):
    try:
        payload = _extract_request_payload()
        input_payload = payload.get("input_payload")
        if not isinstance(input_payload, dict):
            return jsonify({"ok": False, "error": "input_payload 必须是对象"}), 400
        result = skill_studio_service.preview_execution_plan(
            skill_name=str(skill_name).strip(),
            input_payload=input_payload,
            tool_mode=str(payload.get("tool_mode") or "").strip(),
        )
        return jsonify(_to_json_safe({"ok": True, "execution_plan": result}))
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"ok": False, "error": f"预览 execution plan 失败: {exc}"}), 500


@app.route("/api/router/preview", methods=["POST"])
def api_preview_intent_route():
    try:
        payload = _extract_request_payload()
        user_text = str(payload.get("user_text") or payload.get("question") or "").strip()
        if not user_text:
            return jsonify({"ok": False, "error": "user_text 不能为空"}), 400
        context = payload.get("context")
        application_name = str(payload.get("application_name") or "investment_workbench").strip() or "investment_workbench"
        execution_profile = str(payload.get("execution_profile") or "real").strip() or "real"
        result = agent_execution_service.preview_route(
            user_text=user_text,
            application_name=application_name,
            context=context if isinstance(context, dict) else {},
            execution_profile=execution_profile,
        )
        result["mode"] = "route_preview"
        result["items"] = [
            _build_route_item(
                application_name=str(result.get("application_name") or "").strip(),
                agent_name=str(result.get("agent_name") or "").strip(),
                route_snapshot=result.get("route_snapshot") if isinstance(result.get("route_snapshot"), dict) else {},
                selected_skill=str(((result.get("route_snapshot") or {}).get("route") or {}).get("selected_skill") or "").strip(),
                launchables=result.get("launchables") if isinstance(result.get("launchables"), list) else [],
                input_error=str(result.get("input_error") or "").strip(),
            )
        ]
        result = _apply_application_workspace_orchestration(
            result,
            _ui_application_context(application_name),
        )
        return jsonify(_to_json_safe(result))
    except AgentExecutionError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": f"预览自然语言路由失败: {exc}"}), 500


@app.route("/api/router/submit", methods=["POST"])
def api_submit_routed_task():
    try:
        payload = _extract_request_payload()
        user_text = str(payload.get("user_text") or payload.get("question") or "").strip()
        if not user_text:
            return jsonify({"ok": False, "error": "user_text 不能为空"}), 400
        context = payload.get("context")
        application_name = str(payload.get("application_name") or "investment_workbench").strip() or "investment_workbench"
        execution_profile = str(payload.get("execution_profile") or "real").strip() or "real"
        result = agent_execution_service.submit_routed_task(
            user_text=user_text,
            application_name=application_name,
            context=context if isinstance(context, dict) else {},
            execution_profile=execution_profile,
            source_type="router_studio",
        )
        submit_status = str(result.get("submit_status") or "").strip()
        result["mode"] = f"route_submit_{submit_status}" if submit_status else "route_submit"
        result["items"] = [
            _build_route_item(
                application_name=str(result.get("application_name") or "").strip(),
                agent_name=str(result.get("agent_name") or "").strip(),
                route_snapshot=result.get("route_snapshot") if isinstance(result.get("route_snapshot"), dict) else {},
                submit_status=submit_status,
                selected_skill=str(result.get("skill_name") or "").strip(),
                job=result.get("job") if isinstance(result.get("job"), dict) else {},
                launchables=result.get("launchables") if isinstance(result.get("launchables"), list) else [],
                clarification_state=result.get("clarification_state") if isinstance(result.get("clarification_state"), dict) else {},
                input_error=str(result.get("input_error") or "").strip(),
            )
        ]
        result = _apply_application_workspace_orchestration(
            result,
            _ui_application_context(application_name),
        )
        status_code = 202 if str(result.get("submit_status") or "").strip() == "submitted" else 200
        return jsonify(_to_json_safe(result)), status_code
    except AgentExecutionError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except TaskCapacityError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 429
    except Exception as exc:
        return jsonify({"ok": False, "error": f"提交路由任务失败: {exc}"}), 500


@app.route("/api/tools/simple_web_debug", methods=["GET", "POST"])
def api_simple_web_debug():
    try:
        payload = _extract_request_payload()
        query = str(payload.get("query") or payload.get("keyword") or "").strip()
        if not query:
            return jsonify({"ok": False, "error": "query 不能为空"}), 400

        raw_site_names = payload.get("site_names")
        if isinstance(raw_site_names, str):
            site_names = [part.strip() for part in raw_site_names.split(",") if part.strip()]
        elif isinstance(raw_site_names, list):
            site_names = [str(part).strip() for part in raw_site_names if str(part).strip()]
        else:
            site_names = []

        result = search_simple_web(
            query=query,
            site_names=site_names or None,
            max_results_per_site=int(payload.get("max_results_per_site", 10) or 10),
            keep_days=int(payload.get("keep_days", 3) or 3),
            follow_depth=int(payload.get("follow_depth", 0) or 0),
            persist=_parse_bool_flag(payload.get("persist"), default=False),
            dedupe=_parse_bool_flag(payload.get("dedupe"), default=True),
            fetch_mode_override=str(payload.get("fetch_mode_override") or "").strip(),
        )
        return jsonify(_to_json_safe({"ok": True, "result": result}))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": f"simple_web_debug 执行失败: {exc}"}), 500


@app.route("/api/tools/catalog", methods=["GET"])
def api_tool_catalog():
    try:
        items = tool_studio_service.list_tools()
        # The React composer uses this endpoint for `$` suggestions.  Custom
        # tools live in the runtime artifact store rather than the static
        # tool-definition directory, so merge the active tools visible to the
        # current guest into the same catalog response.
        guest_identity = _resolve_current_guest_identity()
        owner_id = str(guest_identity.get("user_id") or "").strip()
        custom_manifests = custom_tool_agent_service.store.list_tools(
            owner_ids=[owner_id] if owner_id else None,
        )
        known_names = {
            str(item.get("tool_name") or "").strip()
            for item in items
            if isinstance(item, dict) and str(item.get("tool_name") or "").strip()
        }
        for manifest in custom_manifests:
            if not isinstance(manifest, dict):
                continue
            tool_name = str(manifest.get("tool_name") or "").strip()
            if not tool_name or tool_name in known_names:
                continue
            try:
                bundle = custom_tool_agent_service.store.load_for_runtime(
                    tool_name,
                    owner_ids=[owner_id] if owner_id else None,
                    allow_inactive=False,
                )
            except Exception:
                bundle = {}
            items.append(
                {
                    "tool_name": tool_name,
                    "display_name": str(manifest.get("display_name") or tool_name).strip(),
                    "description": str(manifest.get("description") or "").strip(),
                    "status": str(manifest.get("status") or "active").strip(),
                    "version": "v1",
                    "availability": {
                        "lifecycle": "active",
                        "retrieval_mode": "retrievable",
                        "visibility": str(manifest.get("visibility") or "personal").strip(),
                    },
                    "capabilities": list(manifest.get("capabilities") or ["custom_tool"]),
                    "custom_tool": True,
                    "input_schema": bundle.get("input_schema") if isinstance(bundle.get("input_schema"), dict) else {},
                    "sample_input": bundle.get("sample_input") if isinstance(bundle.get("sample_input"), dict) else {},
                }
            )
            known_names.add(tool_name)
        return jsonify(_to_json_safe({"ok": True, "items": items}))
    except Exception as exc:
        return jsonify({"ok": False, "error": f"获取 tool 目录失败: {exc}"}), 500


@app.route("/api/tools/finance-data/catalog", methods=["GET"])
def api_finance_data_tool_catalog():
    try:
        subject = str(request.args.get("subject") or "").strip()
        dataview = str(request.args.get("dataview") or "").strip()
        return jsonify(_to_json_safe({"ok": True, "catalog": tool_studio_service.load_finance_data_catalog(subject=subject, dataview=dataview)}))
    except KeyError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": f"获取 finance data catalog 失败: {exc}"}), 500


@app.route("/api/tools/finance-data/catalog/node", methods=["PUT"])
def api_save_finance_data_tool_catalog_node():
    try:
        payload = _extract_request_payload()
        result = tool_studio_service.save_finance_data_catalog_node(
            node_type=str(payload.get("node_type") or "").strip(),
            subject=str(payload.get("subject") or "").strip(),
            dataview=str(payload.get("dataview") or "").strip(),
            path=payload.get("path") if isinstance(payload.get("path"), list) else None,
            node=payload.get("node") if isinstance(payload.get("node"), dict) else {},
        )
        return jsonify(_to_json_safe({"ok": True, "catalog": result}))
    except ToolStudioError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except KeyError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": f"保存 finance data catalog 节点失败: {exc}"}), 500


@app.route("/api/tools/<tool_name>/bundle", methods=["GET"])
def api_get_tool_bundle(tool_name):
    try:
        bundle = tool_studio_service.load_tool_bundle(str(tool_name).strip())
        return jsonify(_to_json_safe({"ok": True, "bundle": bundle}))
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"ok": False, "error": f"获取 tool bundle 失败: {exc}"}), 500


@app.route("/api/tools/<tool_name>/bundle", methods=["PUT"])
def api_save_tool_bundle(tool_name):
    try:
        payload = _extract_request_payload()
        bundle = tool_studio_service.save_tool_bundle(
            tool_name=str(tool_name).strip(),
            definition_text=str(payload.get("definition_text") or ""),
            output_schema_text=str(payload.get("output_schema_text") or ""),
            tool_spec_text=str(payload.get("tool_spec_text") or ""),
            tool_hub_text=str(payload.get("tool_hub_text") or ""),
        )
        return jsonify(_to_json_safe({"ok": True, "bundle": bundle}))
    except ToolStudioError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"ok": False, "error": f"保存 tool bundle 失败: {exc}"}), 500


@app.route("/api/tools/<tool_name>/run", methods=["POST"])
def api_run_tool_bundle(tool_name):
    try:
        payload = _extract_request_payload()
        result = tool_studio_service.run_tool(
            tool_name=str(tool_name).strip(),
            arguments=payload.get("arguments") or {},
            execution_profile=str(payload.get("execution_profile") or "mock").strip() or "mock",
        )
        return jsonify(_to_json_safe({"ok": True, **result}))
    except ToolStudioError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": f"执行 tool 失败: {exc}"}), 500


@app.route("/api/tools/<tool_name>/availability", methods=["POST"])
def api_update_tool_availability(tool_name):
    try:
        payload = _extract_request_payload()
        bundle = tool_studio_service.update_tool_availability(
            tool_name=str(tool_name).strip(),
            lifecycle=str(payload.get("lifecycle") or "").strip(),
            retrieval_mode=str(payload.get("retrieval_mode") or "").strip(),
        )
        return jsonify(_to_json_safe({"ok": True, "bundle": bundle}))
    except ToolStudioError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"ok": False, "error": f"更新 tool availability 失败: {exc}"}), 500


@app.route("/api/skills/<skill_name>/jobs", methods=["POST"])
def api_create_generic_skill_job(skill_name):
    skill_text = str(skill_name or "").strip()
    if skill_text == "stock_deep_dive":
        return api_create_stock_deep_dive_job()
    try:
        payload = _submit_generic_skill_job(skill_text, _extract_request_payload())
        return jsonify(_to_json_safe({"ok": True, "job": payload})), 202
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except TaskCapacityError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 429
    except Exception as exc:
        return jsonify({"ok": False, "error": f"创建异步任务失败: {exc}"}), 500


@app.route("/api/tasks/<job_id>", methods=["GET"])
def api_get_task_job(job_id):
    try:
        payload = async_task_service.get_job(str(job_id).strip())
        if not payload:
            return jsonify({"ok": False, "error": "task job 不存在"}), 404
        return jsonify(_to_json_safe({"ok": True, "job": payload}))
    except Exception as exc:
        return jsonify({"ok": False, "error": f"获取任务状态失败: {exc}"}), 500


@app.route("/api/tasks/<job_id>/steps", methods=["GET"])
def api_get_task_steps(job_id):
    try:
        if not async_task_service.get_job(str(job_id).strip()):
            return jsonify({"ok": False, "error": "task job 不存在"}), 404
        payload = async_task_service.get_steps(str(job_id).strip())
        return jsonify(_to_json_safe({"ok": True, "items": payload}))
    except Exception as exc:
        return jsonify({"ok": False, "error": f"获取任务步骤失败: {exc}"}), 500


@app.route("/api/tasks/<job_id>/result", methods=["GET"])
def api_get_task_result(job_id):
    try:
        if not async_task_service.get_job(str(job_id).strip()):
            return jsonify({"ok": False, "error": "task job 不存在"}), 404
        result_type = str(request.args.get("result_type") or "").strip()
        result_map = async_task_service.get_result_map(str(job_id).strip())
        if result_type:
            content = result_map.get(result_type)
            if result_type == "render_payload" and isinstance(content, dict):
                content = _enhance_render_payload(content, result_map)
            return jsonify(_to_json_safe({"ok": True, "result_type": result_type, "content": content}))
        if isinstance(result_map.get("render_payload"), dict):
            result_map = dict(result_map)
            result_map["render_payload"] = _enhance_render_payload(result_map["render_payload"], result_map)
            final_output = result_map.get("final_output")
            if isinstance(final_output, dict):
                final_copy = dict(final_output)
                if isinstance(final_copy.get("render_payload"), dict):
                    final_copy["render_payload"] = result_map["render_payload"]
                result_map["final_output"] = final_copy
        return jsonify(_to_json_safe({"ok": True, "results": result_map}))
    except Exception as exc:
        return jsonify({"ok": False, "error": f"获取任务结果失败: {exc}"}), 500


def _build_report_protocol_demo_payload():
    return {
        "version": "1.0",
        "page_id": "hotspot-report-demo-20260312",
        "page_type": "hotspot_report",
        "title": "AI 算力主线观察",
        "subtitle": "后端按 section/block 协议输出内容，前端按顺序渲染并适配 PC 与移动端。",
        "as_of": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "theme": {
            "accent": "#b74d33",
            "tone": "warm",
        },
        "summary": {
            "market_phase": "risk_on",
            "tags": ["算力", "液冷", "光模块", "服务器"],
        },
        "sections": [
            {
                "section_id": "market-overview",
                "section_kind": "market_overview",
                "title": "行情区",
                "description": "用摘要指标、结构化观点和分时事件线，把今天的市场状态先定下来。",
                "layout": {"desktop_columns": 12, "mobile_columns": 1},
                "blocks": [
                    {
                        "block_id": "market-metrics",
                        "type": "metric_strip",
                        "title": "市场温度",
                        "span": {"desktop": 12, "tablet": 12, "mobile": 1},
                        "height": "compact",
                        "data": {
                            "items": [
                                {"label": "上证指数", "value": "3382.41", "change": "+0.68%"},
                                {"label": "创业板", "value": "2198.12", "change": "+1.42%"},
                                {"label": "涨停数", "value": "86", "change": "+12"},
                                {"label": "炸板率", "value": "18%", "change": "-4%"},
                                {"label": "主线强度", "value": "8.7/10", "change": "+0.9"},
                            ]
                        },
                    },
                    {
                        "block_id": "market-brief",
                        "type": "structured_text",
                        "title": "盘面结论",
                        "span": {"desktop": 7, "tablet": 12, "mobile": 1},
                        "data": {
                            "lead": "今天最强的仍然是 AI 算力链，资金从龙头向液冷、光模块、交换机分支扩散。",
                            "paragraphs": [
                                "指数层面没有明显拖累，情绪与成交配合较好，因此强势方向具备继续演化的条件。",
                                "不过午后如果出现高位量价背离，短线需要优先盯高位核心而不是盲目追补涨。"
                            ],
                            "bullets": [
                                {"label": "主线", "text": "服务器与光模块共振，弹性股活跃度提升"},
                                {"label": "扩散", "text": "液冷和电源链接力，属于主线内部轮动"},
                                {"label": "观察点", "text": "成交额是否继续放大，龙头是否能保持封单强度"},
                            ],
                            "callout": {
                                "tone": "warning",
                                "text": "若指数翻绿且炸板率快速回升，应把页面中的热力图颜色和排序更新为风险优先视角。"
                            }
                        },
                    },
                    {
                        "block_id": "intraday-timeline",
                        "type": "timeline",
                        "title": "盘中节奏",
                        "span": {"desktop": 5, "tablet": 12, "mobile": 1},
                        "data": {
                            "items": [
                                {"time": "09:31", "title": "服务器方向率先放量", "text": "高辨识度个股快速冲高，板块成交占比抬升。"},
                                {"time": "10:06", "title": "光模块核心股接力", "text": "弹性标的普遍翻红，热度从龙头扩散到二线。"},
                                {"time": "11:18", "title": "液冷分支启动", "text": "题材完成从主线到细分支的扩散，说明情绪未退。"},
                                {"time": "14:07", "title": "资金回流前排", "text": "尾盘回流龙头，市场更偏向抱团主线。"},
                            ]
                        },
                    },
                ],
            },
            {
                "section_id": "research-prediction",
                "section_kind": "research_prediction",
                "title": "研报预测区",
                "description": "这一块直接承接后端对研报、目标价、评级变化和核心逻辑的结构化抽取。",
                "layout": {"desktop_columns": 12, "mobile_columns": 1},
                "blocks": [
                    {
                        "block_id": "research-table",
                        "type": "table",
                        "title": "重点研报摘要",
                        "span": {"desktop": 8, "tablet": 12, "mobile": 1},
                        "data": {
                            "columns": [
                                {"key": "name", "label": "标的"},
                                {"key": "sector", "label": "方向"},
                                {"key": "rating", "label": "评级"},
                                {"key": "target", "label": "目标价"},
                                {"key": "reason", "label": "逻辑"},
                            ],
                            "rows": [
                                {"name": "中际旭创", "sector": "光模块", "rating": "增持", "target": "182", "reason": "800G 出货提升，海外需求超预期"},
                                {"name": "浪潮信息", "sector": "服务器", "rating": "买入", "target": "61", "reason": "AI 服务器订单与算力资本开支共振"},
                                {"name": "英维克", "sector": "液冷", "rating": "增持", "target": "39", "reason": "液冷渗透率提升，盈利弹性开始兑现"},
                                {"name": "新易盛", "sector": "光模块", "rating": "买入", "target": "121", "reason": "产品结构升级带动毛利率改善"},
                            ]
                        },
                    },
                    {
                        "block_id": "research-summary",
                        "type": "structured_text",
                        "title": "一致预期摘要",
                        "span": {"desktop": 4, "tablet": 12, "mobile": 1},
                        "data": {
                            "lead": "卖方口径正在从“景气修复”转向“资本开支兑现”。",
                            "bullets": [
                                {"label": "服务器", "text": "关注订单兑现和国产替代两条线"},
                                {"label": "光模块", "text": "关注海外训练集群带来的高端产品需求"},
                                {"label": "液冷", "text": "关注从主题逻辑走向收入兑现的验证点"},
                            ],
                        },
                    },
                ],
            },
            {
                "section_id": "sector-heat",
                "section_kind": "sector_heat",
                "title": "板块与个股热力图",
                "description": "这里是后端最值得定义协议的地方。后端只要给分组、热度、涨跌幅和权重，前端就能稳定渲染布局热力图。",
                "layout": {"desktop_columns": 12, "mobile_columns": 1},
                "blocks": [
                    {
                        "block_id": "sector-heatmap",
                        "type": "heatmap",
                        "title": "板块-个股布局热力图",
                        "span": {"desktop": 7, "tablet": 12, "mobile": 1},
                        "height": "tall",
                        "data": {
                            "groups": [
                                {
                                    "name": "算力",
                                    "items": [
                                        {"code": "300308", "label": "中际旭创", "value": 98, "change": 8.24, "weight": 28},
                                        {"code": "000977", "label": "浪潮信息", "value": 90, "change": 6.12, "weight": 22},
                                        {"code": "300502", "label": "新易盛", "value": 84, "change": 5.46, "weight": 18},
                                        {"code": "300548", "label": "博创科技", "value": 71, "change": 3.22, "weight": 12},
                                    ],
                                },
                                {
                                    "name": "液冷",
                                    "items": [
                                        {"code": "002837", "label": "英维克", "value": 88, "change": 6.73, "weight": 20},
                                        {"code": "301018", "label": "申菱环境", "value": 72, "change": 4.68, "weight": 14},
                                        {"code": "300499", "label": "高澜股份", "value": 66, "change": 2.54, "weight": 10},
                                    ],
                                },
                                {
                                    "name": "服务器电源",
                                    "items": [
                                        {"code": "300827", "label": "上能电气", "value": 62, "change": 1.86, "weight": 10},
                                        {"code": "002851", "label": "麦格米特", "value": 58, "change": 0.92, "weight": 9},
                                        {"code": "688390", "label": "固德威", "value": 45, "change": -1.38, "weight": 8},
                                    ],
                                },
                            ]
                        },
                    },
                    {
                        "block_id": "focus-kline",
                        "type": "kline",
                        "title": "龙头 K 线",
                        "subtitle": "协议约定 candles = [time, open, close, low, high, volume]",
                        "span": {"desktop": 5, "tablet": 12, "mobile": 1},
                        "height": "tall",
                        "data": {
                            "symbol": "SZ300308",
                            "name": "中际旭创",
                            "candles": [
                                ["2026-03-03", 138.4, 141.2, 136.8, 142.1, 1250000],
                                ["2026-03-04", 141.0, 143.8, 140.4, 145.0, 1432000],
                                ["2026-03-05", 144.1, 142.9, 141.8, 145.4, 1185000],
                                ["2026-03-06", 143.0, 146.4, 142.3, 147.2, 1604000],
                                ["2026-03-07", 146.2, 149.3, 145.2, 150.1, 1718000],
                                ["2026-03-10", 149.0, 151.8, 148.2, 152.6, 1839000],
                                ["2026-03-11", 152.0, 150.6, 149.5, 153.4, 1586000],
                                ["2026-03-12", 150.8, 154.7, 150.4, 155.9, 2051000],
                            ],
                            "lines": {
                                "ma5": [
                                    ["2026-03-03", 140.2],
                                    ["2026-03-04", 141.7],
                                    ["2026-03-05", 142.6],
                                    ["2026-03-06", 143.4],
                                    ["2026-03-07", 144.7],
                                    ["2026-03-10", 146.8],
                                    ["2026-03-11", 148.2],
                                    ["2026-03-12", 150.6],
                                ],
                                "ma10": [
                                    ["2026-03-03", 137.9],
                                    ["2026-03-04", 138.8],
                                    ["2026-03-05", 139.7],
                                    ["2026-03-06", 140.6],
                                    ["2026-03-07", 141.9],
                                    ["2026-03-10", 143.2],
                                    ["2026-03-11", 144.4],
                                    ["2026-03-12", 146.1],
                                ],
                            },
                        },
                    },
                ],
            },
            {
                "section_id": "catalyst-chain",
                "section_kind": "catalyst_chain",
                "title": "催化流程图区",
                "description": "后端可以把事件推演、政策链、产业链传导直接输出为流程图源码，前端渲染或兜底显示。",
                "layout": {"desktop_columns": 12, "mobile_columns": 1},
                "blocks": [
                    {
                        "block_id": "flowchart-main",
                        "type": "flowchart",
                        "title": "算力主线推演",
                        "span": {"desktop": 12, "tablet": 12, "mobile": 1},
                        "data": {
                            "engine": "mermaid",
                            "source": "\nflowchart LR\n  A[海外训练集群扩容] --> B[光模块需求提升]\n  A --> C[服务器资本开支提升]\n  C --> D[液冷与电源链景气度抬升]\n  B --> E[龙头股先强化]\n  D --> F[细分支扩散]\n  E --> G[市场把主线重新定价]\n  F --> G\n".strip(),
                        },
                    },
                ],
            },
        ],
    }


def _build_stock_deep_dive_protocol_demo_payload():
    return {
        "version": "1.0",
        "page_id": "stock-deep-dive-demo-600519",
        "page_type": "stock_deep_dive",
        "title": "贵州茅台深度分析",
        "subtitle": "统一 section/block 协议下的单股票投顾式页面示例。",
        "as_of": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "theme": {
            "accent": "#9e3d2f",
            "tone": "warm"
        },
        "summary": {
            "market_phase": "rebound",
            "tags": ["白酒龙头", "改革验证", "资金回流"]
        },
        "sections": [
            {
                "section_id": "market-overview",
                "section_kind": "market_overview",
                "title": "行情概览",
                "description": "用指标带、洞察卡片和结构化文本先把个股当前所处阶段定下来。",
                "layout": {"desktop_columns": 12, "mobile_columns": 1},
                "blocks": [
                    {
                        "block_id": "market-metrics",
                        "type": "metric_strip",
                        "title": "核心快照",
                        "span": {"desktop": 12, "tablet": 12, "mobile": 1},
                        "height": "compact",
                        "data": {
                            "items": [
                                {"label": "现价", "value": "1460.18", "change": "+3.29%"},
                                {"label": "成交额", "value": "87.1亿", "change": "放量"},
                                {"label": "主力净流入", "value": "9.9亿", "change": "增强"},
                                {"label": "阶段", "value": "估值修复", "change": ""}
                            ]
                        }
                    },
                    {
                        "block_id": "thesis-cards",
                        "type": "insight_cards",
                        "title": "投资判断",
                        "span": {"desktop": 6, "tablet": 12, "mobile": 1},
                        "height": "normal",
                        "data": {
                            "items": [
                                {"title": "阶段判断", "text": "估值修复与改革验证并行，短线情绪与中期逻辑开始共振。", "tone": "accent"},
                                {"title": "核心支撑", "text": "资金回流、渠道改革推进、机构观点一致强化。", "tone": "neutral"},
                                {"title": "最大不确定性", "text": "淡季动销持续性与批价稳定性仍需继续验证。", "tone": "warning"}
                            ]
                        }
                    },
                    {
                        "block_id": "market-brief",
                        "type": "structured_text",
                        "title": "结论摘要",
                        "span": {"desktop": 6, "tablet": 12, "mobile": 1},
                        "height": "normal",
                        "data": {
                            "lead": "股价放量反弹，资金与研报形成共振，但当前仍属于修复期而非完全确认期。",
                            "paragraphs": [
                                "短线表现强于近期平均水平，量价关系改善，主力资金回流信号明确。",
                                "基本面催化来自渠道改革与 i 茅台新政，但后续还需要销量和批价继续验证。"
                            ],
                            "bullets": [
                                {"label": "支撑", "text": "资金面显著改善"},
                                {"label": "支撑", "text": "机构一致预期偏正向"},
                                {"label": "风险", "text": "节后淡季验证压力仍在"}
                            ]
                        }
                    }
                ]
            },
            {
                "section_id": "market-kline",
                "section_kind": "market_kline",
                "title": "K线与技术结构",
                "description": "K线图和技术要点分开展示，前端保持统一卡片化渲染。",
                "layout": {"desktop_columns": 12, "mobile_columns": 1},
                "blocks": [
                    {
                        "block_id": "daily-kline",
                        "type": "kline",
                        "title": "日线结构",
                        "span": {"desktop": 8, "tablet": 12, "mobile": 1},
                        "height": "tall",
                        "data": {
                            "symbol": "SH600519",
                            "name": "贵州茅台",
                            "candles": [
                                ["2026-03-10", 1412, 1418, 1401, 1423, 420000],
                                ["2026-03-11", 1419, 1413, 1408, 1428, 390000],
                                ["2026-03-12", 1410, 1426, 1407, 1433, 450000],
                                ["2026-03-13", 1425, 1438, 1420, 1442, 470000],
                                ["2026-03-16", 1420, 1460, 1420, 1466, 600860]
                            ],
                            "lines": {
                                "ma5": [["2026-03-16", 1413.54]],
                                "ma10": [["2026-03-16", 1409.31]]
                            }
                        }
                    },
                    {
                        "block_id": "technical-summary",
                        "type": "structured_text",
                        "title": "技术观察",
                        "span": {"desktop": 4, "tablet": 12, "mobile": 1},
                        "height": "tall",
                        "data": {
                            "lead": "技术面从弱修复转向试探性转强。",
                            "bullets": [
                                {"label": "均线", "text": "重新站上 MA5 和 MA10"},
                                {"label": "动能", "text": "MACD 绿柱缩短，空头动能减弱"},
                                {"label": "强弱", "text": "RSI 回升至中性区域"}
                            ]
                        }
                    }
                ]
            },
            {
                "section_id": "capital-flow",
                "section_kind": "capital_flow",
                "title": "资金面",
                "description": "资金区统一使用指标带加多标签卡片，不再依赖单独模板。",
                "layout": {"desktop_columns": 12, "mobile_columns": 1},
                "blocks": [
                    {
                        "block_id": "funds-strip",
                        "type": "metric_strip",
                        "title": "资金快照",
                        "span": {"desktop": 12, "tablet": 12, "mobile": 1},
                        "height": "compact",
                        "data": {
                            "items": [
                                {"label": "主力净流入", "value": "9.9亿", "change": "增强"},
                                {"label": "大单净占比", "value": "11.37%", "change": ""},
                                {"label": "行业净流入", "value": "3.37亿", "change": ""},
                                {"label": "近5日主力", "value": "5.97亿", "change": "转正"}
                            ]
                        }
                    },
                    {
                        "block_id": "funds-tabs",
                        "type": "tabs_panel",
                        "title": "资金拆解",
                        "span": {"desktop": 12, "tablet": 12, "mobile": 1},
                        "height": "normal",
                        "data": {
                            "tabs": [
                                {
                                    "label": "主力资金",
                                    "paragraph": "主力净流入显著，大单买入强于卖出，机构回补特征明显。",
                                    "bullets": [
                                        {"label": "大单", "text": "买入 27.3 亿，卖出 17.4 亿"},
                                        {"label": "结论", "text": "主导资金重新偏多"}
                                    ]
                                },
                                {
                                    "label": "行业资金",
                                    "paragraph": "饮料制造行业同步获资金净流入，板块并非孤立表现。",
                                    "bullets": []
                                },
                                {
                                    "label": "资金新闻",
                                    "paragraph": "资金相关资讯可作为辅助验证，但不单独替代结构化资金信号。",
                                    "bullets": []
                                }
                            ]
                        }
                    }
                ]
            },
            {
                "section_id": "research-view",
                "section_kind": "research_prediction",
                "title": "研报观点",
                "description": "研报区以表格和摘要卡组合呈现，不固定成单一模板。",
                "layout": {"desktop_columns": 12, "mobile_columns": 1},
                "blocks": [
                    {
                        "block_id": "research-summary",
                        "type": "structured_text",
                        "title": "一致预期",
                        "span": {"desktop": 4, "tablet": 12, "mobile": 1},
                        "height": "normal",
                        "data": {
                            "lead": "机构观点整体一致偏正向，但短期仍强调验证节奏。",
                            "bullets": [
                                {"label": "核心", "text": "市场化改革改善渠道效率"},
                                {"label": "节奏", "text": "拐点逻辑仍需经营数据印证"}
                            ]
                        }
                    },
                    {
                        "block_id": "research-table",
                        "type": "table",
                        "title": "近期机构观点",
                        "span": {"desktop": 8, "tablet": 12, "mobile": 1},
                        "height": "normal",
                        "data": {
                            "columns": [
                                {"key": "institution", "label": "机构"},
                                {"key": "rating", "label": "评级"},
                                {"key": "view", "label": "核心观点"}
                            ],
                            "rows": [
                                {"institution": "长江证券", "rating": "买入", "view": "渠道新政落地，改革红利释放"},
                                {"institution": "华创证券", "rating": "强推", "view": "i 茅台热销验证真实需求"},
                                {"institution": "中金公司", "rating": "跑赢行业", "view": "价格筑底信号渐明"}
                            ]
                        }
                    }
                ]
            },
            {
                "section_id": "news-catalyst",
                "section_kind": "news_catalyst",
                "title": "新闻催化",
                "description": "新闻催化区用卡片化文本承接事件列表和类型判断。",
                "layout": {"desktop_columns": 12, "mobile_columns": 1},
                "blocks": [
                    {
                        "block_id": "news-cards",
                        "type": "insight_cards",
                        "title": "事件链",
                        "span": {"desktop": 12, "tablet": 12, "mobile": 1},
                        "height": "normal",
                        "data": {
                            "items": [
                                {"title": "渠道改革", "text": "非标产品全面实行代销制，渠道利润模式调整。", "tone": "accent"},
                                {"title": "i 茅台运营", "text": "月活和订单量显示 C 端触达能力增强。", "tone": "neutral"},
                                {"title": "批价验证", "text": "批价企稳是改革是否真正兑现的关键观察项。", "tone": "warning"}
                            ]
                        }
                    }
                ]
            },
            {
                "section_id": "risk-watch",
                "section_kind": "risk_watch",
                "title": "风险与观察",
                "description": "风险区统一用结构化文本，不额外造新模板。",
                "layout": {"desktop_columns": 12, "mobile_columns": 1},
                "blocks": [
                    {
                        "block_id": "risk-text",
                        "type": "structured_text",
                        "title": "继续观察",
                        "span": {"desktop": 12, "tablet": 12, "mobile": 1},
                        "height": "normal",
                        "data": {
                            "lead": "当前更像修复交易，不宜把短期反弹直接等同于趋势完全确认。",
                            "bullets": [
                                {"label": "风险", "text": "淡季动销若走弱，批价可能再次承压"},
                                {"label": "风险", "text": "宏观恢复不及预期会影响高端消费场景"},
                                {"label": "观察", "text": "主力资金能否在后续几日持续净流入"}
                            ]
                        }
                    }
                ]
            }
        ]
    }


@app.route("/report_protocol_demo", methods=["GET"])
def report_protocol_demo():
    return render_template(
        "report_protocol_demo.html",
        payload_api_url="api/report_protocol_demo",
    )


@app.route("/api/report_protocol_demo", methods=["GET"])
def api_report_protocol_demo():
    payload = _build_report_protocol_demo_payload()
    return jsonify(_to_json_safe(payload))


@app.route("/report_protocol_stock_demo", methods=["GET"])
def report_protocol_stock_demo():
    return render_template(
        "report_protocol_demo.html",
        payload_api_url="api/report_protocol_stock_demo",
    )


@app.route("/api/report_protocol_stock_demo", methods=["GET"])
def api_report_protocol_stock_demo():
    payload = _build_stock_deep_dive_protocol_demo_payload()
    return jsonify(_to_json_safe(payload))


def run_server():
    port = int(os.environ.get("FIN_AGENT_PORT") or 22053)
    use_reloader = str(os.environ.get("FIN_AGENT_FLASK_RELOADER") or "").strip().lower() in {"1", "true", "yes"}
    app.run(debug=True, host="0.0.0.0", port=port, use_reloader=use_reloader)


if __name__ == "__main__":
    run_server()
