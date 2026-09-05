import datetime
from typing import Any, Dict, Optional

from src.services.application_runtime_service import ApplicationRuntimeService
from src.services.task_service import AsyncTaskService
from src.services.skill_studio_service import SkillStudioService


class AgentExecutionError(ValueError):
    pass


class AgentExecutionService:
    def __init__(
        self,
        *,
        application_runtime_service: Optional[ApplicationRuntimeService] = None,
        skill_studio_service: Optional[SkillStudioService] = None,
        async_task_service: Optional[AsyncTaskService] = None,
    ) -> None:
        self.application_runtime_service = application_runtime_service or ApplicationRuntimeService()
        self.skill_studio_service = skill_studio_service or SkillStudioService()
        self.async_task_service = async_task_service or AsyncTaskService()

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def build_execution_context(
        self,
        *,
        application_name: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        normalized_application = self._trim(application_name) or "investment_workbench"
        incoming_context = context if isinstance(context, dict) else {}
        app_ctx = self.application_runtime_service.get_application_context(normalized_application)
        route_context = {
            **self.application_runtime_service.build_route_context(normalized_application),
            **incoming_context,
        }
        default_agent = app_ctx.get("default_agent") if isinstance(app_ctx.get("default_agent"), dict) else {}
        selected_agent_name = self._trim(incoming_context.get("agent_name") or incoming_context.get("selected_agent"))
        agent_name = selected_agent_name or self._trim(default_agent.get("agent_name"))
        selected_agent = self._resolve_selected_agent(app_ctx, agent_name)
        if selected_agent:
            route_context["agent_name"] = self._trim(selected_agent.get("agent_name"))
            route_context["allowed_skills"] = [self._trim(x) for x in selected_agent.get("skills", []) if self._trim(x)]
            route_context["allowed_tools"] = [self._trim(x) for x in selected_agent.get("tools", []) if self._trim(x)]
        return {
            "application_name": normalized_application,
            "application_context": app_ctx,
            "default_agent": default_agent,
            "route_context": route_context,
            "agent_name": agent_name,
            "agent_runtime_profile": (
                selected_agent.get("runtime_profile")
                if isinstance((selected_agent or {}).get("runtime_profile"), dict)
                else {}
            ),
        }

    def _resolve_selected_agent(self, app_ctx: Dict[str, Any], agent_name: str) -> Dict[str, Any]:
        normalized = self._trim(agent_name)
        if not normalized:
            return {}
        for item in app_ctx.get("available_agents") or []:
            if not isinstance(item, dict):
                continue
            if self._trim(item.get("agent_name")) == normalized:
                return item
        default_agent = app_ctx.get("default_agent") if isinstance(app_ctx.get("default_agent"), dict) else {}
        if self._trim(default_agent.get("agent_name")) == normalized:
            return default_agent
        return {}

    def preview_route(
        self,
        *,
        user_text: str,
        application_name: str,
        context: Optional[Dict[str, Any]] = None,
        execution_profile: str = "real",
    ) -> Dict[str, Any]:
        normalized_text = self._trim(user_text)
        if not normalized_text:
            raise AgentExecutionError("user_text 不能为空")
        exec_ctx = self.build_execution_context(application_name=application_name, context=context)
        route_snapshot = self.skill_studio_service.preview_intent_route(
            user_text=normalized_text,
            context=exec_ctx["route_context"],
        )
        route = route_snapshot.get("route") if isinstance(route_snapshot.get("route"), dict) else {}
        route_type = self._trim(route.get("route_type"))
        payload: Dict[str, Any] = {
            "ok": True,
            "application_name": exec_ctx["application_name"],
            "agent_name": exec_ctx["agent_name"],
            "route_snapshot": route_snapshot,
        }
        if route_type == "single_skill":
            selected_skill = self._trim(route.get("selected_skill"))
            if selected_skill:
                try:
                    input_payload = self._with_execution_profile(
                        self.build_skill_input_from_route_snapshot(route_snapshot, selected_skill),
                        execution_profile,
                    )
                    payload["input_payload"] = input_payload
                    payload["skill_debug"] = self.build_tool_argument_preview_for_skill(
                        selected_skill,
                        input_payload,
                    )
                except AgentExecutionError as exc:
                    payload["input_error"] = str(exc)
        elif route_type == "multi_skill":
            composite = route_snapshot.get("composite_execution_plan") or {}
            launchables = []
            for step in composite.get("steps") or []:
                skill_name = self._trim(step.get("skill_name"))
                if not skill_name:
                    continue
                try:
                    input_payload = self._with_execution_profile(
                        self.build_skill_input_from_route_snapshot(route_snapshot, skill_name),
                        execution_profile,
                    )
                    launchables.append(
                        {
                            "step_id": self._trim(step.get("step_id")),
                            "skill_name": skill_name,
                            "goal": self._trim(step.get("goal")),
                            "depends_on": step.get("depends_on") or [],
                            "input_payload": input_payload,
                            "skill_debug": self.build_tool_argument_preview_for_skill(
                                skill_name,
                                input_payload,
                            ),
                        }
                    )
                except Exception as exc:
                    launchables.append(
                        {
                            "step_id": self._trim(step.get("step_id")),
                            "skill_name": skill_name,
                            "goal": self._trim(step.get("goal")),
                            "depends_on": step.get("depends_on") or [],
                            "launch_error": str(exc),
                        }
                    )
            payload["launchables"] = launchables
        return payload

    def submit_routed_task(
        self,
        *,
        user_text: str,
        application_name: str,
        context: Optional[Dict[str, Any]] = None,
        execution_profile: str = "real",
        source_type: str = "router_studio",
    ) -> Dict[str, Any]:
        normalized_text = self._trim(user_text)
        if not normalized_text:
            raise AgentExecutionError("user_text 不能为空")
        exec_ctx = self.build_execution_context(application_name=application_name, context=context)
        route_snapshot = self.skill_studio_service.preview_intent_route(
            user_text=normalized_text,
            context=exec_ctx["route_context"],
        )
        route = route_snapshot.get("route") if isinstance(route_snapshot.get("route"), dict) else {}
        route_type = self._trim(route.get("route_type"))
        if route_type == "clarify":
            return {
                "ok": True,
                "application_name": exec_ctx["application_name"],
                "agent_name": exec_ctx["agent_name"],
                "submit_status": "clarify",
                "route_snapshot": route_snapshot,
                "clarification_state": route_snapshot.get("clarification_state"),
            }
        if route_type == "multi_skill":
            composite = route_snapshot.get("composite_execution_plan") or {}
            launchables = []
            for step in composite.get("steps") or []:
                skill_name = self._trim(step.get("skill_name"))
                if not skill_name:
                    continue
                try:
                    input_payload = self._with_execution_profile(
                        self.build_skill_input_from_route_snapshot(route_snapshot, skill_name),
                        execution_profile,
                    )
                    launchables.append(
                        {
                            "step_id": self._trim(step.get("step_id")),
                            "skill_name": skill_name,
                            "goal": self._trim(step.get("goal")),
                            "depends_on": step.get("depends_on") or [],
                            "input_payload": input_payload,
                            "skill_debug": self.build_tool_argument_preview_for_skill(
                                skill_name,
                                input_payload,
                            ),
                        }
                    )
                except Exception as exc:
                    launchables.append(
                        {
                            "step_id": self._trim(step.get("step_id")),
                            "skill_name": skill_name,
                            "goal": self._trim(step.get("goal")),
                            "depends_on": step.get("depends_on") or [],
                            "launch_error": str(exc),
                        }
                    )
            return {
                "ok": True,
                "application_name": exec_ctx["application_name"],
                "agent_name": exec_ctx["agent_name"],
                "submit_status": "planned",
                "route_snapshot": route_snapshot,
                "composite_execution_plan": composite,
                "launchables": launchables,
            }

        selected_skill = self._trim(route.get("selected_skill"))
        if not selected_skill:
            raise AgentExecutionError("路由结果缺少 selected_skill")
        try:
            input_payload = self._with_execution_profile(
                self.build_skill_input_from_route_snapshot(route_snapshot, selected_skill),
                execution_profile,
            )
        except AgentExecutionError as exc:
            return {
                "ok": True,
                "application_name": exec_ctx["application_name"],
                "agent_name": exec_ctx["agent_name"],
                "submit_status": "clarify",
                "route_snapshot": route_snapshot,
                "clarification_state": {
                    "reason": str(exc),
                    "required_fields": ["code_or_concept_or_name"],
                },
                "skill_name": selected_skill,
                "input_error": str(exc),
            }
        job = self.async_task_service.submit_skill_job(
            skill_name=selected_skill,
            input_payload=input_payload,
            max_steps=None,
            enable_think=False,
            execution_profile=self._trim(execution_profile or input_payload.get("_execution_profile")) or "real",
            source_type=self._trim(source_type) or "router_studio",
            route_snapshot=route_snapshot,
            application_name=exec_ctx["application_name"],
            agent_name=exec_ctx["agent_name"],
            agent_runtime_profile=exec_ctx["agent_runtime_profile"],
        )
        return {
            "ok": True,
            "application_name": exec_ctx["application_name"],
            "agent_name": exec_ctx["agent_name"],
            "submit_status": "submitted",
            "route_snapshot": route_snapshot,
            "job": job,
            "skill_name": selected_skill,
            "input_payload": input_payload,
            "skill_debug": self.build_tool_argument_preview_for_skill(selected_skill, input_payload),
        }

    def build_skill_input_from_route_snapshot(self, route_snapshot: Dict[str, Any], skill_name: str) -> Dict[str, Any]:
        normalized_skill = self._trim(skill_name)
        if normalized_skill == "stock_deep_dive":
            payload = self._build_stock_deep_dive_input_from_route_snapshot(route_snapshot)
            if not self._trim(payload.get("code")):
                raise AgentExecutionError("当前 stock_deep_dive 仍需标准股票代码；请补充 code 或先补齐证券解析能力。")
            return payload
        if normalized_skill == "hotspot_trace":
            return self._build_hotspot_trace_input_from_route_snapshot(route_snapshot)
        raise AgentExecutionError(f"暂不支持从路由快照构建 skill 输入: {normalized_skill}")

    def build_tool_argument_preview_for_skill(self, skill_name: str, input_payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.skill_studio_service.preview_tool_argument_plans(
            skill_name=self._trim(skill_name),
            input_payload=input_payload if isinstance(input_payload, dict) else {},
        )

    def _with_execution_profile(self, input_payload: Dict[str, Any], execution_profile: str = "") -> Dict[str, Any]:
        payload = dict(input_payload or {}) if isinstance(input_payload, dict) else {}
        profile = self._trim(execution_profile or payload.get("_execution_profile"))
        if profile:
            payload["_execution_profile"] = profile
        return payload

    def _build_stock_deep_dive_input_from_route_snapshot(self, route_snapshot: Dict[str, Any]) -> Dict[str, Any]:
        route = route_snapshot.get("route") if isinstance(route_snapshot, dict) else {}
        normalized = route.get("normalized_input") if isinstance(route, dict) else {}
        code = self._trim((normalized or {}).get("code"))
        name = self._trim((normalized or {}).get("name"))
        question = self._trim((normalized or {}).get("question"))
        time_range = (normalized or {}).get("time_range") if isinstance((normalized or {}).get("time_range"), dict) else {}
        as_of_date = self._trim(time_range.get("end_date")) or datetime.datetime.now().strftime("%Y-%m-%d")
        return {
            "task_type": "stock_deep_dive",
            "code": code,
            "name": name,
            "runtime_mode": "interactive",
            "question": question or f"请从行情、资金、研报、新闻和风险角度，对{(name or code)}做一份专业、克制的投顾式分析。",
            "context": {
                "focus": "全面分析",
                "as_of_date": as_of_date,
            },
        }

    def _build_hotspot_trace_input_from_route_snapshot(self, route_snapshot: Dict[str, Any]) -> Dict[str, Any]:
        route = route_snapshot.get("route") if isinstance(route_snapshot, dict) else {}
        normalized = route.get("normalized_input") if isinstance(route, dict) else {}
        concept = self._trim((normalized or {}).get("concept"))
        code = self._trim((normalized or {}).get("code"))
        name = self._trim((normalized or {}).get("name"))
        question = self._trim((normalized or {}).get("question"))
        time_range = (normalized or {}).get("time_range") if isinstance((normalized or {}).get("time_range"), dict) else {}
        trigger_date = self._trim(time_range.get("end_date")) or datetime.datetime.now().strftime("%Y-%m-%d")
        trace_type = "manual"
        if concept:
            trace_type = "concept"
        elif code or name:
            trace_type = "abnormal"
        trace_key = concept or code or name
        if not trace_key:
            raise AgentExecutionError("当前热点追踪缺少 trace_key")
        return {
            "task_type": "hotspot_trace",
            "trace_type": trace_type,
            "trace_key": trace_key,
            "title": f"{trace_key} 热点追踪",
            "runtime_mode": "interactive",
            "question": question or f"请分析 {trace_key} 的起因、持续性、证据链和风险。",
            "candidate_context": {
                **({"concept": concept} if concept else {}),
                **({"code": code} if code else {}),
                **({"company": name, "name": name} if name else {}),
                "trigger_date": trigger_date,
            },
            "incremental_meta": {
                "new_urls_count": 0,
                "new_content_chars": 0,
                "latest_news_time": "",
            },
        }
