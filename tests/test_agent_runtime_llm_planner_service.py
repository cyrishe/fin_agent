from src.services.agent_runtime_llm_planner_service import AgentRuntimeLLMPlannerService


class _CapabilitySearchStub:
    def __init__(self, result, *, has_subject_tags=False):
        self.result = result
        self.has_subject_tags = has_subject_tags
        self.last_tool_queries = None
        self.last_work_context = None
        self.last_tool_subject_tags = None

    def find_for_agent_runtime(self, query, work_context=None, application_context=None, tool_top_k=None, skill_top_k=None, tool_queries=None, tool_subject_tags=None):
        self.last_tool_queries = tool_queries
        self.last_work_context = work_context
        self.last_tool_subject_tags = tool_subject_tags
        return self.result

    def has_tool_subject_tags(self, application_context=None):
        return self.has_subject_tags


class _SubjectClassifierStub:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def classify(self, *, task_desc, enable_llm=True):
        self.calls.append({"task_desc": task_desc, "enable_llm": enable_llm})
        return self.result


class _FallbackPlannerStub:
    def build_business_dialog_plan(self, text, work_context=None, application_context=None, tool_queries=None):
        return {
            "objective": text,
            "candidate_skills": [],
            "candidate_tools": [],
            "selected_path": {
                "type": "tool_plan_run",
                "target": {"type": "tool_group", "name": "direct_tools"},
                "reason": "stubbed_fallback",
            },
            "work_items": [
                {"step_id": "step_1", "depends_on": [], "type": "tool", "name": "company_news", "status": "planned"}
            ],
            "presentation_plan": {
                "layout": "report",
                "page_type": "analysis_result",
                "preferred_block_types": ["structured_text"],
                "sections": [],
            },
        }


class _PromptContextCompilerStub:
    def compile_sections(self, **kwargs):
        return [{"section": "stubbed"}]


