import json
import re
from pathlib import Path
from typing import Any, Dict, List

from src.skill_runtime import SkillRunner
from src.skill_runtime.step_budget import resolve_step_budget


def _clean_title(text: str) -> str:
    value = str(text or "").strip()
    return re.sub(r"\s+Skill$", "", value).strip() or value


def _extract_heading_block(markdown: str, heading: str) -> str:
    pattern = re.compile(rf"^##\s+{re.escape(heading)}\s*$", re.MULTILINE)
    match = pattern.search(markdown or "")
    if not match:
        return ""
    start = match.end()
    rest = markdown[start:]
    next_heading = re.search(r"^##\s+", rest, re.MULTILINE)
    block = rest[: next_heading.start() if next_heading else len(rest)]
    return block.strip()


def _extract_bullets(block: str) -> List[str]:
    items: List[str] = []
    for line in (block or "").splitlines():
        line = line.strip()
        if line.startswith("- "):
            items.append(line[2:].strip())
    return items


def _extract_numbered_checks(markdown: str, heading: str = "最终检查") -> List[str]:
    block = _extract_heading_block(markdown, heading)
    items: List[str] = []
    for line in block.splitlines():
        line = line.strip()
        if re.match(r"^\d+\.\s+", line):
            items.append(re.sub(r"^\d+\.\s+", "", line))
    return items


def _find_render_payload_required_sections(skill_md: str) -> List[str]:
    block = _extract_heading_block(skill_md, "输出要求")
    sections: List[str] = []
    capture = False
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("### render_payload"):
            capture = True
            continue
        if capture and stripped.startswith("### "):
            break
        if capture and ("block 类型尽量只使用统一协议里的基元" in stripped or "每个 section / block" in stripped):
            break
        if capture and stripped.startswith("- `") and stripped.endswith("`"):
            sections.append(stripped[3:-1])
    return sections


def _find_preferred_block_types(skill_md: str) -> List[str]:
    block = _extract_heading_block(skill_md, "输出要求")
    items: List[str] = []
    capture = False
    for line in block.splitlines():
        stripped = line.strip()
        if "block 类型尽量只使用统一协议里的基元" in stripped:
            capture = True
            continue
        if capture and stripped.startswith("- `") and stripped.endswith("`"):
            items.append(stripped[3:-1])
            continue
        if capture and stripped and not stripped.startswith("-"):
            break
    return items


def _find_layout_plan_sections(skill_md: str) -> List[str]:
    block = _extract_heading_block(skill_md, "输出内容要求")
    items: List[str] = []
    capture = False
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("### layout_plan"):
            capture = True
            continue
        if capture and stripped.startswith("### "):
            break
        if capture and stripped.endswith("模块体系："):
            continue
        if capture and stripped.startswith("如果是 "):
            continue
        if capture and stripped.startswith("- `") and stripped.endswith("`"):
            value = stripped[3:-1]
            if value not in items:
                items.append(value)
    return items


