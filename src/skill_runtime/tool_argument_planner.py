from typing import Any, Dict, List

from src.skill_runtime.entity_resolver import EntityResolver
from src.skill_runtime.time_resolver import TimeResolver
from src.tools.registry import canonicalize_tool_name, is_tool_definition_disabled


class ToolArgumentPlanner:
    """
    Deterministic argument planner for tool invocation.

    This layer sits between:
    - normalized user/task intent
    - actual tool execution

    It keeps argument construction explainable and testable before any
    LLM-based planner is introduced.
    """

    def __init__(
        self,
        *,
        entity_resolver: EntityResolver | None = None,
        time_resolver: TimeResolver | None = None,
    ) -> None:
        self.entity_resolver = entity_resolver or EntityResolver()
        self.time_resolver = time_resolver or TimeResolver()

    def build_plan(
        self,
        *,
        tool_name: str,
        user_text: str = "",
        context: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        context = context or {}
        normalized_tool_name = canonicalize_tool_name(str(tool_name or "").strip())
        if normalized_tool_name and is_tool_definition_disabled(normalized_tool_name):
            return self._disabled_plan(tool_name=normalized_tool_name)
        entity = self.entity_resolver.resolve(user_text=user_text, context=context)
        time_range = self.time_resolver.resolve(user_text=user_text, context=context)
        normalized = {
            "user_text": str(user_text or "").strip(),
            "question": str(context.get("question") or user_text or "").strip(),
            "query": str(context.get("query") or "").strip(),
            "code": str(context.get("code") or entity.get("code") or "").strip(),
            "name": str(context.get("name") or context.get("company") or entity.get("name") or "").strip(),
            "company": str(context.get("company") or context.get("name") or entity.get("name") or "").strip(),
            "concept": str(context.get("concept") or entity.get("concept") or "").strip(),
            "time_range": time_range,
            "context": context,
        }
        planner = getattr(self, f"_plan_{str(tool_name or '').strip()}", None)
        if callable(planner):
            return planner(normalized)
        if normalized_tool_name == "大盘情绪指标":
            return self._plan_market_sentiment_indicator(normalized)
        if normalized_tool_name == "大盘整体情况":
            return self._plan_market_overview(normalized)
        if normalized_tool_name in {"indicator_series_query", "指标序列查询"}:
            return self._plan_indicator_series(normalized)
        if normalized_tool_name in {"equity_research_search", "stock_reports", "公司研报查询"}:
            return self._plan_equity_research_search(normalized)
        if normalized_tool_name in {"实时个股动量排名", "个股动量排名"}:
            return self._plan_stock_momentum_ranking(normalized)
        if normalized_tool_name == "实时行情排名查询":
            return self._plan_realtime_market_ranking(normalized)
        if normalized_tool_name in {"financial_news_search", "company_news"}:
            return self._plan_financial_news_search(normalized)
        if normalized_tool_name == "theme_leaders":
            return self._plan_theme_leaders(normalized)
        if normalized_tool_name == "get_company_taxonomy_profile":
            return self._plan_company_taxonomy_profile(normalized)
        return self._unsupported_plan(tool_name=str(tool_name or "").strip())

    def _disabled_plan(self, *, tool_name: str) -> Dict[str, Any]:
        plan = self._unsupported_plan(tool_name=tool_name)
        plan["status"] = "disabled"
        plan["notes"] = [f"tool '{tool_name}' 已停用，不再参与参数规划"]
        return plan

    def build_batch(
        self,
        *,
        tool_names: List[str],
        user_text: str = "",
        context: Dict[str, Any] | None = None,
    ) -> List[Dict[str, Any]]:
        return [
            self.build_plan(tool_name=tool_name, user_text=user_text, context=context or {})
            for tool_name in (tool_names or [])
            if str(tool_name or "").strip()
        ]

    def _unsupported_plan(self, *, tool_name: str) -> Dict[str, Any]:
        return {
            "plan_type": "tool_argument_plan",
            "tool_name": tool_name,
            "status": "unsupported",
            "intent": "",
            "execution_profile": "real",
            "resolved_subject": {},
            "arguments": {},
            "missing_arguments": [],
            "required_arguments": [],
            "defaults_applied": {},
            "argument_sources": {},
            "search_plan": None,
            "ranking_plan": None,
            "notes": [f"tool '{tool_name}' 尚未接入参数规划器"],
        }

    def _plan_stock_quote(self, data: Dict[str, Any]) -> Dict[str, Any]:
        code = data.get("code") or ""
        name = data.get("name") or ""
        args = {}
        sources = {}
        missing = []
        if code:
            args["code"] = code
            sources["code"] = "entity_resolver_or_context"
        elif name:
            args["name"] = name
            sources["name"] = "entity_resolver_or_context"
            missing.append("code")
        else:
            missing.append("code_or_name")
        return self._finalize(
            tool_name="stock_quote",
            intent="获取个股行情、分时和历史 K 线",
            execution_profile="real",
            resolved_subject={
                "entity_type": "stock" if (code or name) else "",
                "code": code,
                "name": name,
            },
            arguments=args,
            required_arguments=["code"],
            missing_arguments=missing,
            argument_sources=sources,
            defaults_applied={},
            notes=[],
        )

    def _plan_stock_realtime_quote(self, data: Dict[str, Any]) -> Dict[str, Any]:
        quote_plan = self._plan_stock_quote(data)
        args = {}
        sources = {}
        missing = []
        resolved_subject = quote_plan.get("resolved_subject") or {}
        code = str(resolved_subject.get("code") or "").strip()
        name = str(resolved_subject.get("name") or "").strip()
        if name:
            args["name"] = name
            sources["name"] = "entity_resolver_or_context"
        elif code:
            args["name"] = code
            sources["name"] = "entity_resolver_or_context"
        else:
            missing.append("name")
        return self._finalize(
            tool_name="stock_realtime_quote",
            intent="获取个股实时行情快照",
            execution_profile="real",
            resolved_subject=resolved_subject,
            arguments=args,
            required_arguments=["name"],
            missing_arguments=missing,
            argument_sources=sources,
            defaults_applied={},
            notes=[],
        )

    def _plan_stock_history_kline(self, data: Dict[str, Any]) -> Dict[str, Any]:
        quote_plan = self._plan_stock_realtime_quote(data)
        return self._finalize(
            tool_name="stock_history_kline",
            intent="获取个股历史日K",
            execution_profile="real",
            resolved_subject=quote_plan.get("resolved_subject") or {},
            arguments=quote_plan["arguments"],
            required_arguments=["name"],
            missing_arguments=quote_plan["missing_arguments"],
            argument_sources=quote_plan["argument_sources"],
            defaults_applied={},
            notes=[],
        )

    def _plan_stock_intraday_kline(self, data: Dict[str, Any]) -> Dict[str, Any]:
        quote_plan = self._plan_stock_realtime_quote(data)
        return self._finalize(
            tool_name="stock_intraday_kline",
            intent="获取个股日内分钟K线",
            execution_profile="real",
            resolved_subject=quote_plan.get("resolved_subject") or {},
            arguments=quote_plan["arguments"],
            required_arguments=["name"],
            missing_arguments=quote_plan["missing_arguments"],
            argument_sources=quote_plan["argument_sources"],
            defaults_applied={},
            notes=[],
        )

    def _plan_stock_funds(self, data: Dict[str, Any]) -> Dict[str, Any]:
        quote_plan = self._plan_stock_quote(data)
        args = dict(quote_plan["arguments"])
        sources = dict(quote_plan["argument_sources"])
        return self._finalize(
            tool_name="stock_funds",
            intent="获取资金流向、主力净流和资金新闻",
            execution_profile="real",
            resolved_subject=quote_plan.get("resolved_subject") or {},
            arguments=args,
            required_arguments=["code"],
            missing_arguments=quote_plan["missing_arguments"],
            argument_sources=sources,
            defaults_applied={},
            notes=["工具内部会使用默认资金窗口和新闻抓取策略。"] if not quote_plan["missing_arguments"] else [],
        )

    def _plan_stock_realtime_funds_flow(self, data: Dict[str, Any]) -> Dict[str, Any]:
        quote_plan = self._plan_stock_realtime_quote(data)
        return self._finalize(
            tool_name="stock_realtime_funds_flow",
            intent="获取个股实时资金流向",
            execution_profile="real",
            resolved_subject=quote_plan.get("resolved_subject") or {},
            arguments=quote_plan["arguments"],
            required_arguments=["name"],
            missing_arguments=quote_plan["missing_arguments"],
            argument_sources=quote_plan["argument_sources"],
            defaults_applied={},
            notes=[],
        )

    def _plan_stock_history_funds_flow(self, data: Dict[str, Any]) -> Dict[str, Any]:
        quote_plan = self._plan_stock_realtime_quote(data)
        args = dict(quote_plan["arguments"])
        sources = dict(quote_plan["argument_sources"])
        defaults = {}
        days = data.get("days")
        if days not in {None, ""}:
            args["days"] = days
            sources["days"] = "user_input"
        else:
            defaults["days"] = 20
        return self._finalize(
            tool_name="stock_history_funds_flow",
            intent="获取个股近期资金流向历史",
            execution_profile="real",
            resolved_subject=quote_plan.get("resolved_subject") or {},
            arguments=args,
            required_arguments=["name"],
            missing_arguments=quote_plan["missing_arguments"],
            argument_sources=sources,
            defaults_applied=defaults,
            notes=[],
        )

    def _plan_stock_industry_funds_flow(self, data: Dict[str, Any]) -> Dict[str, Any]:
        quote_plan = self._plan_stock_realtime_quote(data)
        return self._finalize(
            tool_name="stock_industry_funds_flow",
            intent="获取行业同类公司资金情况",
            execution_profile="real",
            resolved_subject=quote_plan.get("resolved_subject") or {},
            arguments=quote_plan["arguments"],
            required_arguments=["name"],
            missing_arguments=quote_plan["missing_arguments"],
            argument_sources=quote_plan["argument_sources"],
            defaults_applied={},
            notes=[],
        )

    def _plan_equity_research_search(self, data: Dict[str, Any]) -> Dict[str, Any]:
        quote_plan = self._plan_stock_quote(data)
        args = {}
        missing_arguments = list(quote_plan["missing_arguments"])
        company_name = str(data.get("company") or data.get("name") or "").strip()
        code = str((quote_plan.get("resolved_subject") or {}).get("code") or "").strip()
        if code:
            args["company"] = code
            missing_arguments = []
        elif company_name:
            args["company"] = company_name
            missing_arguments = []
        sources = {}
        if args.get("company"):
            sources["company"] = "entity_resolver_or_context"
        defaults = {
            "limit": 10,
            "refresh": False
        }
        return self._finalize(
            tool_name="equity_research_search",
            intent="获取个股研报、评级和机构观点",
            execution_profile="real",
            resolved_subject=quote_plan.get("resolved_subject") or {},
            arguments=args,
            required_arguments=["company"],
            missing_arguments=missing_arguments,
            argument_sources=sources,
            defaults_applied=defaults,
            notes=["工具内部会使用默认返回条数和刷新策略。"] if not missing_arguments else [],
        )
    def _plan_financial_news_search(self, data: Dict[str, Any]) -> Dict[str, Any]:
        query = data.get("query") or ""
        code = data.get("code") or ""
        name = data.get("name") or ""
        concept = data.get("concept") or ""
        args = {}
        sources = {}
        defaults = {}
        missing = []
        search_plan = None
        if query:
            args["query"] = query
            sources["query"] = "runtime_binding_or_context"
            search_plan = {
                "query_variants": [query],
                "site_filters": [],
                "time_range": data.get("time_range") or {},
            }
        elif concept:
            args["query"] = concept
            sources["query"] = "entity_resolver_or_context"
            search_plan = {
                "query_variants": [concept, f"{concept} 概念"],
                "site_filters": [],
                "time_range": data.get("time_range") or {},
            }
        elif name or code:
            args["query"] = name or code
            sources["query"] = "entity_resolver_or_context"
            if code:
                sources["resolved_code"] = "entity_resolver_or_context"
            search_plan = {
                "query_variants": [name or code, code] if code and name else [name or code],
                "site_filters": [],
                "time_range": data.get("time_range") or {},
            }
        else:
            missing.append("query")
        args.update(defaults)
        for key in defaults:
            sources[key] = "planner_default"
        return self._finalize(
            tool_name="financial_news_search",
            intent="搜索公司、概念或主题相关新闻",
            execution_profile="real",
            resolved_subject={
                "entity_type": "concept" if concept else "stock" if (name or code) else "",
                "code": code,
                "name": name,
                "concept": concept,
            },
            arguments=args,
            required_arguments=["query"],
            missing_arguments=missing,
            argument_sources=sources,
            defaults_applied=defaults,
            search_plan=search_plan,
            notes=["工具内部会自动识别股票、概念或事件关键词，并使用默认抓取窗口。"] if not missing else [],
        )

    def _plan_theme_leaders(self, data: Dict[str, Any]) -> Dict[str, Any]:
        query = str(data.get("query") or data.get("concept") or data.get("name") or "").strip()
        args = {}
        sources = {}
        missing = []
        if query:
            args["query"] = query
            sources["query"] = "runtime_binding_or_context"
        else:
            missing.append("query")
        return self._finalize(
            tool_name="theme_leaders",
            intent="查询主题、概念或板块的龙头候选",
            execution_profile="real",
            resolved_subject={
                "entity_type": "theme" if query else "",
                "query": query,
            },
            arguments=args,
            required_arguments=["query"],
            missing_arguments=missing,
            argument_sources=sources,
            defaults_applied={},
            notes=["工具内部使用默认排序和返回条数。"] if not missing else [],
        )

    def _plan_company_taxonomy_profile(self, data: Dict[str, Any]) -> Dict[str, Any]:
        query = str(data.get("query") or data.get("name") or data.get("code") or "").strip()
        args = {}
        sources = {}
        missing = []
        if query:
            args["query"] = query
            sources["query"] = "runtime_binding_or_context"
        else:
            missing.append("query")
        return self._finalize(
            tool_name="get_company_taxonomy_profile",
            intent="查询公司所属行业、板块和热点概念",
            execution_profile="real",
            resolved_subject={
                "entity_type": "stock" if query else "",
                "query": query,
            },
            arguments=args,
            required_arguments=["query"],
            missing_arguments=missing,
            argument_sources=sources,
            defaults_applied={},
            notes=[],
        )

    def _plan_market_sentiment_indicator(self, data: Dict[str, Any]) -> Dict[str, Any]:
        defaults = {
            "lookback_days": 20,
            "top_n": 20,
            "ma_window": 5,
        }
        sources = {key: "planner_default" for key in defaults}
        return self._finalize(
            tool_name="大盘情绪指标",
            intent="计算大盘情绪轮动指标",
            execution_profile="real",
            resolved_subject={
                "entity_type": "market",
                "market_scope": "cn_a_share",
                "index_name": "bpwl_brwl_ratio",
            },
            arguments=dict(defaults),
            required_arguments=["history_kline"],
            missing_arguments=["history_kline"],
            argument_sources=sources,
            defaults_applied=dict(defaults),
            notes=["输入应为全市场历史K线矩阵 [[code, date, open, close, high, low, vol, amount], ...]"],
        )

    def _plan_market_overview(self, data: Dict[str, Any]) -> Dict[str, Any]:
        defaults = {
            "lookback_days": 5,
        }
        sources = {key: "planner_default" for key in defaults}
        return self._finalize(
            tool_name="大盘整体情况",
            intent="获取当前大盘涨跌家数、涨跌停家数和成交额概览",
            execution_profile="real",
            resolved_subject={
                "entity_type": "market",
                "market_scope": "cn_a_share",
            },
            arguments=dict(defaults),
            required_arguments=[],
            missing_arguments=[],
            argument_sources=sources,
            defaults_applied=dict(defaults),
            notes=["默认读取最新分钟快照；如上层明确指定时间，可覆写 trade_date 和 minute_index。"],
        )

    def _plan_indicator_series(self, data: Dict[str, Any]) -> Dict[str, Any]:
        defaults = {
            "indicator_ids": ["close_ma_5", "close_ma_20"],
            "subject_codes": ["000001.SH"],
            "range_days": 30,
        }
        sources = {key: "planner_default" for key in defaults}
        return self._finalize(
            tool_name="indicator_series_query",
            intent="查询离线刷新后的指标日序列",
            execution_profile="real",
            resolved_subject={
                "entity_type": "index",
                "subject_codes": ["000001.SH"],
            },
            arguments=dict(defaults),
            required_arguments=["indicator_ids"],
            missing_arguments=[],
            argument_sources=sources,
            defaults_applied=dict(defaults),
            notes=["默认查询上证综指。主体统一优先用标准代码，支持多个指标和多个主体一起查询。"],
        )

    def _plan_stock_momentum_ranking(self, data: Dict[str, Any]) -> Dict[str, Any]:
        defaults = {
            "k": 5,
            "t": 20,
            "universe": "",
        }
        sources = {key: "planner_default" for key in defaults}
        return self._finalize(
            tool_name=str(data.get("context", {}).get("tool_name") or "实时个股动量排名"),
            intent="计算个股动量并返回排名前列的股票",
            execution_profile="real",
            resolved_subject={
                "entity_type": "market",
                "market_scope": "full_market",
            },
            arguments=dict(defaults),
            required_arguments=[],
            missing_arguments=[],
            argument_sources=sources,
            defaults_applied=dict(defaults),
            notes=["动量定义为实时价格相对 K 个交易日前收盘价的涨跌幅"],
        )

    def _plan_realtime_market_ranking(self, data: Dict[str, Any]) -> Dict[str, Any]:
        question = str(data.get("question") or data.get("user_text") or "").strip()
        sort_by = "涨幅"
        if any(token in question for token in ["涨停", "封板"]):
            sort_by = "涨停"
        elif "跌停" in question:
            sort_by = "跌停"
        elif any(token in question for token in ["跌幅", "跌得最多", "跌的最多"]):
            sort_by = "跌幅"
        elif any(token in question for token in ["换手", "换手率"]):
            sort_by = "换手"
        elif any(token in question for token in ["交易量", "成交量"]):
            sort_by = "交易量"
        elif "成交额" in question:
            sort_by = "成交额"

        market = "all"
        if any(token in question for token in ["沪市", "上证", "上海"]):
            market = "sh"
        elif any(token in question for token in ["深市", "深证", "深圳"]):
            market = "sz"
        elif any(token in question for token in ["北交所", "北证", "北京"]):
            market = "bj"

        defaults = {
            "market": market,
            "sort_by": sort_by,
            "top_k": 20,
        }
        return self._finalize(
            tool_name="实时行情排名查询",
            intent="按最新分钟快照查询市场实时行情排名",
            execution_profile="real",
            resolved_subject={
                "entity_type": "market",
                "market": market,
                "sort_by": sort_by,
            },
            arguments=dict(defaults),
            required_arguments=[],
            missing_arguments=[],
            argument_sources={key: "planner_default_or_question_hint" for key in defaults},
            defaults_applied=dict(defaults),
            notes=[],
        )

    def _plan_concept_representatives(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return self._plan_ranked_candidate_tool(
            tool_name="concept_representatives",
            intent="获取概念代表股候选集合",
            topic_value=data.get("concept") or "",
            topic_key="concept",
            data=data,
            default_sort="leadership_score",
        )

    def _plan_hot_topic_stocks(self, data: Dict[str, Any]) -> Dict[str, Any]:
        topic = data.get("concept") or data.get("name") or data.get("question") or ""
        return self._plan_ranked_candidate_tool(
            tool_name="hot_topic_stocks",
            intent="获取热点相关股票候选集合",
            topic_value=topic,
            topic_key="topic",
            data=data,
            default_sort="heat_score",
        )

    def _plan_sector_leaders(self, data: Dict[str, Any]) -> Dict[str, Any]:
        topic = data.get("concept") or data.get("name") or ""
        return self._plan_ranked_candidate_tool(
            tool_name="sector_leaders",
            intent="获取板块龙头候选",
            topic_value=topic,
            topic_key="sector",
            data=data,
            default_sort="leadership_score",
        )

    def _plan_industry_leaders(self, data: Dict[str, Any]) -> Dict[str, Any]:
        topic = data.get("concept") or data.get("name") or ""
        return self._plan_ranked_candidate_tool(
            tool_name="industry_leaders",
            intent="获取行业龙头候选",
            topic_value=topic,
            topic_key="industry",
            data=data,
            default_sort="leadership_score",
        )

    def _plan_ranked_candidate_tool(
        self,
        *,
        tool_name: str,
        intent: str,
        topic_value: str,
        topic_key: str,
        data: Dict[str, Any],
        default_sort: str,
    ) -> Dict[str, Any]:
        value = str(topic_value or "").strip()
        args = {}
        sources = {}
        missing = []
        if value:
            args[topic_key] = value
            sources[topic_key] = "entity_resolver_or_context"
        else:
            missing.append(topic_key)
        defaults = {
            "top_k": 10,
        }
        args.update(defaults)
        sources["top_k"] = "planner_default"
        search_plan = {
            "query_variants": [value] if value else [],
            "site_filters": [],
            "time_range": data.get("time_range") or {},
        }
        ranking_plan = {
            "sort_by": default_sort,
            "score_components": ["market_heat", "volume", "news_mentions"],
            "top_k": 10,
        }
        return self._finalize(
            tool_name=tool_name,
            intent=intent,
            execution_profile="real",
            resolved_subject={
                "entity_type": topic_key,
                topic_key: value,
            },
            arguments=args,
            required_arguments=[topic_key],
            missing_arguments=missing,
            argument_sources=sources,
            defaults_applied=defaults,
            search_plan=search_plan,
            ranking_plan=ranking_plan,
            notes=[],
        )

    def _finalize(
        self,
        *,
        tool_name: str,
        intent: str,
        arguments: Dict[str, Any],
        required_arguments: List[str],
        missing_arguments: List[str],
        argument_sources: Dict[str, str],
        defaults_applied: Dict[str, Any],
        execution_profile: str,
        resolved_subject: Dict[str, Any],
        notes: List[str],
        search_plan: Dict[str, Any] | None = None,
        ranking_plan: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        status = "clarify" if missing_arguments else "ready"
        return {
            "plan_type": "tool_argument_plan",
            "tool_name": tool_name,
            "status": status,
            "intent": intent,
            "execution_profile": str(execution_profile or "real").strip() or "real",
            "resolved_subject": resolved_subject or {},
            "arguments": arguments,
            "missing_arguments": list(missing_arguments or []),
            "required_arguments": list(required_arguments or []),
            "defaults_applied": defaults_applied or {},
            "argument_sources": argument_sources or {},
            "search_plan": search_plan,
            "ranking_plan": ranking_plan,
            "notes": notes or [],
        }
