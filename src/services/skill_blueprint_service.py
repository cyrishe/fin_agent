import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.prompting.prompt_registry import get_prompt_registry
from src.services.display_contract_compiler import DisplayContractCompiler
from src.skill_runtime.step_budget import resolve_step_budget
from src.services.skill_studio_service import SkillStudioService
from src.utils.ai_service import chat_qwen_json


class SkillBlueprintError(ValueError):
    pass


class SkillBlueprintService:
    def __init__(self, *, skill_studio_service: Optional[SkillStudioService] = None) -> None:
        self.skill_studio_service = skill_studio_service or SkillStudioService()
        self.display_contract_compiler = DisplayContractCompiler()
        self.tool_hub_path = Path("src/tools/tool_hub.json")

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def generate_bundle(
        self,
        *,
        skill_name: str,
        requirement_text: str,
        selected_tools: Optional[List[str]] = None,
        selected_skills: Optional[List[str]] = None,
        application_name: str = "",
        agent_name: str = "",
    ) -> Dict[str, Any]:
        normalized_name = self._trim(skill_name)
        if not normalized_name:
            raise SkillBlueprintError("skill_name 不能为空")
        normalized_requirement = self._trim(requirement_text)
        if not normalized_requirement:
            raise SkillBlueprintError("requirement_text 不能为空")

        template = self.skill_studio_service.build_skill_template_bundle(normalized_name)
        tool_names = [self._trim(x) for x in (selected_tools or []) if self._trim(x)]
        skill_refs = [self._trim(x) for x in (selected_skills or []) if self._trim(x)]
        available_tool_catalog = self._load_available_tool_catalog()
        seeded_display_contract = self.display_contract_compiler.compile(
            selected_tools=tool_names,
            requirement_text=normalized_requirement,
            skill_name=normalized_name,
        )

        llm_plan = self._generate_with_llm(
            mode="create",
            skill_name=normalized_name,
            requirement_text=normalized_requirement,
            selected_tools=tool_names,
            selected_skills=skill_refs,
            application_name=application_name,
            agent_name=agent_name,
            display_contract_seed=seeded_display_contract,
            available_tool_catalog=available_tool_catalog,
        )
        display_contract = self.display_contract_compiler.merge_with_llm_plan(seeded_display_contract, llm_plan)
        resolved_tools = self._resolve_tools(
            selected_tools=tool_names,
            llm_plan=llm_plan,
            available_tool_catalog=available_tool_catalog,
            requirement_text=normalized_requirement,
        )

        skill_md = self._build_skill_md(
            skill_name=normalized_name,
            requirement_text=normalized_requirement,
            selected_tools=resolved_tools,
            selected_skills=skill_refs,
            llm_plan=llm_plan,
            display_contract=display_contract,
        )
        skill_config = self._build_skill_config(
            selected_tools=resolved_tools,
            requirement_text=normalized_requirement,
            application_name=application_name,
            agent_name=agent_name,
            llm_plan=llm_plan,
            display_contract=display_contract,
        )
        output_schema = self._build_output_schema(normalized_name, llm_plan=llm_plan, display_contract=display_contract)

        template["files"]["skill_md_text"] = skill_md
        template["files"]["skill_config_text"] = json.dumps(skill_config, ensure_ascii=False, indent=2)
        template["files"]["output_schema_text"] = json.dumps(output_schema, ensure_ascii=False, indent=2)
        template["files"]["skill_config"] = skill_config
        template["files"]["output_schema"] = output_schema
        template["meta"]["blueprint"] = {
            "mode": "create",
            "requirement_text": normalized_requirement,
            "selected_tools": resolved_tools,
            "selected_skills": skill_refs,
            "application_name": self._trim(application_name),
            "agent_name": self._trim(agent_name),
            "llm_enabled": llm_plan is not None,
        }
        return template

    def refine_bundle(
        self,
        *,
        source_skill_name: str,
        target_skill_name: str,
        refinement_text: str,
        selected_tools: Optional[List[str]] = None,
        selected_skills: Optional[List[str]] = None,
        application_name: str = "",
        agent_name: str = "",
    ) -> Dict[str, Any]:
        normalized_source = self._trim(source_skill_name)
        normalized_target = self._trim(target_skill_name)
        normalized_refinement = self._trim(refinement_text)
        if not normalized_source:
            raise SkillBlueprintError("source_skill_name 不能为空")
        if not normalized_target:
            raise SkillBlueprintError("target_skill_name 不能为空")
        if not normalized_refinement:
            raise SkillBlueprintError("refinement_text 不能为空")

        source_bundle = self.skill_studio_service.load_skill_bundle(normalized_source)
        source_files = source_bundle.get("files") if isinstance(source_bundle, dict) else {}
        source_config = source_files.get("skill_config") if isinstance(source_files, dict) else {}
        if not isinstance(source_config, dict):
            source_config = {}
        source_display_contract = source_config.get("display_contract") if isinstance(source_config.get("display_contract"), dict) else {}
        available_tool_catalog = self._load_available_tool_catalog()

        template = self.skill_studio_service.build_skill_template_bundle(normalized_target)
        inherited_tools = [self._trim(x) for x in source_config.get("tools", []) if self._trim(x)]
        refined_tools = [self._trim(x) for x in (selected_tools or inherited_tools) if self._trim(x)]
        refined_skills = [self._trim(x) for x in (selected_skills or [normalized_source]) if self._trim(x)]
        seeded_display_contract = self.display_contract_compiler.compile(
            selected_tools=refined_tools,
            requirement_text=normalized_refinement,
            skill_name=normalized_target,
            preferred_page_type=self._trim(source_config.get("expected_render_page_type")),
            existing_display_contract=source_display_contract,
        )
        llm_plan = self._generate_with_llm(
            mode="refine",
            skill_name=normalized_target,
            requirement_text=normalized_refinement,
            selected_tools=refined_tools,
            selected_skills=refined_skills,
            application_name=application_name,
            agent_name=agent_name,
            source_skill_name=normalized_source,
            source_skill_bundle=source_bundle,
            display_contract_seed=seeded_display_contract,
            available_tool_catalog=available_tool_catalog,
        )
        display_contract = self.display_contract_compiler.merge_with_llm_plan(seeded_display_contract, llm_plan)
        resolved_tools = self._resolve_tools(
            selected_tools=refined_tools,
            llm_plan=llm_plan,
            available_tool_catalog=available_tool_catalog,
            requirement_text=normalized_refinement,
        )

        skill_md = self._build_refined_skill_md(
            target_skill_name=normalized_target,
            source_skill_name=normalized_source,
            source_skill_md=str(source_files.get("skill_md_text") or ""),
            refinement_text=normalized_refinement,
            selected_tools=resolved_tools,
            selected_skills=refined_skills,
            llm_plan=llm_plan,
            display_contract=display_contract,
        )
        skill_config = self._build_refined_skill_config(
            source_skill_name=normalized_source,
            source_skill_config=source_config,
            selected_tools=resolved_tools,
            refinement_text=normalized_refinement,
            application_name=application_name,
            agent_name=agent_name,
            llm_plan=llm_plan,
            display_contract=display_contract,
        )
        output_schema = self._build_refined_output_schema(
            target_skill_name=normalized_target,
            source_output_schema=source_files.get("output_schema") if isinstance(source_files.get("output_schema"), dict) else {},
            llm_plan=llm_plan,
            display_contract=display_contract,
        )

        template["files"]["skill_md_text"] = skill_md
        template["files"]["skill_config_text"] = json.dumps(skill_config, ensure_ascii=False, indent=2)
        template["files"]["output_schema_text"] = json.dumps(output_schema, ensure_ascii=False, indent=2)
        template["files"]["skill_config"] = skill_config
        template["files"]["output_schema"] = output_schema
        template["meta"]["blueprint"] = {
            "mode": "refine",
            "source_skill_name": normalized_source,
            "refinement_text": normalized_refinement,
            "selected_tools": resolved_tools,
            "selected_skills": refined_skills,
            "application_name": self._trim(application_name),
            "agent_name": self._trim(agent_name),
            "llm_enabled": llm_plan is not None,
        }
        return template

    def _build_skill_md(
        self,
        *,
        skill_name: str,
        requirement_text: str,
        selected_tools: List[str],
        selected_skills: List[str],
        llm_plan: Optional[Dict[str, Any]] = None,
        display_contract: Optional[Dict[str, Any]] = None,
    ) -> str:
        title = self._trim((llm_plan or {}).get("title")) or skill_name.replace("_", " ").strip().title()
        short_description = self._trim((llm_plan or {}).get("description")) or requirement_text
        tool_lines = [f"- `{name}`" for name in selected_tools] or ["- 暂未指定；后续在 skill.json 中补齐"]
        skill_lines = [f"- `{name}`" for name in selected_skills] or ["- 无"]
        steps = self._extract_llm_steps(llm_plan) or self._derive_steps(requirement_text, selected_tools)
        goal_points = self._extract_llm_goal_points(llm_plan) or self._derive_goal_points(requirement_text)
        usage_principles = self._extract_llm_usage_principles(llm_plan) or self._derive_usage_principles(selected_tools)
        success_checks = self._extract_llm_success_checks(llm_plan) or self._derive_success_checks(selected_tools)
        step_lines = [f"- {line}" for line in steps]
        goal_lines = [f"- {line}" for line in goal_points] or [f"- {short_description}"]
        principle_lines = [f"- {line}" for line in usage_principles]
        check_lines = [f"{idx}. {line}" for idx, line in enumerate(success_checks, start=1)]
        output_lines = self.display_contract_compiler.build_skill_md_output_contract(display_contract or {})
        return "\n".join(
            [
                "---",
                f"name: {skill_name}",
                f"description: {self._single_line(short_description)}",
                "---",
                "",
                f"# {title}",
                "",
                "## 目标",
                "",
                short_description,
                "",
                "## 目标拆解",
                "",
                *goal_lines,
                "",
                "## 角色定位",
                "",
                "你是一名面向该任务的业务分析代理，要求先事实、后判断，优先引用工具证据，不虚构数据。",
                "",
                "## 可复用 assets",
                "",
                "### 引用的 skills",
                "",
                *skill_lines,
                "",
                "### 允许使用的 tools",
                "",
                *tool_lines,
                "",
                "使用原则：",
                "",
                *principle_lines,
                "",
                "## 核心分析框架",
                "",
                *step_lines,
                "",
                "## 输出要求",
                "",
                "- 输出结论时先给事实，再给判断，再给风险和后续观察点。",
                "- 若存在 render_payload，请保持结构化和可审计。",
                "- 不得虚构工具结果或未调用到的数据。",
                "",
                *output_lines,
                "## 最终检查",
                "",
                *check_lines,
                "",
            ]
        ).strip() + "\n"

    def _build_skill_config(
        self,
        *,
        selected_tools: List[str],
        requirement_text: str,
        application_name: str = "",
        agent_name: str = "",
        llm_plan: Optional[Dict[str, Any]] = None,
        display_contract: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        plan = llm_plan or {}
        selected_set = {self._trim(x) for x in selected_tools if self._trim(x)}
        planned_required_tools = [self._trim(x) for x in plan.get("required_tools", []) if self._trim(x)]
        required_tools = [name for name in planned_required_tools if name in selected_set] or selected_tools
        tool_mode = self._trim(plan.get("tool_policy_mode")) or "strict"
        default_max_steps = int(plan.get("default_max_steps") or self._recommend_max_steps(requirement_text, selected_tools))
        default_max_steps = resolve_step_budget(
            base_max_steps=default_max_steps,
            tool_mode=tool_mode,
            selected_tools=selected_tools,
            required_tools_before_final=required_tools,
        )
        return {
            "status": "draft",
            "created_from": "blueprint",
            "blueprint_context": {
                "application_name": self._trim(application_name),
                "agent_name": self._trim(agent_name),
                "llm_enabled": llm_plan is not None,
            },
            "tool_policy": {"mode": tool_mode},
            "tools": selected_tools,
            "required_tools_before_final": required_tools,
            "default_max_steps": default_max_steps,
            "expected_render_page_type": self._trim(plan.get("expected_render_page_type")),
            "enable_think": bool(plan.get("enable_think", False)),
            "display_contract": display_contract or {},
        }

    def _build_refined_skill_config(
        self,
        *,
        source_skill_name: str,
        source_skill_config: Dict[str, Any],
        selected_tools: List[str],
        refinement_text: str,
        application_name: str = "",
        agent_name: str = "",
        llm_plan: Optional[Dict[str, Any]] = None,
        display_contract: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        base = dict(source_skill_config or {})
        plan = llm_plan or {}
        tool_policy = dict(base.get("tool_policy") or {})
        tool_policy["mode"] = self._trim(plan.get("tool_policy_mode")) or self._trim(tool_policy.get("mode")) or "strict"
        expected_render_page_type = self._trim(plan.get("expected_render_page_type")) or self._trim(base.get("expected_render_page_type"))
        selected_set = {self._trim(x) for x in selected_tools if self._trim(x)}
        planned_required_tools = [self._trim(x) for x in plan.get("required_tools", []) if self._trim(x)]
        inherited_required_tools = [self._trim(x) for x in base.get("required_tools_before_final", []) if self._trim(x)]
        required_tools = [name for name in planned_required_tools if not selected_set or name in selected_set] or selected_tools or inherited_required_tools
        default_max_steps = int(
            plan.get("default_max_steps")
            or self._recommend_max_steps(
                refinement_text,
                selected_tools or [self._trim(x) for x in base.get("tools", []) if self._trim(x)],
            )
        )
        default_max_steps = resolve_step_budget(
            base_max_steps=default_max_steps,
            tool_mode=str(tool_policy.get("mode") or "strict").strip(),
            selected_tools=selected_tools,
            required_tools_before_final=required_tools,
        )
        return {
            **base,
            "status": "draft",
            "created_from": "refine_blueprint",
            "blueprint_context": {
                "application_name": self._trim(application_name),
                "agent_name": self._trim(agent_name),
                "source_skill_name": self._trim(source_skill_name),
                "refinement_text": self._trim(refinement_text),
                "llm_enabled": llm_plan is not None,
            },
            "tool_policy": tool_policy,
            "tools": selected_tools,
            "required_tools_before_final": required_tools,
            "default_max_steps": default_max_steps,
            "expected_render_page_type": expected_render_page_type,
            "enable_think": bool(plan.get("enable_think", base.get("enable_think", False))),
            "display_contract": display_contract or base.get("display_contract") or {},
        }

    def _build_output_schema(
        self,
        skill_name: str,
        *,
        llm_plan: Optional[Dict[str, Any]] = None,
        display_contract: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        normalized_name = self._trim(skill_name) or "generated_skill"
        plan = llm_plan or {}
        required_fields = [self._trim(x) for x in plan.get("required_fields", []) if self._trim(x)] or ["summary", "facts", "risks"]
        field_descriptions = plan.get("field_descriptions") if isinstance(plan.get("field_descriptions"), dict) else {}
        return self.display_contract_compiler.build_output_schema(
            skill_name=normalized_name,
            required_fields=required_fields,
            field_descriptions=field_descriptions,
            display_contract=display_contract or {},
        )

    def _build_refined_output_schema(
        self,
        *,
        target_skill_name: str,
        source_output_schema: Dict[str, Any],
        llm_plan: Optional[Dict[str, Any]] = None,
        display_contract: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if source_output_schema:
            schema = dict(source_output_schema)
            schema["title"] = f"{self._trim(target_skill_name) or 'refined_skill'} Output"
            if llm_plan:
                required_fields = [self._trim(x) for x in llm_plan.get("required_fields", []) if self._trim(x)]
                if required_fields:
                    schema["required"] = required_fields
            compiled = self._build_output_schema(target_skill_name, llm_plan=llm_plan, display_contract=display_contract)
            schema["$defs"] = compiled.get("$defs", schema.get("$defs", {}))
            properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
            compiled_properties = compiled.get("properties") if isinstance(compiled.get("properties"), dict) else {}
            for key in ("facts", "risks", "render_payload", "summary", "judgement"):
                if key in compiled_properties:
                    properties[key] = compiled_properties[key]
            schema["properties"] = properties
            return schema
        return self._build_output_schema(target_skill_name, llm_plan=llm_plan, display_contract=display_contract)

    def _derive_steps(self, requirement_text: str, selected_tools: List[str]) -> List[str]:
        normalized = re.sub(r"\s+", " ", requirement_text).strip()
        steps: List[str] = [
            f"先澄清任务目标与范围：{normalized}",
            "优先整理可直接验证的事实，不提前下结论。",
        ]
        if selected_tools:
            steps.append(f"按需调用这些 tools 获取证据：{', '.join(selected_tools)}。")
        else:
            steps.append("后续在 tool 选择完成后，再按证据优先级补齐工具调用顺序。")
        steps.extend(
            [
                "把工具结果归并成结构化证据，并明确哪些结论已验证、哪些仍待确认。",
                "最后输出摘要、关键事实、综合判断、主要风险。",
            ]
        )
        return steps

    def _derive_goal_points(self, requirement_text: str) -> List[str]:
        text = self._trim(requirement_text)
        if not text:
            return []
        parts = re.split(r"[；;。！？\n]+", text)
        goals = [self._single_line(part) for part in parts if self._trim(part)]
        return goals[:4]

    def _build_refined_skill_md(
        self,
        *,
        target_skill_name: str,
        source_skill_name: str,
        source_skill_md: str,
        refinement_text: str,
        selected_tools: List[str],
        selected_skills: List[str],
        llm_plan: Optional[Dict[str, Any]] = None,
        display_contract: Optional[Dict[str, Any]] = None,
    ) -> str:
        title = self._trim((llm_plan or {}).get("title")) or target_skill_name.replace("_", " ").strip().title()
        short_description = self._trim((llm_plan or {}).get("description")) or refinement_text
        tool_lines = [f"- `{name}`" for name in selected_tools] or ["- 保持原 skill 中已有 tools，后续再细调"]
        skill_lines = [f"- `{name}`" for name in selected_skills] or [f"- `{source_skill_name}`"]
        steps = self._extract_llm_steps(llm_plan) or self._derive_steps(refinement_text, selected_tools)
        goal_points = self._extract_llm_goal_points(llm_plan) or self._derive_goal_points(refinement_text)
        usage_principles = self._extract_llm_usage_principles(llm_plan) or self._derive_usage_principles(selected_tools)
        success_checks = self._extract_llm_success_checks(llm_plan) or self._derive_success_checks(selected_tools)
        step_lines = [f"- {line}" for line in steps]
        goal_lines = [f"- {line}" for line in goal_points] or ["- 在原 skill 基础上做结构化优化，不直接粘贴原始需求。"]
        principle_lines = [f"- {line}" for line in usage_principles]
        check_lines = [f"{idx}. {line}" for idx, line in enumerate(success_checks, start=1)]
        source_excerpt = self._extract_source_excerpt(source_skill_md)
        output_lines = self.display_contract_compiler.build_skill_md_output_contract(display_contract or {})
        return "\n".join(
            [
                "---",
                f"name: {target_skill_name}",
                f"description: {self._single_line(short_description)}",
                "---",
                "",
                f"# {title}",
                "",
                "## 来源",
                "",
                f"- 基于现有 skill：`{source_skill_name}`",
                "",
                "## 本次优化重点",
                "",
                *goal_lines,
                "",
                "## 原 skill 摘要",
                "",
                source_excerpt or "- 无可用摘要",
                "",
                "## 可复用 assets",
                "",
                "### 引用的 skills",
                "",
                *skill_lines,
                "",
                "### 允许使用的 tools",
                "",
                *tool_lines,
                "",
                "使用原则：",
                "",
                *principle_lines,
                "",
                "## 优化后的核心分析框架",
                "",
                *step_lines,
                "",
                "## 输出要求",
                "",
                "- 在继承原有能力的基础上，优先体现本次优化目标。",
                "- 输出结论时先给事实，再给判断，再给风险和后续观察点。",
                "- 不得虚构工具结果或未调用到的数据。",
                "",
                *output_lines,
                "## 最终检查",
                "",
                *check_lines,
            ]
        ).strip() + "\n"

    def _extract_source_excerpt(self, source_skill_md: str) -> str:
        lines = [line.rstrip() for line in str(source_skill_md or "").splitlines()]
        kept: List[str] = []
        for line in lines:
            if not line.strip():
                continue
            kept.append(line)
            if len(kept) >= 10:
                break
        return "\n".join(kept)

    def _extract_llm_steps(self, llm_plan: Optional[Dict[str, Any]]) -> List[str]:
        if not isinstance(llm_plan, dict):
            return []
        return [self._trim(x) for x in llm_plan.get("steps", []) if self._trim(x)]

    def _extract_llm_goal_points(self, llm_plan: Optional[Dict[str, Any]]) -> List[str]:
        if not isinstance(llm_plan, dict):
            return []
        return [self._trim(x) for x in llm_plan.get("goal_points", []) if self._trim(x)]

    def _extract_llm_usage_principles(self, llm_plan: Optional[Dict[str, Any]]) -> List[str]:
        if not isinstance(llm_plan, dict):
            return []
        return [self._trim(x) for x in llm_plan.get("usage_principles", []) if self._trim(x)]

    def _extract_llm_success_checks(self, llm_plan: Optional[Dict[str, Any]]) -> List[str]:
        if not isinstance(llm_plan, dict):
            return []
        return [self._trim(x) for x in llm_plan.get("success_checks", []) if self._trim(x)]

    def _single_line(self, text: str) -> str:
        return re.sub(r"\s+", " ", self._trim(text))

    def _generate_with_llm(
        self,
        *,
        mode: str,
        skill_name: str,
        requirement_text: str,
        selected_tools: List[str],
        selected_skills: List[str],
        application_name: str,
        agent_name: str,
        source_skill_name: str = "",
        source_skill_bundle: Optional[Dict[str, Any]] = None,
        display_contract_seed: Optional[Dict[str, Any]] = None,
        available_tool_catalog: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        prompt_key = "system.skill_blueprint.refine" if mode == "refine" else "system.skill_blueprint.generate"
        try:
            registry = get_prompt_registry()
            source_files = source_skill_bundle.get("files") if isinstance(source_skill_bundle, dict) else {}
            source_config = source_files.get("skill_config") if isinstance(source_files, dict) else {}
            messages = registry.render_messages(
                prompt_key,
                {
                    "skill_name": skill_name,
                    "requirement_text": requirement_text,
                    "application_name": self._trim(application_name),
                    "agent_name": self._trim(agent_name),
                    "selected_tools": json.dumps(selected_tools, ensure_ascii=False),
                    "selected_skills": json.dumps(selected_skills, ensure_ascii=False),
                    "source_skill_name": self._trim(source_skill_name),
                    "source_skill_md": str(source_files.get("skill_md_text") or ""),
                    "source_skill_config": json.dumps(source_config or {}, ensure_ascii=False),
                    "source_output_schema": json.dumps(source_files.get("output_schema") or {}, ensure_ascii=False),
                    "display_contract_seed": self.display_contract_compiler.prompt_context(display_contract_seed or {}),
                    "available_tool_catalog": json.dumps(available_tool_catalog or [], ensure_ascii=False),
                },
            )
            plan, _usage = chat_qwen_json(messages, enable_think=False)
            if not isinstance(plan, dict):
                return None
            return plan
        except Exception:
            return None

    def _recommend_max_steps(self, requirement_text: str, selected_tools: List[str]) -> int:
        base = 5
        if len(selected_tools) >= 3:
            base += 1
        if len(selected_tools) >= 5:
            base += 1
        if len(requirement_text) >= 120:
            base += 1
        return min(max(base, 4), 8)

    def _derive_usage_principles(self, selected_tools: List[str]) -> List[str]:
        if not selected_tools:
            return [
                "先澄清任务目标，再补齐工具清单。",
                "优先使用能直接提供关键证据的工具。",
            ]
        principles = ["先调用最关键的证据工具，再补充验证型工具。"]
        if any(name in selected_tools for name in ("stock_quote", "stock_realtime_quote", "stock_history_kline", "stock_intraday_kline", "indicator_series_query")):
            principles.append("涉及行情或指数趋势时，优先确认价格、均线和趋势阶段。")
        if any(name in selected_tools for name in ("stock_funds", "stock_realtime_funds_flow", "stock_history_funds_flow", "stock_industry_funds_flow", "market_realtime_breadth", "market_history_amount", "market_minute_amount_series", "大盘情绪指标")):
            principles.append("涉及情绪和资金时，优先确认涨跌停、资金流和市场广度。")
        if any(name in selected_tools for name in ("financial_news_search", "equity_research_search", "公司研报查询", "stock_reports")):
            principles.append("新闻和研报仅作为催化或验证，不替代行情和资金证据。")
        return principles[:4]

    def _derive_success_checks(self, selected_tools: List[str]) -> List[str]:
        checks = [
            "是否只使用了受控范围内的 tools。",
            "是否每条关键判断都有对应证据。",
            "是否说明了风险和边界条件。",
        ]
        if selected_tools:
            checks.append("是否覆盖了本次任务要求的关键工具输出。")
        return checks

    def _load_available_tool_catalog(self) -> List[Dict[str, Any]]:
        if not self.tool_hub_path.exists():
            return []
        try:
            payload = json.loads(self.tool_hub_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        rows: List[Dict[str, Any]] = []
        for item in payload if isinstance(payload, list) else []:
            if not isinstance(item, dict):
                continue
            name = self._trim(item.get("name"))
            if not name:
                continue
            rows.append(
                {
                    "name": name,
                    "description": self._trim(item.get("description")),
                    "keywords": [self._trim(x) for x in item.get("keywords", []) if self._trim(x)],
                    "capabilities": [self._trim(x) for x in item.get("capabilities", []) if self._trim(x)],
                }
            )
        return rows

    def _resolve_tools(
        self,
        *,
        selected_tools: List[str],
        llm_plan: Optional[Dict[str, Any]],
        available_tool_catalog: List[Dict[str, Any]],
        requirement_text: str,
    ) -> List[str]:
        merged: List[str] = []
        seen = set()
        available_names = {self._trim(item.get("name")) for item in available_tool_catalog if self._trim(item.get("name"))}
        heuristic_tools = self._recommend_tools_from_catalog(
            requirement_text=requirement_text,
            available_tool_catalog=available_tool_catalog,
        )
        for name in selected_tools + [self._trim(x) for x in (llm_plan or {}).get("required_tools", []) if self._trim(x)] + heuristic_tools:
            if not name or name in seen:
                continue
            if available_names and name not in available_names:
                continue
            seen.add(name)
            merged.append(name)
        return merged or selected_tools

    def _recommend_tools_from_catalog(
        self,
        *,
        requirement_text: str,
        available_tool_catalog: List[Dict[str, Any]],
    ) -> List[str]:
        text = self._trim(requirement_text).lower()
        if not text:
            return []
        scored: List[tuple[int, str]] = []
        for item in available_tool_catalog:
            name = self._trim(item.get("name"))
            if not name:
                continue
            score = 0
            for token in [name, *(item.get("keywords") or []), *(item.get("capabilities") or [])]:
                normalized = self._trim(token).lower()
                if normalized and normalized in text:
                    score += 3 if normalized == name.lower() else 1
            if score > 0:
                scored.append((score, name))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [name for _score, name in scored[:4]]
