import json
from pathlib import Path
from typing import Any, Dict, List, Optional


_SECTION_PRESETS: Dict[str, Dict[str, str]] = {
    "market_overview": {
        "title": "市场概览",
        "description": "关键指标、快照和榜单概览。",
    },
    "market_kline": {
        "title": "行情走势",
        "description": "K线与价格趋势相关展示。",
    },
    "stock_focus": {
        "title": "重点标的",
        "description": "重点股票、榜单或候选对象。",
    },
    "capital_flow": {
        "title": "资金流向",
        "description": "主力净额、历史资金与相关图表。",
    },
    "research_prediction": {
        "title": "研报观点",
        "description": "机构评级、目标价和一致预期。",
    },
    "news_catalyst": {
        "title": "新闻催化",
        "description": "新闻列表、事件线索与催化摘要。",
    },
    "risk_watch": {
        "title": "风险提示",
        "description": "需要提醒用户关注的风险和边界。",
    },
}

_TOOL_SECTION_HINTS: Dict[str, str] = {
    "个股动量排名": "stock_focus",
    "实时个股动量排名": "stock_focus",
    "stock_quote": "market_kline",
    "stock_realtime_quote": "market_kline",
    "stock_history_kline": "market_kline",
    "stock_intraday_kline": "market_kline",
    "stock_funds": "capital_flow",
    "equity_research_search": "research_prediction",
    "公司研报查询": "research_prediction",
    "financial_news_search": "news_catalyst",
    "general_search": "news_catalyst",
    "theme_leaders": "stock_focus",
    "indicator_series_query": "market_overview",
}

_SECTION_BLOCK_PRIORITIES: Dict[str, List[str]] = {
    "market_overview": ["metric_strip", "table", "structured_text"],
    "market_kline": ["kline", "metric_strip", "structured_text"],
    "stock_focus": ["table", "metric_strip", "text_list"],
    "capital_flow": ["metric_strip", "table", "bar", "text_list"],
    "research_prediction": ["table", "text_list", "structured_text"],
    "news_catalyst": ["text_list", "structured_text", "timeline"],
    "risk_watch": ["structured_text", "text_list"],
}