class _ToolRerankStub:
    def __init__(self, selected_tools):
        self.selected_tools = selected_tools
        self.calls = []

    def rerank(self, *, task_desc, candidate_tools=None, enable_llm=True):
        self.calls.append(
            {
                "task_desc": task_desc,
                "candidate_tools": list(candidate_tools or []),
                "enable_llm": enable_llm,
            }
        )
        return {
            "items": [
                {"tool": item.get("tool_name"), "tag": ("FULL" if item.get("tool_name") in set(self.selected_tools) else "PASS")}
                for item in (candidate_tools or [])
            ],
            "selected_tools": list(self.selected_tools),
            "source": "stub",
            "llm_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }


class _FallbackPlannerCaptureStub(_FallbackPlannerStub):
    def __init__(self):
        self.calls = []

    def build_business_dialog_plan(self, text, recall_query=None, work_context=None, application_context=None, tool_queries=None, enable_llm=True):
        self.calls.append(
            {
                "text": text,
                "recall_query": recall_query,
                "tool_queries": tool_queries,
                "enable_llm": enable_llm,
            }
        )
        return super().build_business_dialog_plan(
            text=text,
            work_context=work_context,
            application_context=application_context,
            tool_queries=tool_queries,
        )


def test_build_plan_keeps_fallback_execution_plan_with_forced_deep_mode():
    capability_result = {
        "skills": [{"skill_name": "market_research", "score": 8}],
        "tools": [
            {"tool_name": "company_news", "score": 6},
            {"tool_name": "stock_funds", "score": 5},
            {"tool_name": "get_hot_sectors_and_leaders", "score": 5},
        ],
    }
    service = AgentRuntimeLLMPlannerService(
        capability_search_service=_CapabilitySearchStub(capability_result),
        fallback_planner=_FallbackPlannerStub(),
        prompt_context_compiler=_PromptContextCompilerStub(),
    )

    result = service.build_plan(
        user_objective="为什么今天大盘下跌，先看新闻和板块，再综合判断原因",
        work_context={"preferred_thinking_mode": "deep"},
        application_context={},
        enable_llm=False,
    )

    assert result["ok"] is True
    assert result["source"] == "fallback_rule_planner"
    assert result["thinking_mode_result"]["thinking_mode"] == "deep_thinking"
    assert result["thinking_mode_result"]["mode_source"] == "forced"
    assert result["task_mode_result"] == {}
    assert result["deep_plan_preview"]["thinking_mode"] == "deep_thinking"
    assert result["planner_question_contract"]["primary_lane"] == "deep_analysis"
    assert result["execution_plan"]["question_contract"]["primary_lane"] == "deep_analysis"
    assert result["execution_plan"]["plan_type"] == "tool_plan_run"
    assert result["execution_plan"]["selected_path"]["reason"] == "stubbed_fallback"


def test_build_plan_uses_fallback_when_deep_planner_returns_empty(monkeypatch):
    capability_result = {
        "skills": [],
        "tools": [{"tool_name": "company_news", "score": 6}],
        "planner_skills": [],
        "planner_tools": [{"tool_name": "company_news", "score": 6}],
    }
    service = AgentRuntimeLLMPlannerService(
        capability_search_service=_CapabilitySearchStub(capability_result),
        fallback_planner=_FallbackPlannerStub(),
        prompt_context_compiler=_PromptContextCompilerStub(),
    )
    monkeypatch.setattr(
        service,
        "_run_high_level_planner",
        lambda **kwargs: {"payload": None, "text": "", "explanation": "", "usage": None},
    )

    result = service.build_plan(
        user_objective="分析今天市场下跌的原因",
        work_context={"preferred_thinking_mode": "deep"},
        application_context={},
        enable_llm=True,
    )

    assert result["ok"] is True
    assert result["source"] == "planner_empty_fallback"
    assert result["planner_question_contract"]["primary_lane"] == "deep_analysis"
    assert result["execution_plan"]["question_contract"]["primary_lane"] == "deep_analysis"
    assert result["execution_plan"]["selected_path"]["reason"] == "stubbed_fallback"


def test_build_plan_routes_simple_queries_to_fast_thinking():
    capability_result = {
        "skills": [{"skill_name": "quote_lookup", "score": 4}],
        "tools": [{"tool_name": "stock_realtime_quote", "score": 6}],
        "planner_tools": [{"tool_name": "stock_realtime_quote", "score": 6}],
    }
    rerank = _ToolRerankStub(["stock_realtime_quote"])
    fallback = _FallbackPlannerCaptureStub()
    service = AgentRuntimeLLMPlannerService(
        capability_search_service=_CapabilitySearchStub(capability_result),
        fallback_planner=fallback,
        prompt_context_compiler=_PromptContextCompilerStub(),
        tool_candidate_rerank_service=rerank,
    )

    result = service.build_plan(
        user_objective="查一下贵州茅台最新股价",
        work_context={},
        application_context={},
        enable_llm=False,
    )

    assert result["ok"] is True
    assert result["source"] == "fast_thinking_planner"
    assert result["thinking_mode_result"]["thinking_mode"] == "fast_thinking"
    assert result["thinking_mode_result"]["mode_source"] == "rule_fallback"
    assert result["task_mode_result"] == {}
    assert result["deep_plan_preview"] == {}
    assert result["planner_question_contract"]["primary_lane"] == "direct_query"
    assert result["execution_plan"]["question_contract"]["primary_lane"] == "direct_query"
    assert result["execution_plan"]["analysis_affordances"]["quant_authoring_affordance"]["available"] is False
    assert rerank.calls[0]["enable_llm"] is True
    assert fallback.calls[0]["enable_llm"] is True


def test_build_plan_passes_security_subject_to_capability_search():
    capability_result = {
        "skills": [],
        "tools": [{"tool_name": "fund_profile_query", "subject_tags": ["fund"]}],
        "planner_skills": [],
        "planner_tools": [{"tool_name": "fund_profile_query", "subject_tags": ["fund"]}],
    }
    capability_search = _CapabilitySearchStub(capability_result, has_subject_tags=True)
    classifier = _SubjectClassifierStub(
        {
            "subject": "fund",
            "subjects": ["fund"],
            "reason": "fund product query",
            "source": "stub",
            "llm_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
    )
    service = AgentRuntimeLLMPlannerService(
        capability_search_service=capability_search,
        security_subject_classifier_service=classifier,
        fallback_planner=_FallbackPlannerCaptureStub(),
        prompt_context_compiler=_PromptContextCompilerStub(),
        tool_candidate_rerank_service=_ToolRerankStub(["fund_profile_query"]),
    )

    result = service.build_plan(
        user_objective="红利低波动基金有哪些",
        work_context={"planner_thinking_mode": "fast"},
        application_context={},
        enable_llm=True,
    )

    assert result["subject_result"]["subject"] == "fund"
    assert capability_search.last_tool_subject_tags == ["fund"]
    assert classifier.calls == [{"task_desc": "红利低波动基金有哪些", "enable_llm": True}]


def test_build_plan_auto_mode_uses_llm_thinking_router(monkeypatch):
    capability_result = {
        "skills": [],
        "tools": [{"tool_name": "stock_quote", "score": 6}],
        "planner_skills": [],
        "planner_tools": [{"tool_name": "stock_quote", "score": 6}],
    }
    service = AgentRuntimeLLMPlannerService(
        capability_search_service=_CapabilitySearchStub(capability_result),
        fallback_planner=_FallbackPlannerStub(),
        prompt_context_compiler=_PromptContextCompilerStub(),
    )
    prompt_keys = []

    def _render_messages(prompt_key, variables):
        prompt_keys.append(prompt_key)
        return []

    monkeypatch.setattr(service.registry, "render_messages", _render_messages)
    monkeypatch.setattr(
        "src.services.agent_runtime_llm_planner_service.chat_qwen",
        lambda messages, enable_think=False: (
            '{"thinking_mode":"fast_thinking","reason":"direct metric lookup","confidence":0.8}',
            {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        ),
    )

    result = service.build_plan(
        user_objective="查一下贵州茅台最新股价",
        work_context={"planner_thinking_mode": "auto"},
        application_context={},
        enable_llm=True,
    )

    assert "system.agent_runtime.thinking_mode_router" in prompt_keys
    assert result["source"] == "fast_thinking_planner"
    assert result["thinking_mode_result"]["thinking_mode"] == "fast_thinking"
    assert result["thinking_mode_result"]["mode_source"] == "auto_llm"


def test_build_plan_routes_list_lookup_to_fast_thinking_without_mode_llm():
    capability_result = {
        "skills": [],
        "tools": [{"tool_name": "fund_profile_query", "score": 6}],
        "planner_skills": [],
        "planner_tools": [{"tool_name": "fund_profile_query", "score": 6}],
    }
    rerank = _ToolRerankStub(["fund_profile_query"])
    service = AgentRuntimeLLMPlannerService(
        capability_search_service=_CapabilitySearchStub(capability_result),
        fallback_planner=_FallbackPlannerStub(),
        prompt_context_compiler=_PromptContextCompilerStub(),
        tool_candidate_rerank_service=rerank,
    )

    result = service.build_plan(
        user_objective="红利低波动基金有哪些",
        work_context={"planner_thinking_mode": "auto"},
        application_context={},
        enable_llm=False,
    )

    assert result["source"] == "fast_thinking_planner"
    assert result["thinking_mode_result"]["thinking_mode"] == "fast_thinking"
    assert result["thinking_mode_result"]["mode_source"] == "rule_fallback"
    assert rerank.calls[0]["enable_llm"] is True


def test_build_plan_keeps_quant_capabilities_as_analysis_affordances_only():
    capability_result = {
        "skills": [],
        "tools": [{"tool_name": "company_news", "score": 6}],
        "planner_skills": [],
        "planner_tools": [{"tool_name": "company_news", "purpose": "查询公司新闻"}],
        "planner_quant_capabilities": [
            {
                "capability_id": "stock_momentum_ranking_pipeline",
                "version": "v1",
                "display_name": "Stock Momentum Ranking Pipeline",
                "capability_type": "strategy_pipeline",
                "purpose": "发布态动量策略筛选能力",
                "best_for": ["published_quant_strategy"],
                "params_schema": {"type": "object"},
                "spec_refs": [{"kind": "strategy", "id": "stock_momentum_ranking", "version": "v1"}],
                "execution_policy": {
                    "mode": "published_entrypoint_only",
                    "read_only_candidate": True,
                    "direct_execution": False,
                },
            }
        ],
    }
    service = AgentRuntimeLLMPlannerService(
        capability_search_service=_CapabilitySearchStub(capability_result),
        fallback_planner=_FallbackPlannerStub(),
        prompt_context_compiler=_PromptContextCompilerStub(),
    )

    result = service.build_plan(
        user_objective="我想用已经发布的动量策略看看候选股票",
        work_context={},
        application_context={},
        enable_llm=False,
    )

    execution_plan = result["execution_plan"]
    assert result["planner_question_contract"]["primary_lane"] == "deep_analysis"
    assert execution_plan["question_contract"]["hard_constraints"]["no_direct_quant_execution_without_confirmation"] is True
    assert execution_plan["candidate_quant_capabilities"][0]["capability_id"] == "stock_momentum_ranking_pipeline"
    assert execution_plan["analysis_affordances"]["upgrade_options"][0] == {
        "mode": "published_quant_research",
        "capability_id": "stock_momentum_ranking_pipeline",
        "display_name": "Stock Momentum Ranking Pipeline",
        "capability_type": "strategy_pipeline",
        "requires_confirmation": True,
        "execution": "not_planned",
    }
    assert execution_plan["presentation_plan"]["analysis_affordances"]["quant_research_capabilities"][0]["capability_id"] == "stock_momentum_ranking_pipeline"
    assert execution_plan["selected_tools"] == ["company_news"]
    assert all(item.get("name") != "stock_momentum_ranking_pipeline" for item in execution_plan["work_items"])


def test_build_plan_filters_candidate_tools_by_rerank_before_planner():
    capability_result = {
        "skills": [],
        "tools": [
            {"tool_name": "stock_realtime_quote", "score": 0.9},
            {"tool_name": "financial_news_search", "score": 0.7},
        ],
        "planner_skills": [],
        "planner_tools": [
            {"tool_name": "stock_realtime_quote", "score": 0.9},
            {"tool_name": "financial_news_search", "score": 0.7},
        ],
    }
    service = AgentRuntimeLLMPlannerService(
        capability_search_service=_CapabilitySearchStub(capability_result),
        fallback_planner=_FallbackPlannerStub(),
        prompt_context_compiler=_PromptContextCompilerStub(),
        tool_candidate_rerank_service=_ToolRerankStub(["stock_realtime_quote", "financial_news_search"]),
    )

    result = service.build_plan(
        user_objective="查一下贵州茅台的行情和资金流向",
        work_context={},
        application_context={},
        enable_llm=False,
    )

    tools = result["capability_result"]["planner_tools"]
    assert [item["tool_name"] for item in tools] == ["stock_realtime_quote", "financial_news_search"]
    assert result["capability_result"]["tool_rerank"]["selected_tools"] == ["stock_realtime_quote", "financial_news_search"]


def test_build_plan_exposes_quant_authoring_affordance_without_work_item():
    capability_result = {
        "skills": [],
        "tools": [],
        "planner_skills": [],
        "planner_tools": [],
    }
    service = AgentRuntimeLLMPlannerService(
        capability_search_service=_CapabilitySearchStub(capability_result),
        fallback_planner=_FallbackPlannerStub(),
        prompt_context_compiler=_PromptContextCompilerStub(),
    )

    result = service.build_plan(
        user_objective="让chatbi生成一个动量策略结果查询SQL模板，并保存为后续可复用skill",
        work_context={},
        application_context={},
        enable_llm=False,
    )

    affordance = result["execution_plan"]["analysis_affordances"]["quant_authoring_affordance"]
    assert result["planner_question_contract"]["primary_lane"] == "skill_lifecycle"
    assert affordance["available"] is True
    assert [item["target_type"] for item in affordance["draft_targets"]] == [
        "strategy_draft",
        "sql_template_draft",
    ]
    assert affordance["can_execute_without_confirmation"] is False
    assert all(item.get("name") not in {"QuantStrategyAuthoringContract", "SqlTemplateSpec"} for item in result["execution_plan"]["work_items"])


def test_build_plan_passes_tool_queries_to_capability_search():
    capability_search = _CapabilitySearchStub(
        {
            "skills": [],
            "tools": [],
            "planner_skills": [],
            "planner_tools": [],
        }
    )
    service = AgentRuntimeLLMPlannerService(
        capability_search_service=capability_search,
        fallback_planner=_FallbackPlannerStub(),
        prompt_context_compiler=_PromptContextCompilerStub(),
    )

    service.build_plan(
        user_objective="先找龙头，再查新闻",
        tool_queries=["找龙头", "查新闻"],
        work_context={},
        application_context={},
        enable_llm=False,
    )

    assert capability_search.last_tool_queries == ["找龙头", "查新闻"]


def test_build_plan_keeps_fallback_shape_without_forced_planned_promotion():
    capability_result = {
        "skills": [{"skill_name": "compare_summary", "score": 4}],
        "tools": [
            {"tool_name": "theme_leaders", "score": 6},
            {"tool_name": "company_news", "score": 5},
        ],
    }
    service = AgentRuntimeLLMPlannerService(
        capability_search_service=_CapabilitySearchStub(capability_result),
        fallback_planner=_FallbackPlannerStub(),
        prompt_context_compiler=_PromptContextCompilerStub(),
    )

    result = service.build_plan(
        user_objective="搜集热点龙头并比较差异",
        work_context={"preferred_task_mode": "planned"},
        application_context={},
        enable_llm=False,
    )

    execution_plan = result["execution_plan"]
    assert result["task_mode_result"] == {}
    assert execution_plan["plan_type"] == "tool_plan_run"
    assert execution_plan["selected_path"]["type"] == "tool_plan_run"
    assert execution_plan["selected_path"]["target"]["type"] == "tool_group"
    assert execution_plan["planned_dag"] == {}


def test_build_plan_uses_llm_plan_mode_when_present(monkeypatch):
    capability_result = {
        "skills": [],
        "tools": [
            {"tool_name": "实时行情排名查询", "score": 0.8},
            {"tool_name": "company_news", "score": 0.7},
        ],
        "planner_skills": [],
        "planner_tools": [],
    }
    service = AgentRuntimeLLMPlannerService(
        capability_search_service=_CapabilitySearchStub(capability_result),
        fallback_planner=_FallbackPlannerStub(),
        prompt_context_compiler=_PromptContextCompilerStub(),
    )
    monkeypatch.setattr(service.registry, "render_messages", lambda *args, **kwargs: [])
    responses = iter(
        [
            (
                """规划说明：
我要完成的任务是：涨停的公司中，成交量最大的公司的新闻
结合当前可用工具：
- 实时行情排名查询：返回最新分钟行情榜单
- company_news：查询相关新闻
我认为可以采用如下步骤：
1. 第一步：筛出候选股票
   目的：先拿到可比较的股票列表
   依赖：无
   产出：股票列表
   可选工具：实时行情排名查询
2. 第二步：查询目标公司新闻
   目的：获取目标公司的新闻
   依赖：第一步的目标公司名称
   产出：新闻列表
   可选工具：company_news
{
  "objective": "涨停的公司中，成交量最大的公司的新闻",
  "plan_mode": "planned",
  "plan_type": "planned_run",
  "selected_tools": ["实时行情排名查询", "company_news"],
  "work_items": [
    {
      "step_id": "step_1",
      "intent": "筛选候选股票",
      "depends_on": [],
      "type": "tool",
      "name": "获取候选股票列表",
      "status": "planned",
      "tool_candidates": ["实时行情排名查询"],
      "produces": ["候选股票列表"]
    },
    {
      "step_id": "step_2",
      "intent": "查询公司新闻",
      "depends_on": ["step_1"],
      "type": "tool",
      "name": "查询目标公司新闻",
      "status": "planned",
      "tool_candidates": ["company_news"],
      "consumes": ["目标公司"],
      "produces": ["新闻列表"]
    }
  ],
  "reason": "llm_planned_stub"
}""",
                {},
            ),
            (
                """{
  "objective": "涨停的公司中，成交量最大的公司的新闻",
  "plan_mode": "planned",
  "plan_type": "planned_run",
  "selected_tools": ["实时行情排名查询", "company_news"],
  "work_items": [
    {
      "step_id": "step_1",
      "intent": "筛选候选股票",
      "depends_on": [],
      "type": "tool",
      "name": "实时行情排名查询",
      "status": "planned",
      "tool_candidates": ["实时行情排名查询"],
      "produces": ["候选股票列表"],
      "output_binding": {
        "候选股票列表": "$result.data",
        "目标公司代码": "$result.data.0.stock_code",
        "目标公司名称": "$result.data.0.stock_name"
      }
    },
    {
      "step_id": "step_2",
      "intent": "查询公司新闻",
      "depends_on": ["step_1"],
      "type": "tool",
      "name": "company_news",
      "status": "planned",
      "tool_candidates": ["company_news"],
      "consumes": ["目标公司"],
      "produces": ["新闻列表"],
      "input_binding": {
        "code": "$目标公司代码",
        "query": "$目标公司名称"
      }
    }
  ],
  "reason": "llm_planned_stub"
}""",
                {},
            ),
        ]
    )
    monkeypatch.setattr(
        "src.services.agent_runtime_llm_planner_service.chat_qwen",
        lambda messages, enable_think=False: next(responses),
    )

    result = service.build_plan(
        user_objective="涨停的公司中，成交量最大的公司的新闻",
        work_context={},
        application_context={},
        enable_llm=True,
    )

    execution_plan = result["execution_plan"]
    assert execution_plan["task_mode"] == "planned"
    assert execution_plan["plan_type"] == "planned_run"
    assert execution_plan["selected_path"]["type"] == "planned_run"
    assert execution_plan["selected_tools"] == ["实时行情排名查询", "company_news"]
    assert execution_plan["work_items"][1]["depends_on"] == ["step_1"]
    assert execution_plan["work_items"][0]["intent"] == "筛选候选股票"
    assert execution_plan["work_items"][1]["input_binding"]["query"] == "$目标公司名称"
    assert "规划说明" in result["planner_explanation"]
    assert result["raw_high_level_plan"]["work_items"][0]["name"] == "获取候选股票列表"
    assert result["raw_compiled_plan"]["work_items"][0]["name"] == "实时行情排名查询"


def test_normalize_execution_plan_accepts_item_type_and_item_action():
    service = AgentRuntimeLLMPlannerService(
        capability_search_service=_CapabilitySearchStub({"skills": [], "tools": [], "planner_skills": [], "planner_tools": []}),
        fallback_planner=_FallbackPlannerStub(),
        prompt_context_compiler=_PromptContextCompilerStub(),
    )

    normalized = service._normalize_execution_plan(
        payload={
            "objective": "从涨停股里找成交量最大的股票再查新闻",
            "plan_mode": "planned",
            "plan_type": "planned_run",
            "selected_tools": ["实时行情排名查询", "financial_news_search"],
            "work_items": [
                {
                    "step_id": "step_1",
                    "intent": "找涨停股列表",
                    "depends_on": [],
                    "item_type": "tool",
                    "item_action": "实时行情排名查询",
                    "status": "planned",
                    "input_binding": {"sort_by": "涨停", "top_k": 50},
                    "output_binding": {"limit_up_stock_list": "$result.data"},
                },
                {
                    "step_id": "step_2",
                    "intent": "取成交量最大的股票",
                    "depends_on": ["step_1"],
                    "item_type": "transform",
                    "item_action": "$input | sort(@.volume desc) | top(1) | first() | project(target_stock_name=@.stock_name)",
                    "status": "planned",
                    "input_binding": {"input": "${step_1.limit_up_stock_list}"},
                    "output_binding": {"target_stock_name": "$result.target_stock_name"},
                },
            ],
        },
        fallback_plan={},
        agent_context={},
    )

    assert normalized["work_items"][0]["type"] == "tool"
    assert normalized["work_items"][0]["name"] == "实时行情排名查询"
    assert normalized["work_items"][0]["item_type"] == "tool"
    assert normalized["work_items"][0]["item_action"] == "实时行情排名查询"
    assert normalized["work_items"][0]["execution_mode"] == "direct"
    assert normalized["work_items"][1]["type"] == "transform"
    assert normalized["work_items"][1]["name"].startswith("$input | sort")
    assert normalized["work_items"][1]["transform_spec"]["dsl"].startswith("$input | sort")


def test_normalize_execution_plan_accepts_code_work_item():
    service = AgentRuntimeLLMPlannerService(
        capability_search_service=_CapabilitySearchStub({"skills": [], "tools": [], "planner_skills": [], "planner_tools": []}),
        fallback_planner=_FallbackPlannerStub(),
        prompt_context_compiler=_PromptContextCompilerStub(),
    )

    normalized = service._normalize_execution_plan(
        payload={
            "objective": "用 Python 对上游表格聚合",
            "plan_mode": "planned",
            "plan_type": "planned_run",
            "work_items": [
                {
                    "step_id": "step_1",
                    "intent": "聚合表格",
                    "item_type": "code",
                    "item_action": "analysis_python",
                    "depends_on": ["step_0"],
                    "runtime_profile": "analysis_python_v1",
                    "code_task_spec": {
                        "task_kind": "table_analysis",
                        "solution_mode": "generated_inline",
                        "entrypoint": "run",
                        "code": "print('ok')",
                    },
                    "input_binding": {"rows": "${step_0.rows}"},
                    "output_binding": {"analysis_table": "$result.data.structured_data.analysis_table"},
                }
            ],
        },
        fallback_plan={},
        agent_context={},
    )

    item = normalized["work_items"][0]
    assert item["type"] == "code"
    assert item["item_type"] == "code"
    assert item["name"] == "analysis_python"
    assert item["runtime_profile"] == "analysis_python_v1"
    assert item["code_task_spec"]["task_kind"] == "table_analysis"


def test_validate_plan_contracts_accepts_code_output_binding():
    service = AgentRuntimeLLMPlannerService(
        capability_search_service=_CapabilitySearchStub({"skills": [], "tools": [], "planner_skills": [], "planner_tools": []}),
        fallback_planner=_FallbackPlannerStub(),
        prompt_context_compiler=_PromptContextCompilerStub(),
    )

    errors = service._validate_plan_contracts(
        {
            "objective": "用 Python 聚合表格",
            "plan_mode": "planned",
            "plan_type": "planned_run",
            "work_items": [
                {
                    "step_id": "step_1",
                    "item_type": "code",
                    "item_action": "analysis_python",
                    "input_binding": {"rows": [{"name": "A"}]},
                    "output_binding": {"analysis_table": "$result.data.structured_data.analysis_table"},
                    "code_task_spec": {"code": "print('ok')"},
                }
            ],
        }
    )

    assert errors == []


def test_normalize_execution_plan_maps_legacy_foreach_to_tool_execution_mode():
    service = AgentRuntimeLLMPlannerService(
        capability_search_service=_CapabilitySearchStub({"skills": [], "tools": [], "planner_skills": [], "planner_tools": []}),
        fallback_planner=_FallbackPlannerStub(),
        prompt_context_compiler=_PromptContextCompilerStub(),
    )

    normalized = service._normalize_execution_plan(
        payload={
            "objective": "逐个查询两只股票的新闻",
            "plan_mode": "planned",
            "plan_type": "planned_run",
            "selected_tools": ["financial_news_search"],
            "work_items": [
                {
                    "step_id": "step_1",
                    "intent": "逐个查询新闻",
                    "depends_on": ["step_0"],
                    "item_type": "foreach",
                    "item_action": "financial_news_search",
                    "status": "planned",
                    "foreach_binding": {"items": "${step_0.top2_stocks}"},
                    "input_binding": {"query": "${item.stock_name}"},
                    "output_binding": {"news_list": "$result"},
                }
            ],
        },
        fallback_plan={},
        agent_context={},
    )

    assert normalized["work_items"][0]["type"] == "tool"
    assert normalized["work_items"][0]["item_type"] == "tool"
    assert normalized["work_items"][0]["name"] == "financial_news_search"
    assert normalized["work_items"][0]["execution_mode"] == "foreach"


def test_validate_plan_contracts_catches_transform_tool_style_input_key():
    service = AgentRuntimeLLMPlannerService(
        capability_search_service=_CapabilitySearchStub({"skills": [], "tools": [], "planner_skills": [], "planner_tools": []}),
        fallback_planner=_FallbackPlannerStub(),
        prompt_context_compiler=_PromptContextCompilerStub(),
    )

    errors = service._validate_plan_contracts(
        {
            "objective": "从概念列表中取第一个概念",
            "plan_mode": "planned",
            "plan_type": "planned_run",
            "work_items": [
                {
                    "step_id": "step_1",
                    "item_type": "transform",
                    "item_action": "$input | first() | project(query=@)",
                    "input_binding": {"query": "${step_0.concept_list}"},
                    "output_binding": {"selected_concept": "$result.query"},
                }
            ],
        }
    )

    assert any("transform step should not use tool-style input key `query`" in item["message"] for item in errors)


def test_validate_plan_contracts_catches_tool_output_binding_non_result_expr():
    service = AgentRuntimeLLMPlannerService(
        capability_search_service=_CapabilitySearchStub({"skills": [], "tools": [], "planner_skills": [], "planner_tools": []}),
        fallback_planner=_FallbackPlannerStub(),
        prompt_context_compiler=_PromptContextCompilerStub(),
    )

    errors = service._validate_plan_contracts(
        {
            "objective": "读取画像并导出首个概念",
            "plan_mode": "planned",
            "plan_type": "planned_run",
            "work_items": [
                {
                    "step_id": "step_1",
                    "item_type": "tool",
                    "item_action": "get_company_taxonomy_profile",
                    "input_binding": {"query": "工业富联"},
                    "output_binding": {"primary_concept": "$input.data.concepts.0"},
                }
            ],
        }
    )

    assert any("output expr `$input.data.concepts.0` is not compatible" in item["message"] for item in errors)


def test_validate_plan_contracts_catches_transform_array_expectation_mismatch():
    service = AgentRuntimeLLMPlannerService(
        capability_search_service=_CapabilitySearchStub({"skills": [], "tools": [], "planner_skills": [], "planner_tools": []}),
        fallback_planner=_FallbackPlannerStub(),
        prompt_context_compiler=_PromptContextCompilerStub(),
    )

    errors = service._validate_plan_contracts(
        {
            "objective": "从龙头结果中取前二",
            "plan_mode": "planned",
            "plan_type": "planned_run",
            "work_items": [
                {
                    "step_id": "step_1",
                    "item_type": "tool",
                    "item_action": "实时行情排名查询",
                    "input_binding": {"sort_by": "成交额"},
                    "output_binding": {"leader_name": "$result.data.0.name"},
                },
                {
                    "step_id": "step_2",
                    "item_type": "transform",
                    "item_action": "$input | top(2) | project(stock_code=@.stock_code)",
                    "input_binding": {"$input": "${step_1.leader_name}"},
                    "output_binding": {"top2_stocks": "$result"},
                },
            ],
        }
    )

    assert any("transform DSL expects list-like `$input`" in item["message"] for item in errors)


def test_validate_plan_contracts_catches_unsupported_binding_syntax():
    service = AgentRuntimeLLMPlannerService(
        capability_search_service=_CapabilitySearchStub({"skills": [], "tools": [], "planner_skills": [], "planner_tools": []}),
        fallback_planner=_FallbackPlannerStub(),
        prompt_context_compiler=_PromptContextCompilerStub(),
    )

    errors = service._validate_plan_contracts(
        {
            "objective": "从概念列表中取第一个概念并查询龙头",
            "plan_mode": "planned",
            "plan_type": "planned_run",
            "work_items": [
                {
                    "step_id": "step_1",
                    "item_type": "tool",
                    "item_action": "实时行情排名查询",
                    "input_binding": {"sort_by": "$input.step_1.concepts_list[0]"},
                }
            ],
        }
    )

    assert any("supported binding syntax" in item["message"] for item in errors)


def test_validate_plan_contracts_catches_stringified_json_in_foreach_items():
    service = AgentRuntimeLLMPlannerService(
        capability_search_service=_CapabilitySearchStub({"skills": [], "tools": [], "planner_skills": [], "planner_tools": []}),
        fallback_planner=_FallbackPlannerStub(),
        prompt_context_compiler=_PromptContextCompilerStub(),
    )

    errors = service._validate_plan_contracts(
        {
            "objective": "对两只股票逐个查新闻",
            "plan_mode": "planned",
            "plan_type": "planned_run",
            "work_items": [
                {
                    "step_id": "step_1",
                    "item_type": "tool",
                    "item_action": "financial_news_search",
                    "execution_mode": "foreach",
                    "foreach_binding": {
                        "items": "[{\"query\": \"${step_0.name}\"}]"
                    },
                    "input_binding": {"query": "${item.query}"},
                }
            ],
        }
    )

    assert any("foreach_binding.items" in item["message"] for item in errors)


def test_apply_rule_repairs_promotes_single_transform_input_to_dollar_input():
    service = AgentRuntimeLLMPlannerService(
        capability_search_service=_CapabilitySearchStub({"skills": [], "tools": [], "planner_skills": [], "planner_tools": []}),
        fallback_planner=_FallbackPlannerStub(),
        prompt_context_compiler=_PromptContextCompilerStub(),
    )

    repaired = service._apply_rule_repairs(
        {
            "work_items": [
                {
                    "step_id": "step_1",
                    "item_type": "transform",
                    "item_action": "$input | first() | project(target=$input)",
                    "input_binding": {"query": "${step_0.concept_list}"},
                    "output_binding": {"target": "$result.target"},
                }
            ]
        }
    )

    assert repaired["work_items"][0]["input_binding"] == {"$input": "${step_0.concept_list}"}


def test_validate_plan_contracts_allows_array_index_binding_on_exported_scalar_list():
    service = AgentRuntimeLLMPlannerService(
        capability_search_service=_CapabilitySearchStub({"skills": [], "tools": [], "planner_skills": [], "planner_tools": []}),
        fallback_planner=_FallbackPlannerStub(),
        prompt_context_compiler=_PromptContextCompilerStub(),
    )

    errors = service._validate_plan_contracts(
        {
            "objective": "选择第一个概念",
            "plan_mode": "planned",
            "plan_type": "planned_run",
            "work_items": [
                {
                    "step_id": "step_1",
                    "item_type": "tool",
                    "item_action": "get_company_taxonomy_profile",
                    "input_binding": {"stock": "工业富联"},
                    "output_binding": {"plate_list": "$result.data.plates"},
                },
                {
                    "step_id": "step_2",
                    "item_type": "tool",
                    "item_action": "finance_data_query",
                    "input_binding": {"request": "${step_1.plate_list[0]}"},
                },
            ],
        }
    )

    assert errors == []


def test_validate_plan_contracts_allows_transform_project_after_first_on_scalar_list():
    service = AgentRuntimeLLMPlannerService(
        capability_search_service=_CapabilitySearchStub({"skills": [], "tools": [], "planner_skills": [], "planner_tools": []}),
        fallback_planner=_FallbackPlannerStub(),
        prompt_context_compiler=_PromptContextCompilerStub(),
    )

    errors = service._validate_plan_contracts(
        {
            "objective": "选择第一个概念并查询龙头",
            "plan_mode": "planned",
            "plan_type": "planned_run",
            "work_items": [
                {
                    "step_id": "step_1",
                    "item_type": "tool",
                    "item_action": "get_company_taxonomy_profile",
                    "input_binding": {"stock": "工业富联"},
                    "output_binding": {"plate_list": "$result.data.plates"},
                },
                {
                    "step_id": "step_2",
                    "item_type": "transform",
                    "item_action": "$input | first() | project(query=@)",
                    "input_binding": {"$input": "${step_1.plate_list}"},
                    "output_binding": {"target_concept": "$result.query"},
                },
                {
                    "step_id": "step_3",
                    "item_type": "tool",
                    "item_action": "finance_data_query",
                    "input_binding": {"request": "${step_2.target_concept}"},
                },
            ],
        }
    )

    assert errors == []


def test_validate_plan_contracts_catches_transform_output_binding_on_scalar_result():
    service = AgentRuntimeLLMPlannerService(
        capability_search_service=_CapabilitySearchStub({"skills": [], "tools": [], "planner_skills": [], "planner_tools": []}),
        fallback_planner=_FallbackPlannerStub(),
        prompt_context_compiler=_PromptContextCompilerStub(),
    )

    errors = service._validate_plan_contracts(
        {
            "objective": "选择第一个概念",
            "plan_mode": "planned",
            "plan_type": "planned_run",
            "work_items": [
                {
                    "step_id": "step_1",
                    "item_type": "tool",
                    "item_action": "get_company_taxonomy_profile",
                    "input_binding": {"query": "工业富联"},
                    "output_binding": {"concept_list": "$result.data.concepts"},
                },
                {
                    "step_id": "step_2",
                    "item_type": "transform",
                    "item_action": "$input | first()",
                    "input_binding": {"$input": "${step_1.concept_list}"},
                    "output_binding": {"target_concept": "$result.query"},
                },
            ],
        }
    )

    assert any("transform output expr `$result.query` is not compatible" in item["message"] for item in errors)


def test_normalize_execution_plan_derives_selected_tools_from_work_items():
    service = AgentRuntimeLLMPlannerService(
        capability_search_service=_CapabilitySearchStub({"skills": [], "tools": [], "planner_skills": [], "planner_tools": []}),
        fallback_planner=_FallbackPlannerStub(),
        prompt_context_compiler=_PromptContextCompilerStub(),
    )

    normalized = service._normalize_execution_plan(
        payload={
            "objective": "查询主题龙头并补新闻",
            "plan_mode": "planned",
            "plan_type": "planned_run",
            "work_items": [
                {
                    "step_id": "step_1",
                    "item_type": "tool",
                    "item_action": "finance_data_query",
                    "input_binding": {"query": "机器人概念龙头"},
                    "output_binding": {"rows": "$result.data"},
                },
                {
                    "step_id": "step_2",
                    "item_type": "tool",
                    "item_action": "financial_news_search",
                    "execution_mode": "foreach",
                    "foreach_binding": {"items": "${step_1.leaders}"},
                    "input_binding": {"query": "${item.stock_name}"},
                    "output_binding": {"news_list": "$result.data"},
                },
            ],
        },
        fallback_plan={},
        agent_context={},
    )

    assert normalized["selected_tools"] == ["finance_data_query", "financial_news_search"]


def test_normalize_execution_plan_filters_disabled_theme_leaders():
    service = AgentRuntimeLLMPlannerService(
        capability_search_service=_CapabilitySearchStub({"skills": [], "tools": [], "planner_skills": [], "planner_tools": []}),
        fallback_planner=_FallbackPlannerStub(),
        prompt_context_compiler=_PromptContextCompilerStub(),
    )

    normalized = service._normalize_execution_plan(
        payload={
            "objective": "查询主题龙头",
            "plan_type": "tool_plan_run",
            "work_items": [
                {
                    "step_id": "step_1",
                    "item_type": "tool",
                    "item_action": "theme_leaders",
                    "input_binding": {"query": "机器人"},
                }
            ],
        },
        fallback_plan={},
        agent_context={},
    )

    assert normalized["selected_tools"] == []
    assert normalized["work_items"] == []


def test_format_planner_tool_summaries_keeps_only_lightweight_fields():
    service = AgentRuntimeLLMPlannerService(
        capability_search_service=_CapabilitySearchStub({"skills": [], "tools": [], "planner_skills": [], "planner_tools": []}),
        fallback_planner=_FallbackPlannerStub(),
        prompt_context_compiler=_PromptContextCompilerStub(),
    )

    text = service._format_planner_tool_summaries(
        [
            {
                "tool_name": "finance_data_query",
                "display_name": "金融数据查询",
                "purpose": "按金融数据协议查询行情、资金、估值等数据",
                "best_for": ["结构化金融查询"],
                "required_inputs": ["query"],
                "output_fields": ["data[].stock_code"],
            }
        ]
    )

    assert "tool_name: finance_data_query" in text
    assert "purpose: 按金融数据协议查询行情、资金、估值等数据" in text
    assert "best_for: 结构化金融查询" in text
    assert "required_inputs" not in text
    assert "output_fields" not in text


def test_collect_plan_tool_contracts_builds_structured_field_level_schema():
    capability_result = {
        "skills": [],
        "tools": [],
        "planner_skills": [],
        "planner_tools": [
            {
                "tool_name": "finance_data_query",
                "display_name": "金融数据查询",
                "purpose": "按金融数据协议查询行情、资金、估值等数据",
                "best_for": ["结构化金融查询"],
                "input_notes": ["query 传概念名"],
            }
        ],
    }
    service = AgentRuntimeLLMPlannerService(
        capability_search_service=_CapabilitySearchStub(capability_result),
        fallback_planner=_FallbackPlannerStub(),
        prompt_context_compiler=_PromptContextCompilerStub(),
    )

    contracts = service._collect_plan_tool_contracts(
        high_level_plan={
            "work_items": [
                {"tool_candidates": ["finance_data_query"]},
            ]
        },
        planner_tools=capability_result["planner_tools"],
    )

    assert contracts[0]["tool_name"] == "finance_data_query"
    assert any(field["name"] == "request" for field in contracts[0]["input_schema"])


def test_build_plan_accepts_natural_language_high_level_plan_for_second_stage(monkeypatch):
    capability_result = {
        "skills": [],
        "tools": [
            {"tool_name": "finance_data_query", "score": 0.8},
            {"tool_name": "company_news", "score": 0.7},
        ],
        "planner_skills": [],
        "planner_tools": [
            {
                "tool_name": "finance_data_query",
                "purpose": "按金融数据协议查询热点主题、行情和成分股",
                "best_for": ["热点龙头筛选"],
                "required_inputs": ["concept"],
                "optional_inputs": ["top_k"],
                "input_notes": [],
                "output_fields": ["data.items", "data.items[].leader_name"],
            },
            {
                "tool_name": "company_news",
                "purpose": "查询公司新闻",
                "best_for": ["公司新闻补充"],
                "required_inputs": ["query"],
                "optional_inputs": ["code"],
                "input_notes": [],
                "output_fields": ["data.items"],
            },
        ],
    }
    service = AgentRuntimeLLMPlannerService(
        capability_search_service=_CapabilitySearchStub(capability_result),
        fallback_planner=_FallbackPlannerStub(),
        prompt_context_compiler=_PromptContextCompilerStub(),
    )
    monkeypatch.setattr(service.registry, "render_messages", lambda *args, **kwargs: [])
    responses = iter(
        [
            (
                """分析过程：
先找到热点龙头，再补该公司的新闻。
执行步骤：
Task-Mode: Planed
Excute-Plan:
Step1
目标：找到热点龙头
可能的工具：finance_data_query
得到的结果：目标公司列表

Step2
目标：查询目标公司新闻
可能的工具：company_news
得到的结果：新闻列表
依赖：Step1的结果，需要选择第一个目标公司
""",
                {},
            ),
            (
                """{
  "objective": "先找到热点龙头，再补该公司的新闻",
  "plan_mode": "planned",
  "plan_type": "planned_run",
  "selected_tools": ["finance_data_query", "company_news"],
  "work_items": [
    {
      "step_id": "step_1",
      "intent": "找到热点龙头",
      "depends_on": [],
      "type": "tool",
      "name": "finance_data_query",
      "status": "planned",
      "tool_candidates": ["finance_data_query"],
      "produces": ["目标公司列表"],
      "output_binding": {
        "items": "$result.items",
        "top1_name": "$result.items.0.leader_name"
      }
    },
    {
      "step_id": "step_2",
      "intent": "查询目标公司新闻",
      "depends_on": ["step_1"],
      "type": "tool",
      "name": "company_news",
      "status": "planned",
      "tool_candidates": ["company_news"],
      "consumes": ["目标公司"],
      "produces": ["新闻列表"],
      "input_binding": {
        "query": "$top1_name"
      }
    }
  ],
  "reason": "compile_nl_plan"
}""",
                {},
            ),
        ]
    )
    monkeypatch.setattr(
        "src.services.agent_runtime_llm_planner_service.chat_qwen",
        lambda messages, enable_think=False: next(responses),
    )

    result = service.build_plan(
        user_objective="先找到热点龙头，再补该公司的新闻",
        work_context={},
        application_context={},
        enable_llm=True,
    )

    execution_plan = result["execution_plan"]
    assert execution_plan["plan_type"] == "planned_run"
    assert execution_plan["work_items"][0]["name"] == "finance_data_query"
    assert execution_plan["work_items"][1]["name"] == "company_news"
    assert result["raw_high_level_plan"] is None
    assert "Task-Mode: Planed" in result["raw_high_level_plan_text"]


def test_plan_compiler_receives_tool_output_contracts(monkeypatch):
    capability_result = {
        "skills": [],
        "tools": [{"tool_name": "实时行情排名查询", "score": 0.9}],
        "planner_skills": [],
        "planner_tools": [
            {
                "tool_name": "实时行情排名查询",
                "purpose": "查询实时行情排行",
                "best_for": ["涨停榜", "成交额榜"],
                "required_inputs": [],
                "optional_inputs": ["sort_by", "top_k"],
                "input_notes": ["sort_by 支持 涨停、成交额 等"],
                "output_fields": ["data[].stock_code", "data[].stock_name", "data[].amount"],
            }
        ],
    }
    service = AgentRuntimeLLMPlannerService(
        capability_search_service=_CapabilitySearchStub(capability_result),
        fallback_planner=_FallbackPlannerStub(),
        prompt_context_compiler=_PromptContextCompilerStub(),
    )
    captured = {}

    def _render_messages(prompt_key, variables):
        if prompt_key == "system.agent_runtime.plan_compiler":
            captured["candidate_tool_contract_sections"] = variables.get("candidate_tool_contract_sections")
        return []

    monkeypatch.setattr(service.registry, "render_messages", _render_messages)
    responses = iter(
        [
            (
                """分析过程：
先取涨停列表，再从中按成交额选前三。
执行步骤：
Task-Mode: Planned
Excute-Plan:
Step1
目标：获取候选股票列表
可能的工具：实时行情排名查询
得到的结果：涨停股票列表
""",
                {},
            ),
            (
                """{
  "objective": "先取涨停列表，再从中按成交额选前三",
  "plan_mode": "planned",
  "plan_type": "planned_run",
  "selected_tools": ["实时行情排名查询"],
  "work_items": [],
  "reason": "ok"
}""",
                {},
            ),
        ]
    )
    monkeypatch.setattr(
        "src.services.agent_runtime_llm_planner_service.chat_qwen",
        lambda messages, enable_think=False: next(responses),
    )

    result = service.build_plan(
        user_objective="先取涨停列表，再从中按成交额选前三",
        work_context={},
        application_context={},
        enable_llm=True,
    )

    assert result["ok"] is True
    section_text = captured["candidate_tool_contract_sections"]
    assert "### 工具：实时行情排名查询" in section_text
    assert "`sort_by`" in section_text
    assert "`data[].code`" in section_text
    assert "`data[].name`" in section_text
    assert "`data[].amount`" in section_text


def test_code_runtime_hint_is_passed_to_planner_prompt(monkeypatch):
    capability_search = _CapabilitySearchStub(
        {
            "skills": [],
            "tools": [],
            "planner_skills": [],
            "planner_tools": [],
        }
    )
    service = AgentRuntimeLLMPlannerService(
        capability_search_service=capability_search,
        fallback_planner=_FallbackPlannerStub(),
        prompt_context_compiler=_PromptContextCompilerStub(),
    )
    captured = {}

    def _render_messages(prompt_key, variables):
        if prompt_key == "system.agent_runtime.planner":
            captured["planner_variables"] = variables
        return []

    monkeypatch.setattr(service.registry, "render_messages", _render_messages)
    responses = iter(
        [
            (
                """分析过程：
用户希望优先用 code 处理上游表格。
执行步骤：
Task-Mode: planned
Excute-Plan:
Step1
目标：用 Python 做表格聚合
手段：code
得到的结果：聚合表
""",
                {},
            ),
            (
                """{
  "objective": "用 Python 做表格聚合",
  "plan_mode": "planned",
  "plan_type": "planned_run",
  "work_items": [
    {
      "step_id": "step_1",
      "intent": "用 Python 做表格聚合",
      "item_type": "code",
      "item_action": "analysis_python",
      "input_binding": {"rows": [{"name": "A", "score": 1}]},
      "code_task_spec": {"task_kind": "table_analysis", "solution_mode": "generated_inline", "code": "print('ok')"},
      "output_binding": {"analysis_table": "$result.data.structured_data.analysis_table"}
    }
  ]
}""",
                {},
            ),
        ]
    )
    monkeypatch.setattr(
        "src.services.agent_runtime_llm_planner_service.chat_qwen",
        lambda messages, enable_think=False: next(responses),
    )

    result = service.build_plan(
        user_objective="聚合这批表格",
        work_context={"code_runtime_hint": True, "preferred_runtime": "code"},
        application_context={},
        enable_llm=True,
    )

    assert captured["planner_variables"]["code_runtime_hint"] is True
    assert captured["planner_variables"]["runtime_preference"] == "code"
    assert capability_search.last_work_context["code_runtime_hint"] is True
    assert result["execution_plan"]["work_items"][0]["type"] == "code"
