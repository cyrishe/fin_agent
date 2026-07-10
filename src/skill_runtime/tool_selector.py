import json
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

from src.prompting.prompt_registry import get_prompt_registry
from src.utils.ai_service import chat_qwen_json

from src.tools.registry import list_tools


class ToolSelectionError(ValueError):
    pass


class ToolSelector:
    """
    Two-stage tool selection:
    1) discover_candidates: lightweight recall from tool hub
    2) select: apply skill policy to get final allowed tools

    Current implementation keeps both stages simple, but the split is
    intentional so future versions can plug in embedding recall, reranking,
    permission filters, tool cost limits, or compressed tool definitions.
    """

    def __init__(self, hub_path: str = "src/tools/tool_hub.json") -> None:
        self.hub_path = Path(hub_path)
        self._hub = self._load_hub()

    def _load_hub(self) -> List[Dict[str, Any]]:
        if not self.hub_path.exists():
            return []
        return json.loads(self.hub_path.read_text(encoding="utf-8"))

    @staticmethod
    def _is_hub_item_retrievable(item: Dict[str, Any]) -> bool:
        availability = item.get("availability") if isinstance(item.get("availability"), dict) else {}
        lifecycle = str(availability.get("lifecycle") or item.get("lifecycle") or "active").strip().lower()
        retrieval_mode = str(availability.get("retrieval_mode") or item.get("retrieval_mode") or "retrievable").strip().lower()
        visibility = str(availability.get("visibility") or item.get("visibility") or "visible").strip().lower()
        return lifecycle == "active" and retrieval_mode == "retrievable" and visibility != "hidden"

    def select(
        self,
        *,
        skill_name: str,
        skill_md: str,
        skill_config: Dict[str, Any],
        input_payload: Dict[str, Any],
    ) -> List[str]:
        detail = self.select_detailed(
            skill_name=skill_name,
            skill_md=skill_md,
            skill_config=skill_config,
            input_payload=input_payload,
        )
        return detail["selected_tools"]

    def select_detailed(
        self,
        *,
        skill_name: str,
        skill_md: str,
        skill_config: Dict[str, Any],
        input_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        mode = str((skill_config.get("tool_policy") or {}).get("mode") or "strict").strip().lower()
        declared_tools = [str(x).strip() for x in skill_config.get("tools", []) if str(x).strip()]
        all_tools = set(list_tools())
        max_candidates = int((skill_config.get("tool_policy") or {}).get("max_candidates") or 8)
        max_auto_selected = int((skill_config.get("tool_policy") or {}).get("max_auto_selected") or 4)
        use_llm_selector = self._resolve_llm_selector_flag(skill_config)

        if mode == "strict":
            if not declared_tools:
                raise ToolSelectionError(f"skill '{skill_name}' uses strict mode but defines no tools")
            selected = [tool for tool in declared_tools if tool in all_tools]
            if not selected:
                raise ToolSelectionError(f"skill '{skill_name}' strict tools are unavailable")
            return {
                "mode": mode,
                "declared_tools": declared_tools,
                "selected_tools": selected,
                "discovered_candidates": [],
                "selection_reason": "strict mode uses declared tools only",
            }

        discovered = self.discover_candidates(
            skill_name=skill_name,
            skill_md=skill_md,
            input_payload=input_payload,
            max_candidates=max_candidates,
        )
        discovered_names = [item["name"] for item in discovered]

        if mode == "auto":
            selected = self._select_auto_tools(
                declared_tools=[tool for tool in declared_tools if tool in all_tools],
                discovered=discovered,
                input_payload=input_payload,
                max_selected=max_auto_selected,
                use_llm_selector=use_llm_selector,
            )
            return {
                "mode": mode,
                "declared_tools": declared_tools,
                "selected_tools": selected,
                "discovered_candidates": discovered,
                "selection_reason": "auto mode uses scored discovery with optional LLM rerank",
            }

        if mode == "free":
            preferred = [tool for tool in declared_tools if tool in all_tools]
            merged: List[str] = []
            seen = set()
            for tool in preferred + discovered_names:
                if tool in seen:
                    continue
                seen.add(tool)
                merged.append(tool)
            return {
                "mode": mode,
                "declared_tools": declared_tools,
                "selected_tools": merged,
                "discovered_candidates": discovered,
                "selection_reason": "free mode merges declared tools with discovered candidates",
            }

        raise ToolSelectionError(f"unsupported tool selection mode: {mode}")

    def discover_candidates(
        self,
        *,
        skill_name: str,
        skill_md: str,
        input_payload: Dict[str, Any],
        max_candidates: int = 8,
    ) -> List[Dict[str, Any]]:
        if not self._hub:
            return [{"name": name, "score": 0, "reasons": ["fallback_no_hub"]} for name in list_tools()[:max(1, int(max_candidates))]]

        text_parts = [
            skill_name,
            skill_md,
            json.dumps(input_payload, ensure_ascii=False),
        ]
        corpus = " ".join(part for part in text_parts if part).lower()

        scored: List[Tuple[int, str, List[str]]] = []
        for item in self._hub:
            if not self._is_hub_item_retrievable(item):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            score = int(item.get("priority", 0) or 0)
            reasons: List[str] = []
            for token in item.get("keywords", []) or []:
                token_text = str(token or "").strip().lower()
                if token_text and token_text in corpus:
                    score += 5
                    reasons.append(f"keyword:{token_text}")
            for token in item.get("capabilities", []) or []:
                token_text = str(token or "").strip().lower()
                if token_text and token_text in corpus:
                    score += 3
                    reasons.append(f"capability:{token_text}")
            description = str(item.get("description") or "").strip().lower()
            if description:
                desc_hits = sum(1 for word in description.split() if word and word in corpus)
                score += desc_hits
                if desc_hits:
                    reasons.append(f"description_hits:{desc_hits}")
            scored.append((score, name, reasons))

        scored.sort(key=lambda x: (-x[0], x[1]))
        selected = [
            {"name": name, "score": score, "reasons": reasons}
            for score, name, reasons in scored
            if score > 0
        ]
        fallback = list_tools()
        if not selected:
            selected = [{"name": name, "score": 0, "reasons": ["fallback_zero_score"]} for name in fallback]
        return selected[:max(1, int(max_candidates))]

    def _select_auto_tools(
        self,
        *,
        declared_tools: List[str],
        discovered: List[Dict[str, Any]],
        input_payload: Dict[str, Any],
        max_selected: int,
        use_llm_selector: bool,
    ) -> List[str]:
        discovered_names = [str(item.get("name") or "").strip() for item in discovered if str(item.get("name") or "").strip()]
        if not discovered_names:
            return declared_tools[:max(1, max_selected)] if declared_tools else []

        if use_llm_selector:
            llm_selected = self._llm_select_tools(
                discovered=discovered,
                declared_tools=declared_tools,
                input_payload=input_payload,
                max_selected=max_selected,
            )
            if llm_selected:
                return llm_selected

        heuristic_selected = self._heuristic_auto_select(
            discovered_names=discovered_names,
            input_payload=input_payload,
            max_selected=max_selected,
        )
        if heuristic_selected:
            if declared_tools:
                declared_set = set(declared_tools)
                filtered = [tool for tool in heuristic_selected if tool in declared_set]
                if filtered:
                    return filtered
            return heuristic_selected

        preferred = [tool for tool in declared_tools if tool in discovered_names]
        if preferred:
            return preferred[:max(1, max_selected)]
        return discovered_names[:max(1, max_selected)]

    @staticmethod
    def _heuristic_auto_select(
        *,
        discovered_names: List[str],
        input_payload: Dict[str, Any],
        max_selected: int,
    ) -> List[str]:
        text = json.dumps(input_payload, ensure_ascii=False).lower()
        trace_type = str(input_payload.get("trace_type") or "").strip().lower()
        candidate_context = input_payload.get("candidate_context") if isinstance(input_payload.get("candidate_context"), dict) else {}
        code = str(input_payload.get("code") or candidate_context.get("code") or "").strip()
        stock_like = bool(code) or trace_type == "abnormal"
        selections: List[str] = []

        def maybe_add(tool_name: str, condition: bool) -> None:
            if condition and tool_name in discovered_names and tool_name not in selections:
                selections.append(tool_name)

        maybe_add("financial_news_search", any(token in text for token in ("新闻", "催化", "事件", "热点", "概念", "资讯")) or trace_type in {"concept", "manual", "abnormal"})
        maybe_add("stock_realtime_quote", stock_like and any(token in text for token in ("行情", "股价", "价格", "现价", "实时")) or trace_type == "abnormal")
        maybe_add("stock_history_kline", stock_like and any(token in text for token in ("k线", "走势", "量价", "日k", "均线", "历史行情")))
        maybe_add("stock_intraday_kline", stock_like and any(token in text for token in ("分时", "分钟k", "日内", "盘口")))
        maybe_add("stock_realtime_funds_flow", stock_like and any(token in text for token in ("资金", "主力", "净流入", "净流出", "资金面", "实时资金")) or trace_type == "abnormal")
        maybe_add("stock_history_funds_flow", stock_like and any(token in text for token in ("历史资金", "近n日资金", "近期资金", "资金变化", "连续流入")))
        maybe_add("stock_industry_funds_flow", stock_like and any(token in text for token in ("行业资金", "同行资金", "同类公司资金", "行业净流入", "行业对比")))
        maybe_add("equity_research_search", stock_like and any(token in text for token in ("研报", "机构", "评级", "目标价", "观点", "判断", "风险", "持续性")))
        maybe_add("hotspot_trace", any(token in text for token in ("上次", "历史", "追踪", "回看", "之前", "增量")))

        if not selections:
            selections = discovered_names[:max(1, max_selected)]
        return selections[:max(1, max_selected)]

    @staticmethod
    def _resolve_llm_selector_flag(skill_config: Dict[str, Any]) -> bool:
        policy = skill_config.get("tool_policy") if isinstance(skill_config.get("tool_policy"), dict) else {}
        raw = policy.get("use_llm_selector")
        if raw is None:
            raw = os.getenv("TOOL_SELECTOR_USE_LLM", "")
        return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _llm_select_tools(
        *,
        discovered: List[Dict[str, Any]],
        declared_tools: List[str],
        input_payload: Dict[str, Any],
        max_selected: int,
    ) -> List[str]:
        tool_descriptions = []
        for item in discovered:
            tool_descriptions.append(
                {
                    "name": item.get("name"),
                    "score": item.get("score"),
                    "reasons": item.get("reasons") or [],
                }
            )
        messages = [
            *get_prompt_registry().render_messages(
                "system.tool_selector.auto_rerank",
                {
                    "selection_payload": json.dumps(
                        {
                            "input_payload": input_payload,
                            "declared_tools": declared_tools,
                            "candidate_tools": tool_descriptions,
                            "max_selected": max_selected,
                            "selection_rules": [
                                "优先选择与当前输入直接相关的工具",
                                "如果是概念类热点，没有明确股票代码时，不要轻易选择个股行情和个股资金工具",
                                "如果是异动个股，行情和新闻通常优先，资金和研报按问题需要保留",
                                "不要输出未在 candidate_tools 中出现的工具"
                            ],
                        },
                        ensure_ascii=False,
                    ),
                },
            )
        ]
        try:
            payload, _usage = chat_qwen_json(messages, enable_think=False)
        except Exception:
            return []
        if not isinstance(payload, dict):
            return []
        selected = payload.get("selected_tools")
        if not isinstance(selected, list):
            return []
        candidate_set = {str(item.get("name") or "").strip() for item in discovered if str(item.get("name") or "").strip()}
        normalized: List[str] = []
        for tool in selected:
            name = str(tool or "").strip()
            if name and name in candidate_set and name not in normalized:
                normalized.append(name)
        return normalized[:max(1, max_selected)]