class DisplayContractCompiler:
    def __init__(self, specs_root: str = "src/tools/specs") -> None:
        self.specs_root = Path(specs_root)

    def compile(
        self,
        *,
        selected_tools: List[str],
        requirement_text: str = "",
        skill_name: str = "",
        preferred_page_type: str = "",
        existing_display_contract: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        tool_names = [str(x or "").strip() for x in selected_tools if str(x or "").strip()]
        existing = existing_display_contract if isinstance(existing_display_contract, dict) else {}
        sections: List[Dict[str, Any]] = []
        section_index: Dict[str, Dict[str, Any]] = {}
        preferred_block_types: List[str] = []
        passthrough_bindings: List[Dict[str, Any]] = []
        reasoning_sources: List[Dict[str, Any]] = []

        for tool_name in tool_names:
            spec = self._load_tool_spec(tool_name)
            output_guidance = (spec.get("output_guidance") if isinstance(spec, dict) else {}) or {}
            render_paths = [str(x).strip() for x in output_guidance.get("high_value_for_render", []) if str(x).strip()]
            reasoning_paths = [str(x).strip() for x in output_guidance.get("high_value_for_reasoning", []) if str(x).strip()]

            section_kind = self._infer_section_kind(tool_name, render_paths, reasoning_paths)
            section = self._ensure_section(section_kind, sections, section_index)

            for path in render_paths:
                block_type = self._infer_block_type(path, tool_name)
                if block_type not in section["preferred_block_types"]:
                    section["preferred_block_types"].append(block_type)
                if block_type not in preferred_block_types:
                    preferred_block_types.append(block_type)
                passthrough_bindings.append(
                    {
                        "tool_name": tool_name,
                        "source_path": path,
                        "section_kind": section_kind,
                        "block_type": block_type,
                        "title": self._binding_title(tool_name, path),
                        "mode": "pass_through",
                    }
                )

            if reasoning_paths:
                reasoning_sources.append(
                    {
                        "tool_name": tool_name,
                        "reasoning_paths": reasoning_paths,
                        "summary": str(spec.get("purpose") or "").strip(),
                    }
                )

        if "risk_watch" not in section_index and (tool_names or requirement_text):
            section = self._ensure_section("risk_watch", sections, section_index)
            if "structured_text" not in section["preferred_block_types"]:
                section["preferred_block_types"].append("structured_text")
            if "structured_text" not in preferred_block_types:
                preferred_block_types.append("structured_text")

        page_type = (
            str(preferred_page_type or "").strip()
            or str(existing.get("page_type") or "").strip()
            or self._infer_page_type(tool_names, skill_name, requirement_text)
        )
        merged_sections = self._merge_sections_with_existing(sections, existing.get("sections"))
        merged_preferred_blocks = self._merge_unique(
            preferred_block_types,
            [str(x).strip() for x in existing.get("preferred_block_types", []) if str(x).strip()],
        )

        return {
            "page_type": page_type,
            "sections": merged_sections,
            "default_sections": [section.get("section_kind") for section in merged_sections if section.get("section_kind")],
            "preferred_block_types": merged_preferred_blocks,
            "passthrough_bindings": passthrough_bindings,
            "reasoning_sources": reasoning_sources,
        }

    def build_output_schema(
        self,
        *,
        skill_name: str,
        required_fields: List[str],
        field_descriptions: Dict[str, Any],
        display_contract: Dict[str, Any],
    ) -> Dict[str, Any]:
        normalized_name = str(skill_name or "").strip() or "generated_skill"
        required = [str(x).strip() for x in required_fields if str(x).strip()] or ["summary", "facts", "risks", "render_payload"]
        properties = {
            "summary": {
                "type": "string",
                "description": str(field_descriptions.get("summary") or "skill 最终摘要"),
            },
            "facts": {
                "type": "array",
                "description": str(field_descriptions.get("facts") or "支撑结论的关键事实"),
                "items": {"$ref": "#/$defs/factItem"},
            },
            "judgement": {
                "type": "string",
                "description": str(field_descriptions.get("judgement") or "综合判断"),
            },
            "risks": {
                "type": "array",
                "description": str(field_descriptions.get("risks") or "关键风险或待确认事项"),
                "items": {"$ref": "#/$defs/riskItem"},
            },
            "render_payload": self._render_payload_schema(
                page_type=str(display_contract.get("page_type") or "").strip(),
                field_description=str(field_descriptions.get("render_payload") or "可选的前端渲染载荷"),
            ),
        }
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": f"{normalized_name} Output",
            "type": "object",
            "required": required,
            "properties": properties,
            "additionalProperties": True,
            "$defs": self._defs_schema(),
        }

    def build_skill_md_output_contract(self, display_contract: Dict[str, Any]) -> List[str]:
        lines: List[str] = []
        default_sections = [str(x).strip() for x in display_contract.get("default_sections", []) if str(x).strip()]
        preferred_block_types = [str(x).strip() for x in display_contract.get("preferred_block_types", []) if str(x).strip()]
        passthrough_bindings = display_contract.get("passthrough_bindings") if isinstance(display_contract.get("passthrough_bindings"), list) else []

        if default_sections:
            lines.extend(["### render_payload", "", "前端可直接渲染，至少包含这些 section：", ""])
            for item in default_sections:
                lines.append(f"- `{item}`")
            lines.append("")
        if preferred_block_types:
            lines.append("并且 block 类型尽量只使用统一协议里的基元：")
            lines.append("")
            for item in preferred_block_types:
                lines.append(f"- `{item}`")
            lines.append("")
        if passthrough_bindings:
            lines.extend(["优先透传的工具展示数据：", ""])
            for binding in passthrough_bindings[:8]:
                lines.append(
                    f"- `{binding.get('tool_name')}` -> `{binding.get('source_path')}` -> `{binding.get('section_kind')}` / `{binding.get('block_type')}`"
                )
            lines.append("")
        return lines

    def prompt_context(self, display_contract: Dict[str, Any]) -> str:
        compact = {
            "page_type": display_contract.get("page_type"),
            "default_sections": display_contract.get("default_sections"),
            "preferred_block_types": display_contract.get("preferred_block_types"),
            "passthrough_bindings": [
                {
                    "tool_name": item.get("tool_name"),
                    "source_path": item.get("source_path"),
                    "section_kind": item.get("section_kind"),
                    "block_type": item.get("block_type"),
                }
                for item in (display_contract.get("passthrough_bindings") or [])[:12]
                if isinstance(item, dict)
            ],
        }
        return json.dumps(compact, ensure_ascii=False)

    def merge_with_llm_plan(self, base_contract: Dict[str, Any], llm_plan: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not isinstance(llm_plan, dict):
            return base_contract
        plan_contract = llm_plan.get("display_contract") if isinstance(llm_plan.get("display_contract"), dict) else {}
        if not plan_contract:
            return base_contract
        merged = dict(base_contract)
        page_type = str(plan_contract.get("page_type") or "").strip()
        if page_type:
            merged["page_type"] = page_type
        preferred_block_types = [str(x).strip() for x in plan_contract.get("preferred_block_types", []) if str(x).strip()]
        if preferred_block_types:
            merged["preferred_block_types"] = self._merge_unique(
                preferred_block_types,
                [str(x).strip() for x in merged.get("preferred_block_types", []) if str(x).strip()],
            )
        override_sections = [str(x).strip() for x in plan_contract.get("default_sections", []) if str(x).strip()]
        if override_sections:
            merged["default_sections"] = override_sections
            existing_sections = merged.get("sections") if isinstance(merged.get("sections"), list) else []
            merged["sections"] = [section for section in existing_sections if str(section.get("section_kind") or "").strip() in override_sections]
        return merged

    def _defs_schema(self) -> Dict[str, Any]:
        return {
            "factItem": {
                "type": "object",
                "required": ["category", "detail"],
                "properties": {
                    "category": {"type": "string"},
                    "detail": {"type": "string"},
                    "source_tool": {"type": "string"},
                    "source_path": {"type": "string"},
                    "render_hint": {"type": "string"},
                },
                "additionalProperties": True,
            },
            "riskItem": {
                "type": "object",
                "required": ["description", "type"],
                "properties": {
                    "description": {"type": "string"},
                    "type": {"type": "string"},
                    "severity": {"type": "string"},
                    "source_tool": {"type": "string"},
                },
                "additionalProperties": True,
            },
            "renderBlock": {
                "type": "object",
                "required": ["block_id", "type", "data"],
                "properties": {
                    "block_id": {"type": "string"},
                    "type": {"type": "string"},
                    "title": {"type": "string"},
                    "span": {"type": "object"},
                    "height": {"type": "string"},
                    "data": {"type": "object"},
                },
                "additionalProperties": True,
            },
            "renderSection": {
                "type": "object",
                "required": ["section_id", "section_kind", "title", "blocks"],
                "properties": {
                    "section_id": {"type": "string"},
                    "section_kind": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "layout": {"type": "object"},
                    "blocks": {"type": "array", "items": {"$ref": "#/$defs/renderBlock"}},
                },
                "additionalProperties": True,
            },
        }

    def _render_payload_schema(self, *, page_type: str, field_description: str) -> Dict[str, Any]:
        return {
            "type": "object",
            "description": field_description,
            "required": ["version", "page_id", "page_type", "title", "subtitle", "summary", "sections"],
            "properties": {
                "version": {"type": "string"},
                "page_id": {"type": "string"},
                "page_type": {"type": "string", "default": page_type},
                "title": {"type": "string"},
                "subtitle": {"type": "string"},
                "as_of": {"type": "string"},
                "theme": {"type": "object"},
                "summary": {"type": "object"},
                "sections": {"type": "array", "items": {"$ref": "#/$defs/renderSection"}},
            },
            "additionalProperties": True,
        }

    def _load_tool_spec(self, tool_name: str) -> Dict[str, Any]:
        path = self.specs_root / f"{tool_name}.spec.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _infer_page_type(self, tool_names: List[str], skill_name: str, requirement_text: str) -> str:
        name_text = " ".join(tool_names + [skill_name, requirement_text]).lower()
        if "动量" in name_text or "momentum" in name_text:
            return "momentum_dashboard"
        if "hotspot" in name_text or "热点" in name_text:
            return "hotspot_report"
        if {
            "stock_quote",
            "stock_realtime_quote",
            "stock_history_kline",
            "stock_intraday_kline",
            "stock_funds",
            "equity_research_search",
            "financial_news_search",
            "general_search",
        } & set(tool_names):
            return "stock_deep_dive"
        return "analysis_report"

    def _infer_section_kind(self, tool_name: str, render_paths: List[str], reasoning_paths: List[str]) -> str:
        hinted = _TOOL_SECTION_HINTS.get(tool_name)
        if hinted:
            return hinted
        paths = " ".join(render_paths + reasoning_paths).lower()
        if "kline" in paths:
            return "market_kline"
        if "news" in paths or "items" in paths:
            return "news_catalyst"
        if "report" in paths:
            return "research_prediction"
        if "flow" in paths or "fund" in paths:
            return "capital_flow"
        return "stock_focus"

    def _infer_block_type(self, path: str, tool_name: str) -> str:
        lower = str(path or "").lower()
        if "kline" in lower:
            return "kline"
        if "chart" in lower or "series" in lower:
            return "bar"
        if "historical_table" in lower or lower.endswith("table") or ".data" == lower[-5:] or tool_name in {"个股动量排名", "实时个股动量排名"}:
            return "table"
        if "items" in lower:
            if "news" in lower or "report" in lower:
                return "text_list"
            return "table"
        if "meta" in lower or "snapshot" in lower:
            return "metric_strip"
        return "structured_text"

    def _binding_title(self, tool_name: str, path: str) -> str:
        leaf = str(path or "").split(".")[-1] or "data"
        return f"{tool_name} · {leaf}"

    def _ensure_section(
        self,
        section_kind: str,
        sections: List[Dict[str, Any]],
        section_index: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        existing = section_index.get(section_kind)
        if existing is not None:
            return existing
        preset = _SECTION_PRESETS.get(section_kind, {"title": section_kind, "description": ""})
        section = {
            "section_id": section_kind,
            "section_kind": section_kind,
            "title": preset.get("title") or section_kind,
            "description": preset.get("description") or "",
            "preferred_block_types": list(_SECTION_BLOCK_PRIORITIES.get(section_kind, [])),
        }
        sections.append(section)
        section_index[section_kind] = section
        return section

    def _merge_sections_with_existing(self, sections: List[Dict[str, Any]], existing_sections: Any) -> List[Dict[str, Any]]:
        merged = [dict(section) for section in sections]
        if not isinstance(existing_sections, list):
            return merged
        existing_map = {
            str(item.get("section_kind") or "").strip(): item
            for item in existing_sections
            if isinstance(item, dict) and str(item.get("section_kind") or "").strip()
        }
        for section in merged:
            existing = existing_map.get(str(section.get("section_kind") or "").strip())
            if not isinstance(existing, dict):
                continue
            if str(existing.get("title") or "").strip():
                section["title"] = str(existing.get("title") or "").strip()
            if str(existing.get("description") or "").strip():
                section["description"] = str(existing.get("description") or "").strip()
            existing_blocks = [str(x).strip() for x in existing.get("preferred_block_types", []) if str(x).strip()]
            if existing_blocks:
                section["preferred_block_types"] = self._merge_unique(
                    section.get("preferred_block_types") or [],
                    existing_blocks,
                )
        return merged

    def _merge_unique(self, first: List[str], second: List[str]) -> List[str]:
        merged: List[str] = []
        for item in list(first) + list(second):
            value = str(item or "").strip()
            if value and value not in merged:
                merged.append(value)
        return merged