class SkillBundleCompiler:
    def __init__(self, skills_root: str = "src/skills") -> None:
        self.skills_root = Path(skills_root)
        self.runner = SkillRunner(skills_root=skills_root)

    def bundle_to_skill_spec(self, skill_name: str) -> Dict[str, Any]:
        skill = self.runner.load_skill(skill_name)
        skill_dir = Path(skill.skill_dir)
        config = dict(skill.config or {})
        examples = self._load_examples(skill_dir / "examples")
        required_fields = list(((skill.output_schema or {}).get("required") or []))
        title = _clean_title(self._extract_first_h1(skill.skill_md) or skill.name.replace("_", " ").title())
        purpose = str(config.get("purpose") or config.get("description") or _extract_heading_block(skill.skill_md, "目标") or "").strip()
        skill_body = str(config.get("skill_body") or config.get("execution_process") or skill.skill_body or "").strip()
        success_criteria = _extract_numbered_checks(skill.skill_md)
        avoid_rules = self._extract_avoid_rules(skill.skill_md)
        expected_fields = sorted({*examples[0].keys()} if examples else [])
        config_display_contract = config.get("display_contract") if isinstance(config.get("display_contract"), dict) else {}
        default_sections = _find_render_payload_required_sections(skill.skill_md)
        if not default_sections:
            default_sections = _find_layout_plan_sections(skill.skill_md)
        if not default_sections:
            default_sections = [str(x).strip() for x in config_display_contract.get("default_sections", []) if str(x).strip()]
        preferred_block_types = _find_preferred_block_types(skill.skill_md)
        if not preferred_block_types:
            preferred_block_types = [str(x).strip() for x in config_display_contract.get("preferred_block_types", []) if str(x).strip()]
        display_sections = config_display_contract.get("sections") if isinstance(config_display_contract.get("sections"), list) else []

        spec = {
            "identity": {
                "skill_name": skill.name,
                "title": title,
                "version": "v1",
                "purpose": purpose,
                "description": purpose,
            },
            "goal": {
                "summary": purpose.splitlines()[0].strip() if purpose else title,
                "success_criteria": success_criteria,
                "avoid_rules": avoid_rules,
            },
            "inputs": {
                "expected_fields": expected_fields,
                "example_inputs": examples,
            },
            "tool_policy": {
                "mode": str(((config.get("tool_policy") or {}).get("mode") or "strict")).strip(),
                "tools": [str(x).strip() for x in config.get("tools", []) if str(x).strip()],
                "required_tools_before_final": [str(x).strip() for x in config.get("required_tools_before_final", []) if str(x).strip()],
                "preferred_tools": [str(x).strip() for x in config.get("tools", []) if str(x).strip()],
                "forbidden_tools": [],
            },
            "execution_process": {
                "summary": skill_body.splitlines()[0].strip() if skill_body else "",
                "skill_body": skill_body,
            },
            "reasoning_requirements": {
                "must_cover": self._infer_must_cover(skill_body or skill.skill_md, required_fields),
                "evidence_types": self._infer_evidence_types(skill_body or skill.skill_md),
                "notes": self._extract_reasoning_notes(skill_body or skill.skill_md),
            },
            "output_contract": {
                "required_top_level_fields": required_fields,
                "output_schema": skill.output_schema,
            },
            "display_contract": {
                "page_type": str(config_display_contract.get("page_type") or config.get("expected_render_page_type") or "").strip(),
                "default_sections": default_sections,
                "preferred_block_types": preferred_block_types,
                "sections": display_sections,
                "passthrough_bindings": config_display_contract.get("passthrough_bindings") or [],
            },
            "runtime_hints": {
                "default_max_steps": int(config.get("default_max_steps", 6) or 6),
                "enable_think": bool(config.get("enable_think", False)),
            },
            "source_bundle": {
                "skill_md_text": skill.skill_md,
                "skill_config_text": json.dumps(config, ensure_ascii=False, indent=2),
                "output_schema_text": json.dumps(skill.output_schema, ensure_ascii=False, indent=2),
            },
        }
        return spec

    def skill_spec_to_bundle(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        identity = spec.get("identity") if isinstance(spec.get("identity"), dict) else {}
        goal = spec.get("goal") if isinstance(spec.get("goal"), dict) else {}
        tool_policy = spec.get("tool_policy") if isinstance(spec.get("tool_policy"), dict) else {}
        reasoning = spec.get("reasoning_requirements") if isinstance(spec.get("reasoning_requirements"), dict) else {}
        output_contract = spec.get("output_contract") if isinstance(spec.get("output_contract"), dict) else {}
        display_contract = spec.get("display_contract") if isinstance(spec.get("display_contract"), dict) else {}
        runtime_hints = spec.get("runtime_hints") if isinstance(spec.get("runtime_hints"), dict) else {}

        lines = [
            f"# {identity.get('title') or identity.get('skill_name') or 'Skill'}",
            "",
            "## 目标",
            "",
            str(goal.get("summary") or "").strip(),
            "",
        ]
        success_criteria = goal.get("success_criteria") or []
        if isinstance(success_criteria, list) and success_criteria:
            lines.extend(["## 最终检查", ""])
            for idx, item in enumerate(success_criteria, start=1):
                lines.append(f"{idx}. {str(item).strip()}")
            lines.append("")

        tools = [str(x).strip() for x in tool_policy.get("tools", []) if str(x).strip()]
        if tools:
            lines.extend(["## 可用 tools", ""])
            for name in tools:
                lines.append(f"- `{name}`")
            lines.append("")

        must_cover = reasoning.get("must_cover") or []
        if isinstance(must_cover, list) and must_cover:
            lines.extend(["## 核心分析框架", ""])
            for item in must_cover:
                lines.append(f"- {str(item).strip()}")
            lines.append("")

        required_fields = output_contract.get("required_top_level_fields") or []
        sections = display_contract.get("default_sections") or []
        block_types = display_contract.get("preferred_block_types") or []
        if required_fields or sections or block_types:
            lines.extend(["## 输出要求", ""])
            if required_fields:
                lines.append("必须输出以下核心字段：")
                lines.append("")
                for item in required_fields:
                    lines.append(f"- `{str(item).strip()}`")
                lines.append("")
            if sections:
                lines.extend(["### render_payload", "", "前端可直接渲染，至少包含这些 section：", ""])
                for item in sections:
                    lines.append(f"- `{str(item).strip()}`")
                lines.append("")
        if block_types:
            lines.append("并且 block 类型尽量只使用统一协议里的基元：")
            lines.append("")
            for item in block_types:
                lines.append(f"- `{str(item).strip()}`")
            lines.append("")
        passthrough_bindings = display_contract.get("passthrough_bindings") or []
        if isinstance(passthrough_bindings, list) and passthrough_bindings:
            lines.extend(["优先透传的工具展示数据：", ""])
            for item in passthrough_bindings[:8]:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    f"- `{str(item.get('tool_name') or '').strip()}` -> `{str(item.get('source_path') or '').strip()}` -> `{str(item.get('section_kind') or '').strip()}` / `{str(item.get('block_type') or '').strip()}`"
                )
            lines.append("")

        config = {
            "tool_policy": {"mode": str(tool_policy.get("mode") or "strict").strip()},
            "tools": tools,
            "default_max_steps": int(runtime_hints.get("default_max_steps", 6) or 6),
            "enable_think": bool(runtime_hints.get("enable_think", False)),
        }
        required_tools_before_final = [str(x).strip() for x in tool_policy.get("required_tools_before_final", []) if str(x).strip()]
        if required_tools_before_final:
            config["required_tools_before_final"] = required_tools_before_final
        expected_render_page_type = str(display_contract.get("page_type") or "").strip()
        if expected_render_page_type:
            config["expected_render_page_type"] = expected_render_page_type
        if isinstance(display_contract, dict) and display_contract:
            config["display_contract"] = display_contract

        output_schema = output_contract.get("output_schema") if isinstance(output_contract.get("output_schema"), dict) else {}
        return {
            "skill_md_text": "\n".join(lines).strip() + "\n",
            "skill_config_text": json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            "output_schema_text": json.dumps(output_schema, ensure_ascii=False, indent=2) + "\n",
            "skill_config": config,
            "output_schema": output_schema,
        }

    def build_execution_plan(
        self,
        *,
        skill_name: str,
        input_payload: Dict[str, Any],
        tool_mode: str = "",
    ) -> Dict[str, Any]:
        skill = self.runner.require_active_skill(skill_name)
        spec = self.bundle_to_skill_spec(skill_name)
        if tool_mode:
            policy = dict(skill.config.get("tool_policy") or {})
            policy["mode"] = tool_mode
            skill.config["tool_policy"] = policy
        selection = self.runner.tool_selector.select_detailed(
            skill_name=skill.name,
            skill_md=skill.skill_md,
            skill_config=skill.config,
            input_payload=input_payload,
        )
        display_contract = spec.get("display_contract") if isinstance(spec.get("display_contract"), dict) else {}
        reasoning = spec.get("reasoning_requirements") if isinstance(spec.get("reasoning_requirements"), dict) else {}
        runtime_hints = spec.get("runtime_hints") if isinstance(spec.get("runtime_hints"), dict) else {}
        page_type = str(display_contract.get("page_type") or "").strip()
        selected_sections = [str(x).strip() for x in display_contract.get("default_sections", []) if str(x).strip()]
        selected_tools = selection.get("selected_tools") or []
        tool_policy = spec.get("tool_policy") if isinstance(spec.get("tool_policy"), dict) else {}
        runtime_max_steps = resolve_step_budget(
            base_max_steps=int(runtime_hints.get("default_max_steps", 6) or 6),
            tool_mode=str(tool_policy.get("mode") or "strict").strip(),
            selected_tools=selected_tools,
            required_tools_before_final=tool_policy.get("required_tools_before_final") or [],
        )
        stage_plan = [
            {
                "stage": "collecting_data",
                "goal": "完成核心证据收集，优先获取高价值工具结果。",
                "expected_outputs": selected_tools,
            },
            {
                "stage": "synthesizing",
                "goal": "基于证据形成结构化判断，覆盖 skill 要求的核心维度。",
                "expected_outputs": [str(x).strip() for x in reasoning.get("must_cover", []) if str(x).strip()],
            },
            {
                "stage": "rendering",
                "goal": "生成满足 schema 的 final_output 和 render_payload。",
                "expected_outputs": selected_sections,
            },
            {
                "stage": "compliance_check",
                "goal": "完成格式、约束和页面类型校验。",
                "expected_outputs": [page_type] if page_type else [],
            },
        ]
        return {
            "skill_name": skill_name,
            "skill_version": str((spec.get("identity") or {}).get("version") or "v1"),
            "input_payload": input_payload,
            "selected_tools": selected_tools,
            "selected_sections": selected_sections,
            "required_evidence_types": [str(x).strip() for x in reasoning.get("evidence_types", []) if str(x).strip()],
            "runtime_limits": {
                "max_steps": runtime_max_steps,
                "enable_think": bool(runtime_hints.get("enable_think", False)),
            },
            "stage_plan": stage_plan,
            "fallback_rules": [
                "当某个模块证据不足时，允许保留空态或显式写明待验证，不允许臆造结论。",
                "当工具返回过大时，优先依赖系统层 retention/reducer 进入下一步上下文。",
                "最终输出必须满足 schema 和 expected_render_page_type。",
            ],
            "tool_selection": selection,
            "source_skill_spec": spec,
        }

    @staticmethod
    def _extract_first_h1(markdown: str) -> str:
        match = re.search(r"^#\s+(.+)$", markdown or "", re.MULTILINE)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _extract_avoid_rules(markdown: str) -> List[str]:
        text = markdown or ""
        candidates = [
            "不虚构研报、资金或行情信息",
            "不凭空补齐不存在的数据",
            "避免情绪化语言和喊单式表达",
            "不夸张、不喊单",
        ]
        return [item for item in candidates if item in text]

    @staticmethod
    def _infer_must_cover(skill_md: str, required_fields: List[str]) -> List[str]:
        text = skill_md or ""
        mapping = {
            "行情": ["行情", "K线", "市场信号"],
            "资金": ["资金", "主力资金", "capital flow"],
            "研报": ["研报", "机构观点"],
            "新闻": ["新闻", "催化", "事件"],
            "风险": ["风险", "观察点"],
            "时间线": ["时间线"],
        }
        items: List[str] = []
        for key, aliases in mapping.items():
            if any(alias in text for alias in aliases):
                items.append(key)
        for field in required_fields:
            field_text = str(field or "").strip()
            if field_text and field_text not in items:
                if field_text in {"timeline", "brief", "attribution", "watch_items"}:
                    items.append(field_text)
        return items

    @staticmethod
    def _infer_evidence_types(skill_md: str) -> List[str]:
        text = skill_md or ""
        candidates = []
        for name in ("news", "reports", "funds", "quote", "timeline"):
            mapping = {
                "news": ("新闻", "催化", "事件"),
                "reports": ("研报", "机构"),
                "funds": ("资金", "主力"),
                "quote": ("行情", "K线", "量价"),
                "timeline": ("时间线",),
            }
            if any(token in text for token in mapping[name]):
                candidates.append(name)
        return candidates

    @staticmethod
    def _extract_reasoning_notes(skill_md: str) -> List[str]:
        role_block = _extract_heading_block(skill_md, "角色定位")
        return [line.strip("- ").strip() for line in role_block.splitlines() if line.strip().startswith("-")]

    @staticmethod
    def _load_examples(examples_dir: Path) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        if not examples_dir.exists():
            return rows
        for path in sorted(examples_dir.glob("*.json")):
            if path.name.startswith("._"):
                continue
            rows.append(json.loads(path.read_text(encoding="utf-8")))
        return rows
