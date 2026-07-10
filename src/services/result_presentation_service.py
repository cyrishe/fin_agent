from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional


class ResultPresentationService:
    _CHART_TYPES = {"line", "bar", "pie", "kline", "flow"}

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def build_contract(
        self,
        *,
        final_output: Dict[str, Any] | None,
        render_payload: Dict[str, Any] | None,
        objective: str = "",
    ) -> Dict[str, Any]:
        final_output = final_output if isinstance(final_output, dict) else {}
        render_payload = render_payload if isinstance(render_payload, dict) else {}
        sections = render_payload.get("sections") if isinstance(render_payload.get("sections"), list) else []
        normalized_blocks = self._collect_blocks(sections)

        metric_blocks = [item for item in normalized_blocks if item.get("type") == "metric_strip"]
        chart_blocks = [item for item in normalized_blocks if item.get("type") in self._CHART_TYPES]
        table_blocks = [item for item in normalized_blocks if item.get("type") == "table"]
        other_blocks = [
            item for item in normalized_blocks
            if item.get("type") not in self._CHART_TYPES and item.get("type") not in {"metric_strip", "table"}
        ]

        contract_sections: List[Dict[str, Any]] = []
        if metric_blocks:
            contract_sections.append(self._make_block_section("metrics", "核心指标", metric_blocks))
        if chart_blocks:
            contract_sections.append(self._make_block_section("charts", "关键图表", chart_blocks))
        if table_blocks:
            contract_sections.append(self._make_block_section("tables", "数据表", table_blocks))
        if other_blocks:
            contract_sections.append(self._make_block_section("details", "补充信息", other_blocks))

        facts = self._normalize_text_items(final_output.get("facts"))
        risks = self._normalize_text_items(final_output.get("risks"), field="description")
        if facts or risks:
            contract_sections.append(
                {
                    "section_id": "analysis",
                    "section_type": "analysis",
                    "title": "结论与提示",
                    "summary": self._trim(final_output.get("summary")),
                    "facts": facts,
                    "risks": risks,
                }
            )

        return {
            "version": "v1",
            "layout": "conversation_compact",
            "hero": {
                "title": self._hero_title(objective=objective, render_payload=render_payload),
                "summary": self._trim(final_output.get("summary")),
            },
            "sections": contract_sections,
        }

    def _collect_blocks(self, sections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        blocks: List[Dict[str, Any]] = []
        for section in sections:
            if not isinstance(section, dict):
                continue
            section_id = self._trim(section.get("section_id"))
            section_title = self._trim(section.get("title"))
            for block in section.get("blocks") or []:
                if not isinstance(block, dict):
                    continue
                block_type = self._trim(block.get("type"))
                data = block.get("data") if isinstance(block.get("data"), dict) else {}
                if not block_type or not data:
                    continue
                blocks.append(
                    {
                        "section_id": section_id,
                        "section_title": section_title,
                        "block_id": self._trim(block.get("block_id")) or f"{section_id}_{len(blocks) + 1}",
                        "type": block_type,
                        "title": self._trim(block.get("title")),
                        "data": deepcopy(data),
                        "meta": deepcopy(block.get("meta") or {}),
                    }
                )
        return blocks

    def _make_block_section(self, section_id: str, title: str, blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "section_id": section_id,
            "section_type": "block_group",
            "title": title,
            "blocks": [
                {
                    "block_id": item.get("block_id"),
                    "type": item.get("type"),
                    "title": item.get("title"),
                    "data": deepcopy(item.get("data") or {}),
                    "meta": deepcopy(item.get("meta") or {}),
                }
                for item in blocks
            ],
        }

    def _normalize_text_items(self, value: Any, field: str = "detail") -> List[str]:
        items: List[str] = []
        if not isinstance(value, list):
            return items
        for item in value:
            if isinstance(item, dict):
                text = self._trim(item.get(field) or item.get("text") or item.get("detail") or item.get("description"))
            else:
                text = self._trim(item)
            if text:
                items.append(text)
        return items

    def _hero_title(self, *, objective: str, render_payload: Dict[str, Any]) -> str:
        page_type = self._trim(render_payload.get("page_type"))
        if page_type:
            return page_type
        return objective[:48] if self._trim(objective) else "结果"
