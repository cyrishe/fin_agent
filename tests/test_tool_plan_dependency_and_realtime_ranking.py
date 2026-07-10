import time

from src.services.agent_runtime_llm_planner_service import AgentRuntimeLLMPlannerService
from src.services.capability_search_service import CapabilitySearchService
from src.services.execution_plan_service import AgentRuntimePlanner
from src.services.tool_plan_runtime_service import ToolPlanRuntimeService
from src.tools.realtime_market_ranking_tool import RealtimeMarketRankingTool
from src.tools.registry import normalize_tool_args_for_definition


class _CapabilitySearchStub:
    def __init__(self, result):
        self.result = result

    def find_for_agent_runtime(self, query, work_context=None, application_context=None, tool_queries=None, **kwargs):
        return self.result


class _PromptContextCompilerStub:
    def compile_sections(self, **kwargs):
        return [{"section": "stubbed"}]


class _ToolArgumentCompilerStub:
    def __init__(self, mapping=None):
        self.mapping = mapping or {}

    def compile_arguments(
        self,
        *,
        tool_name,
        user_objective="",
        step_intent="",
        current_arguments=None,
        current_input_binding=None,
        planner_tool=None,
        enable_llm=True,
    ):
        arguments = dict(self.mapping.get(tool_name) or {})
        current_arguments = dict(current_arguments or {})
        return {
            "tool_name": tool_name,
            "status": "ready" if arguments else "empty",
            "source": "stub",
            "arguments": {**arguments, **current_arguments},
            "missing_arguments": [],
            "dropped_arguments": [],
            "defaults_applied_by_schema": {},
            "reason": "",
            "llm_usage": {},
        }


class _FallbackPlannerWithBindings:
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
                {
                    "step_id": "step_1",
                    "depends_on": [],
                    "type": "tool",
                    "name": "finance_data_query",
                    "status": "planned",
                    "input_binding": {"query": text},
                    "output_binding": {
                        "top1_code": "$result.top1.code",
                        "top1_name": "$result.top1.name",
                    },
                },
                {
                    "step_id": "step_2",
                    "depends_on": ["step_1"],
                    "type": "tool",
                    "name": "company_news",
                    "status": "planned",
                    "input_binding": {
                        "code": "$top1_code",
                        "name": "$top1_name",
                        "query": "$top1_name",
                    },
                    "output_binding": {},
                },
            ],
            "presentation_plan": {
                "layout": "report",
                "page_type": "analysis_result",
                "preferred_block_types": ["structured_text"],
                "sections": [],
            },
        }


def test_llm_planner_keeps_or_backfills_tool_bindings(monkeypatch):
    capability_result = {
        "skills": [],
        "tools": [
            {"tool_name": "finance_data_query", "score": 0.8},
            {"tool_name": "company_news", "score": 0.7},
        ],
        "planner_skills": [],
        "planner_tools": [],
    }
    service = AgentRuntimeLLMPlannerService(
        capability_search_service=_CapabilitySearchStub(capability_result),
        fallback_planner=_FallbackPlannerWithBindings(),
        prompt_context_compiler=_PromptContextCompilerStub(),
    )
    monkeypatch.setattr(service.registry, "render_messages", lambda *args, **kwargs: [])
    responses = iter(
        [
            (
                """规划说明：
我要完成的任务是：先找龙头，再查新闻
{
  "objective": "先找龙头，再查新闻",
  "plan_type": "tool_plan_run",
  "selected_tools": ["finance_data_query", "company_news"],
  "work_items": [
    {"type": "tool", "name": "找龙头公司", "status": "planned", "tool_candidates": ["finance_data_query"], "produces": ["龙头公司"]},
    {"type": "tool", "name": "查公司新闻", "status": "planned", "tool_candidates": ["company_news"], "consumes": ["龙头公司"], "produces": ["新闻列表"]}
  ],
  "reason": "llm_stub"
}""",
                {},
            ),
            (
                """{
  "objective": "先找龙头，再查新闻",
  "plan_type": "tool_plan_run",
  "selected_tools": ["finance_data_query", "company_news"],
  "work_items": [
    {"type": "tool", "name": "finance_data_query", "status": "planned"},
    {"type": "tool", "name": "company_news", "status": "planned"}
  ],
  "reason": "llm_stub"
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
        user_objective="先找龙头，再查新闻",
        work_context={},
        application_context={},
        enable_llm=True,
    )

    work_items = result["execution_plan"]["work_items"]
    assert work_items[0]["name"] == "finance_data_query"
    assert work_items[1]["depends_on"] == ["step_1"]
    assert work_items[1]["input_binding"]["query"] == "$top1_name"


def test_fast_planner_exposes_quant_capabilities_as_analysis_affordances_only():
    capability_result = {
        "skills": [],
        "tools": [],
        "planner_skills": [],
        "planner_tools": [],
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
    planner = AgentRuntimePlanner(capability_search_service=_CapabilitySearchStub(capability_result))

    plan = planner.build_business_dialog_plan(
        text="我想用已经发布的动量策略看看候选股票",
        work_context={},
        application_context={},
        enable_llm=False,
    )

    assert plan["candidate_quant_capabilities"][0]["capability_id"] == "stock_momentum_ranking_pipeline"
    assert plan["evidence_state"]["analysis_affordances"]["upgrade_options"] == [
        {
            "mode": "published_quant_research",
            "capability_id": "stock_momentum_ranking_pipeline",
            "display_name": "Stock Momentum Ranking Pipeline",
            "capability_type": "strategy_pipeline",
            "requires_confirmation": True,
            "execution": "not_planned",
        }
    ]
    assert plan["presentation_plan"]["analysis_affordances"]["quant_research_capabilities"][0]["capability_id"] == "stock_momentum_ranking_pipeline"
    assert plan["evidence_state"]["selected_tools"] == []
    assert all(item.get("name") != "stock_momentum_ranking_pipeline" for item in plan["work_items"])


def test_fast_planner_exposes_quant_authoring_affordance_without_work_item():
    capability_result = {
        "skills": [],
        "tools": [],
        "planner_skills": [],
        "planner_tools": [],
        "planner_quant_capabilities": [],
    }
    planner = AgentRuntimePlanner(capability_search_service=_CapabilitySearchStub(capability_result))

    plan = planner.build_business_dialog_plan(
        text="做一个动量选股策略，用Python回测，验证后保存为可复用skill",
        work_context={},
        application_context={},
        enable_llm=False,
    )

    affordance = plan["evidence_state"]["analysis_affordances"]["quant_authoring_affordance"]
    assert plan["question_contract"]["primary_lane"] == "skill_lifecycle"
    assert affordance["available"] is True
    assert affordance["draft_targets"][0]["target_type"] == "strategy_draft"
    assert affordance["can_execute_without_confirmation"] is False
    assert plan["evidence_state"]["selected_tools"] == []
    assert all(item.get("type") != "tool" for item in plan["work_items"])


def test_execution_plan_builds_realtime_ranking_dependency_chain():
    planner = AgentRuntimePlanner()
    work_items = planner._build_tool_work_items(["实时行情排名查询", "company_news"])

    assert work_items[0]["output_binding"]["top1_name"] == "$result.data.0.stock_name"
    assert work_items[1]["depends_on"] == ["step_1"]
    assert work_items[1]["input_binding"]["code"] == "$top1_code"


def test_execution_plan_fast_path_binds_single_stock_arguments():
    planner = AgentRuntimePlanner()
    work_items = planner._build_tool_work_items(
        ["stock_realtime_quote", "financial_news_search"],
        user_text="请帮我查一下今天贵州茅台的行情和相关新闻",
    )

    quote_step = next(item for item in work_items if item["name"] == "stock_realtime_quote")
    news_step = next(item for item in work_items if item["name"] == "financial_news_search")

    assert quote_step["arguments"]["name"] in {"贵州茅台", "600519"}
    assert news_step["arguments"]["query"] == "贵州茅台"


def test_execution_plan_fast_path_can_backfill_market_arguments_with_llm_compiler():
    planner = AgentRuntimePlanner(
        tool_argument_compiler_service=_ToolArgumentCompilerStub(
            {
                "finance_data_query": {"query": "最近10个交易日三大市场成交额走势"},
            }
        )
    )
    work_items = planner._build_tool_work_items(
        ["finance_data_query"],
        user_text="最近10个交易日，上证、深证、北证三大市场的总成交额分别是什么走势？帮我按日期列出来。",
        planner_tools=[{"tool_name": "finance_data_query"}],
        enable_llm=True,
    )

    assert work_items[0]["name"] == "finance_data_query"
    assert work_items[0]["arguments"] == {"query": "最近10个交易日三大市场成交额走势"}


def test_llm_planner_can_backfill_tool_arguments_after_compiler(monkeypatch):
    capability_result = {
        "skills": [],
        "tools": [{"tool_name": "finance_data_query", "score": 0.9}],
        "planner_skills": [],
        "planner_tools": [
            {
                    "tool_name": "finance_data_query",
                    "purpose": "按金融数据协议执行结构化查询",
                    "best_for": ["行情、资金、估值等结构化金融查询"],
            }
        ],
    }
    service = AgentRuntimeLLMPlannerService(
        capability_search_service=_CapabilitySearchStub(capability_result),
        fallback_planner=_FallbackPlannerWithBindings(),
        prompt_context_compiler=_PromptContextCompilerStub(),
        tool_argument_compiler_service=_ToolArgumentCompilerStub(
            {"finance_data_query": {"query": "最近10个交易日三大市场成交额走势"}}
        ),
    )
    monkeypatch.setattr(service.registry, "render_messages", lambda *args, **kwargs: [])
    responses = iter(
        [
            (
                """{
  "objective": "查询三大市场最近10个交易日成交额走势",
  "plan_type": "tool_plan_run",
  "selected_tools": ["finance_data_query"],
  "work_items": [
    {"type": "tool", "name": "查询市场整体资金", "status": "planned", "tool_candidates": ["finance_data_query"]}
  ],
  "reason": "llm_stub"
}""",
                {},
            ),
            (
                """{
  "objective": "查询三大市场最近10个交易日成交额走势",
  "plan_type": "tool_plan_run",
  "selected_tools": ["finance_data_query"],
  "work_items": [
    {"type": "tool", "name": "finance_data_query", "status": "planned", "arguments": {}}
  ],
  "reason": "llm_stub"
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
        user_objective="最近10个交易日，上证、深证、北证三大市场的总成交额分别是什么走势？帮我按日期列出来。",
        work_context={"planner_thinking_mode": "deep_thinking"},
        application_context={},
        enable_llm=True,
    )

    work_items = result["execution_plan"]["work_items"]
    assert work_items[0]["name"] == "finance_data_query"
    assert work_items[0]["arguments"] == {"query": "最近10个交易日三大市场成交额走势"}


def test_tool_plan_runtime_resolves_output_binding_from_generic_result():
    service = ToolPlanRuntimeService()
    shared_outputs = {}
    service._merge_shared_outputs(
        shared_outputs=shared_outputs,
        step={
            "output_binding": {
                "top1_code": "$result.data.0.stock_code",
                "top1_name": "$result.data.0.stock_name",
            }
        },
        run_record={
            "tool_name": "theme_leaders",
            "result": {
                "tool": "theme_leaders",
                "ok": True,
                "data": [
                    {
                        "theme_name": "机器人",
                        "stock_code": "002050",
                        "stock_name": "三花智控",
                    }
                ],
                "error": "",
            },
            "retention": {},
        },
    )
    assert shared_outputs["top1_code"] == "002050"
    assert shared_outputs["top1_name"] == "三花智控"


def test_tool_plan_runtime_task_state_preserves_planned_run_mode():
    service = ToolPlanRuntimeService()
    task_state = service._build_task_state(
        runtime_trace={"task_id": 123, "local_events": []},
        tool_runs=[{"status": "completed"}],
        execution_plan={"plan_type": "planned_run", "task_mode": "planned"},
    )

    assert task_state["job"]["task_type"] == "planned_run"
    assert task_state["job"]["plan_mode"] == "planned"


class _ToolArgumentPlannerReadyStub:
    def build_plan(self, tool_name, user_text, context):
        return {"status": "ready", "arguments": {}}


class _RuntimeExecutionStub:
    def __init__(self):
        self.calls = []

    def begin_artifact_run(self, **kwargs):
        return {"thread_id": None, "task_id": None, "turn_id": None, "local_events": []}

    def append_event(self, **kwargs):
        return None

    def finish_task(self, **kwargs):
        return None

    def execute_tool(self, tool_name, args, executor):
        self.calls.append((tool_name, dict(args)))
        clean_args = {k: v for k, v in args.items() if k != "_runtime"}
        if tool_name == "plate_members_query":
            return {
                "tool": "plate_members_query",
                "ok": True,
                "data": [
                    {"stock_code": "000001.SZ", "stock_name": "平安银行"},
                    {"stock_code": "000002.SZ", "stock_name": "万科A"},
                ],
                "error": "",
            }
        if tool_name == "stock_funds":
            return {
                "tool": "stock_funds",
                "ok": True,
                "data": {"code": clean_args.get("code"), "main_net_inflow": 123},
                "error": "",
            }
        if tool_name == "financial_news_search":
            return {
                "tool": "financial_news_search",
                "ok": True,
                "data": {"query": clean_args.get("query"), "items": [], "resolved": {}, "stats": {}, "sites": [], "history_records": [], "persisted": False, "persist_stats": {}},
                "error": "",
            }
        return executor(args)


def test_runtime_plan_normalizer_repairs_alias_and_binding_shape():
    service = ToolPlanRuntimeService()
    normalized = service._normalize_runtime_plan(
        {
            "plan_type": "planned_run",
            "work_items": [
                {
                    "step_id": "step_1",
                    "type": "tool",
                    "name": "get_company_taxonomy_profile",
                    "depends_on": [],
                    "input_binding": {"query": "工业富联"},
                    "output_binding": {"concept_list": "hot_concepts"},
                },
                {
                    "step_id": "step_2",
                    "type": "transform",
                    "name": "extract_strongest_concept",
                    "depends_on": ["step_1"],
                    "input_binding": {},
                    "output_binding": {"strongest_concept": "selected_concept"},
                },
                {
                    "step_id": "step_3",
                    "type": "tool",
                    "name": "theme_leaders",
                    "depends_on": ["step_1_transform"],
                    "input_binding": {"query": "${step_2.output_binding.strongest_concept}"},
                    "output_binding": {},
                },
            ],
        }
    )

    assert normalized["work_items"][2]["depends_on"] == ["step_2"]
    assert normalized["work_items"][2]["input_binding"]["query"] == "${step_2.strongest_concept}"


def test_runtime_plan_normalizer_accepts_item_type_and_item_action():
    service = ToolPlanRuntimeService()
    normalized = service._normalize_runtime_plan(
        {
            "plan_type": "planned_run",
            "work_items": [
                {
                    "step_id": "step_1",
                    "item_type": "tool",
                    "item_action": "theme_leaders",
                    "depends_on": [],
                    "input_binding": {"query": "机器人"},
                    "output_binding": {"leaders": "$result.data"},
                },
                {
                    "step_id": "step_2",
                    "item_type": "transform",
                    "item_action": "$input | top(1) | first() | project(code=@.stock_code)",
                    "depends_on": ["step_1"],
                    "input_binding": {"input": "${step_1.leaders}"},
                    "output_binding": {"top_code": "$result.code"},
                },
            ],
        }
    )

    assert normalized["work_items"][0]["type"] == "tool"
    assert normalized["work_items"][0]["name"] == "theme_leaders"
    assert normalized["work_items"][0]["execution_mode"] == "direct"
    assert normalized["work_items"][1]["type"] == "transform"
    assert normalized["work_items"][1]["name"].startswith("$input | top(1)")
    assert normalized["work_items"][1]["transform_spec"]["dsl"].startswith("$input | top(1)")


def test_runtime_plan_normalizer_maps_legacy_foreach_type_to_tool_execution_mode():
    service = ToolPlanRuntimeService()
    normalized = service._normalize_runtime_plan(
        {
            "plan_type": "planned_run",
            "work_items": [
                {
                    "step_id": "step_1",
                    "item_type": "foreach",
                    "item_action": "stock_funds",
                    "depends_on": ["step_0"],
                    "foreach_binding": {"items": "${step_0.top2_stocks}"},
                    "input_binding": {"code": "${item.stock_code}"},
                    "output_binding": {"funds_list": "$result"},
                }
            ],
        }
    )

    assert normalized["work_items"][0]["type"] == "tool"
    assert normalized["work_items"][0]["item_type"] == "tool"
    assert normalized["work_items"][0]["name"] == "stock_funds"
    assert normalized["work_items"][0]["execution_mode"] == "foreach"


def test_tool_plan_runtime_executes_transform_and_foreach_subset():
    runtime_stub = _RuntimeExecutionStub()
    service = ToolPlanRuntimeService(
        tool_argument_planner=_ToolArgumentPlannerReadyStub(),
        runtime_execution_service=runtime_stub,
        enable_tool_preflight=False,
    )
    result = service.execute_for_assistant(
        execution_plan={
            "plan_type": "planned_run",
            "task_mode": "planned",
            "objective": "对比机器人龙头前两名的资金",
            "work_items": [
                {
                    "step_id": "step_1",
                    "type": "tool",
                    "name": "plate_members_query",
                    "depends_on": [],
                    "input_binding": {"plate": "机器人"},
                    "output_binding": {"leaders": "$result.data"},
                },
                {
                    "step_id": "step_2_transform",
                    "type": "transform",
                    "name": "extract_top_2_codes",
                    "depends_on": ["step_1"],
                    "input_binding": {"source_data": "${step_1.result.data}"},
                    "output_binding": {"target_companies": "target_companies"},
                },
                {
                    "step_id": "step_3",
                    "type": "tool",
                    "name": "stock_funds",
                    "execution_mode": "foreach",
                    "depends_on": ["step_1_transform"],
                    "foreach_binding": {"items": "${step_2.target_companies}"},
                    "input_binding": {"code": "${target_companies.code}"},
                    "output_binding": {"funds_list": "funds_data_list"},
                },
            ],
        },
        user_text="对比机器人龙头前两名的资金",
        application_context={},
        thread_context={},
    )

    items = result["items"]
    assert [item["name"] for item in items] == ["plate_members_query", "extract_top_2_codes", "stock_funds"]
    assert items[1]["status"] == "completed"
    assert items[2]["status"] == "completed"
    fund_calls = [call for call in runtime_stub.calls if call[0] == "stock_funds"]
    assert [call[1]["code"] for call in fund_calls] == ["000001.SZ", "000002.SZ"]


def test_tool_plan_runtime_executes_transform_dsl_top1_projection():
    service = ToolPlanRuntimeService()
    result = service._run_transform_logic(
        step={
            "name": "select_top_company",
            "input_binding": {
                "input": [
                    {"stock_code": "000001.SZ", "stock_name": "平安银行", "volume": 10},
                    {"stock_code": "000002.SZ", "stock_name": "万科A", "volume": 30},
                    {"stock_code": "000003.SZ", "stock_name": "国农科技", "volume": 20},
                ]
            },
            "transform_spec": {
                "dsl": "$input | sort(@.volume desc) | top(1) | first() | project(target_stock_code=@.stock_code, target_stock_name=@.stock_name)"
            },
            "output_binding": {
                "target_stock_code": "$result.target_stock_code",
                "target_stock_name": "$result.target_stock_name",
            },
        },
        shared_outputs={},
        step_outputs={},
    )

    result_data, named_outputs = result
    assert result_data["target_stock_code"] == "000002.SZ"
    assert result_data["target_stock_name"] == "万科A"
    assert named_outputs["target_stock_code"] == "000002.SZ"
    assert named_outputs["target_stock_name"] == "万科A"


def test_tool_plan_runtime_executes_transform_dsl_with_dollar_input_and_scalar_project():
    service = ToolPlanRuntimeService()
    result_data, named_outputs = service._run_transform_logic(
        step={
            "name": "select_first_concept",
            "input_binding": {
                "$input": ["半导体", "电子", "5G"]
            },
            "transform_spec": {
                "dsl": "$input | first() | project(query=@)"
            },
            "output_binding": {
                "target_concept": "$result.query",
            },
        },
        shared_outputs={},
        step_outputs={},
    )

    assert result_data == {"query": "半导体"}
    assert named_outputs["target_concept"] == "半导体"


def test_tool_plan_runtime_executes_transform_dsl_then_tool_binding():
    runtime_stub = _RuntimeExecutionStub()
    service = ToolPlanRuntimeService(runtime_execution_service=runtime_stub)
    result = service.execute_for_assistant(
        execution_plan={
            "plan_type": "planned_run",
            "task_mode": "planned",
            "objective": "从榜单里找成交量最大的股票再查新闻",
            "work_items": [
                {
                    "step_id": "step_1",
                    "type": "tool",
                    "name": "plate_members_query",
                    "depends_on": [],
                    "input_binding": {"plate": "机器人"},
                    "output_binding": {"leaders": "$result.data"},
                },
                {
                    "step_id": "step_2",
                    "type": "transform",
                    "name": "select_top_company",
                    "depends_on": ["step_1"],
                    "input_binding": {"input": "${step_1.result.data}"},
                    "transform_spec": {
                        "dsl": "$input | top(1) | first() | project(target_stock_code=@.stock_code, target_stock_name=@.stock_name)"
                    },
                    "output_binding": {
                        "target_stock_code": "$result.target_stock_code",
                        "target_stock_name": "$result.target_stock_name",
                    },
                },
                {
                    "step_id": "step_3",
                    "type": "tool",
                    "name": "financial_news_search",
                    "depends_on": ["step_2"],
                    "input_binding": {"query": "${step_2.target_stock_name}"},
                    "output_binding": {},
                },
            ],
        },
        user_text="从榜单里找成交量最大的股票再查新闻",
        application_context={},
        thread_context={},
    )

    items = result["items"]
    assert [item["status"] for item in items] == ["completed", "completed", "completed"]
    news_call = [call for call in runtime_stub.calls if call[0] == "financial_news_search"]
    assert news_call[0][1]["query"] == "平安银行"


class _ParallelRuntimeExecutionStub(_RuntimeExecutionStub):
    def execute_tool(self, tool_name, args, executor):
        self.calls.append((tool_name, dict(args)))
        time.sleep(0.2)
        return {
            "tool": tool_name,
            "ok": True,
            "data": {"query": args.get("query") or args.get("code")},
            "error": "",
        }


class _FailingPreflightStub:
    def validate_tool_call(self, *, tool_name, arguments=None):
        raise AssertionError("preflight should not run when disabled")


class _BlockingPreflightStub:
    def __init__(self):
        self.calls = []

    def validate_tool_call(self, *, tool_name, arguments=None):
        self.calls.append((tool_name, dict(arguments or {})))
        return {
            "ok": False,
            "status": "blocked",
            "tool_name": tool_name,
            "arguments": dict(arguments or {}),
            "reason": "tool_not_active",
            "details": {"status": "disabled"},
        }


def test_tool_plan_runtime_runs_ready_tool_steps_in_parallel(monkeypatch):
    runtime_stub = _ParallelRuntimeExecutionStub()
    service = ToolPlanRuntimeService(runtime_execution_service=runtime_stub, enable_tool_preflight=False)
    monkeypatch.setattr(
        service,
        "_build_final_output",
        lambda **kwargs: ({"summary": "ok", "facts": [], "risks": []}, None),
    )
    started_at = time.monotonic()
    result = service.execute_for_assistant(
        execution_plan={
            "plan_type": "planned_run",
            "task_mode": "planned",
            "objective": "并行查行情和新闻",
            "work_items": [
                {
                    "step_id": "step_1",
                    "type": "tool",
                    "name": "stock_quote",
                    "depends_on": [],
                    "input_binding": {"code": "600519"},
                    "output_binding": {},
                },
                {
                    "step_id": "step_2",
                    "type": "tool",
                    "name": "financial_news_search",
                    "depends_on": [],
                    "input_binding": {"query": "贵州茅台"},
                    "output_binding": {},
                },
            ],
        },
        user_text="并行查行情和新闻",
        application_context={},
        thread_context={},
    )
    elapsed = time.monotonic() - started_at

    assert [item["status"] for item in result["items"]] == ["completed", "completed"]
    assert elapsed < 0.38


def test_tool_plan_runtime_can_disable_preflight(monkeypatch):
    runtime_stub = _ParallelRuntimeExecutionStub()
    service = ToolPlanRuntimeService(
        runtime_execution_service=runtime_stub,
        tool_runtime_preflight_service=_FailingPreflightStub(),
        enable_tool_preflight=False,
    )
    monkeypatch.setattr(
        service,
        "_build_final_output",
        lambda **kwargs: ({"summary": "ok", "facts": [], "risks": []}, None),
    )

    result = service.execute_for_assistant(
        execution_plan={
            "plan_type": "tool_plan_run",
            "objective": "查行情",
            "work_items": [
                {
                    "step_id": "step_1",
                    "type": "tool",
                    "name": "stock_quote",
                    "depends_on": [],
                    "arguments": {"code": "600519"},
                    "input_binding": {},
                    "output_binding": {},
                }
            ],
        },
        user_text="查行情",
        application_context={},
        thread_context={},
    )

    assert result["items"][0]["status"] == "completed"
    assert runtime_stub.calls[0][0] == "stock_quote"


class _PartialFailureRuntimeExecutionStub(_RuntimeExecutionStub):
    def execute_tool(self, tool_name, args, executor):
        self.calls.append((tool_name, dict(args)))
        if tool_name == "mock_failed_tool":
            return {"tool": tool_name, "ok": False, "data": {}, "error": "unsupported sort_by: turnover"}
        return {"tool": tool_name, "ok": True, "data": {"rows": [{"name": "A"}]}, "error": ""}


def test_tool_plan_runtime_allows_synthesis_after_partial_tool_failure(monkeypatch):
    runtime_stub = _PartialFailureRuntimeExecutionStub()
    service = ToolPlanRuntimeService(
        runtime_execution_service=runtime_stub,
        enable_tool_preflight=False,
    )
    monkeypatch.setattr(
        service,
        "_build_final_output",
        lambda **kwargs: ({"summary": "partial", "facts": [], "risks": []}, None),
    )

    result = service.execute_for_assistant(
        execution_plan={
            "plan_type": "planned_run",
            "task_mode": "planned",
            "objective": "一个工具失败时仍汇总已完成结果",
            "work_items": [
                {
                    "step_id": "step_1",
                    "type": "tool",
                    "name": "mock_failed_tool",
                    "depends_on": [],
                    "arguments": {},
                    "input_binding": {},
                    "output_binding": {},
                },
                {
                    "step_id": "step_2",
                    "type": "tool",
                    "name": "mock_ok_tool",
                    "depends_on": [],
                    "arguments": {},
                    "input_binding": {},
                    "output_binding": {},
                },
                {
                    "step_id": "step_3",
                    "type": "synthesis",
                    "name": "final_synthesis",
                    "depends_on": ["step_1", "step_2"],
                },
                {
                    "step_id": "step_4",
                    "type": "presentation",
                    "name": "presentation_plan",
                    "depends_on": ["step_3"],
                },
            ],
        },
        user_text="一个工具失败时仍汇总已完成结果",
        application_context={},
        thread_context={},
    )

    assert [item["status"] for item in result["items"]] == ["failed", "completed", "completed", "completed"]
    assert result["items"][2]["reason"] == "partial_dependencies"
    assert result["items"][2]["feedback"]["suggested_action"] == "continue"


def test_tool_plan_runtime_preflight_blocks_bad_enum_without_blocking_synthesis(monkeypatch):
    runtime_stub = _PartialFailureRuntimeExecutionStub()
    service = ToolPlanRuntimeService(runtime_execution_service=runtime_stub)
    monkeypatch.setattr(
        service,
        "_build_final_output",
        lambda **kwargs: ({"summary": "partial", "facts": [], "risks": []}, None),
    )

    result = service.execute_for_assistant(
        execution_plan={
            "plan_type": "planned_run",
            "task_mode": "planned",
            "objective": "机器人板块今天涨幅超过2%的股票有多少只，按成交额列前5个",
            "work_items": [
                {
                    "step_id": "step_1",
                    "type": "tool",
                    "name": "plate_rank_query",
                    "depends_on": [],
                    "arguments": {
                        "query": "机器人",
                        "include_members": True,
                        "member_limit": 5,
                        "sort_by": "turnover",
                        "top_k": 1,
                    },
                    "input_binding": {},
                    "output_binding": {},
                },
                {
                    "step_id": "step_2",
                    "type": "tool",
                    "name": "plate_members_query",
                    "depends_on": [],
                    "arguments": {
                        "plate": "机器人",
                        "limit": 50,
                        "sort_by": "stock_rise_fall_rate",
                    },
                    "input_binding": {},
                    "output_binding": {},
                },
                {
                    "step_id": "step_3",
                    "type": "synthesis",
                    "name": "final_synthesis",
                    "depends_on": ["step_1", "step_2"],
                },
            ],
        },
        user_text="机器人板块今天涨幅超过2%的股票有多少只，按成交额列前5个",
        application_context={},
        thread_context={},
    )

    assert [item["status"] for item in result["items"]] == ["skipped", "completed", "completed"]
    assert result["items"][0]["reason"] == "invalid_argument_enum"
    assert result["items"][0]["feedback"]["reason_code"] == "tool_schema_mismatch"
    assert result["items"][0]["feedback"]["suggested_action"] == "replan_stage"
    assert [call[0] for call in runtime_stub.calls] == ["plate_members_query"]


def test_tool_plan_runtime_optional_preflight_blocks_inactive_tool(monkeypatch):
    runtime_stub = _ParallelRuntimeExecutionStub()
    preflight = _BlockingPreflightStub()
    service = ToolPlanRuntimeService(
        runtime_execution_service=runtime_stub,
        tool_runtime_preflight_service=preflight,
        enable_tool_preflight=True,
    )
    monkeypatch.setattr(
        service,
        "_build_final_output",
        lambda **kwargs: ({"summary": "blocked", "facts": [], "risks": []}, None),
    )

    result = service.execute_for_assistant(
        execution_plan={
            "plan_type": "tool_plan_run",
            "objective": "查行情",
            "work_items": [
                {
                    "step_id": "step_1",
                    "type": "tool",
                    "name": "stock_quote",
                    "depends_on": [],
                    "arguments": {"code": "600519"},
                    "input_binding": {},
                    "output_binding": {},
                }
            ],
        },
        user_text="查行情",
        application_context={},
        thread_context={},
    )

    assert result["items"][0]["status"] == "skipped"
    assert result["items"][0]["reason"] == "tool_not_active"
    skipped_event = next(
        item
        for item in result["runtime_trace"]["local_events"]
        if item["event_type"] == "tool_skipped" and item["payload"].get("reason") == "tool_not_active"
    )
    assert skipped_event["payload"]["preflight"]["details"]["status"] == "disabled"
    assert preflight.calls == [("stock_quote", {"code": "600519"})]
    assert runtime_stub.calls == []


def test_tool_plan_runtime_merges_static_arguments_before_schema_validation(monkeypatch):
    runtime_stub = _ParallelRuntimeExecutionStub()
    service = ToolPlanRuntimeService(runtime_execution_service=runtime_stub, enable_tool_preflight=False)
    monkeypatch.setattr(
        service,
        "_build_final_output",
        lambda **kwargs: ({"summary": "ok", "facts": [], "risks": []}, None),
    )

    result = service.execute_for_assistant(
        execution_plan={
            "plan_type": "planned_run",
            "task_mode": "planned",
            "objective": "查询贵州茅台行情和资金流向",
            "work_items": [
                {
                    "step_id": "step_1",
                    "type": "tool",
                    "name": "stock_quote",
                    "depends_on": [],
                    "arguments": {"code": "贵州茅台"},
                    "input_binding": {},
                    "output_binding": {},
                },
                {
                    "step_id": "step_2",
                    "type": "tool",
                    "name": "stock_funds",
                    "depends_on": [],
                    "arguments": {"code": "贵州茅台"},
                    "input_binding": {},
                    "output_binding": {},
                },
            ],
        },
        user_text="查询贵州茅台行情和资金流向",
        application_context={},
        thread_context={},
    )

    assert [item["status"] for item in result["items"]] == ["completed", "completed"]
    quote_call = next(args for name, args in runtime_stub.calls if name == "stock_quote")
    funds_call = next(args for name, args in runtime_stub.calls if name == "stock_funds")
    assert quote_call["code"] == "贵州茅台"
    assert funds_call["code"] == "贵州茅台"


def test_tool_plan_runtime_builds_kline_and_intraday_blocks_and_reference_materials():
    service = ToolPlanRuntimeService()
    tool_runs = [
        {
            "tool_name": "stock_quote",
            "status": "completed",
            "retention": {
                "render_artifacts": {
                    "data.daily_kline": {
                        "kline": [["2026-04-16", 10.0, 10.5, 9.8, 10.8, 10000, 1.2]],
                        "indicators": {"MA5": [["2026-04-16", 10.2]]},
                    },
                    "data.intraday_kline": {
                        "kline": [["09:35", 10.0, 10.2, 9.9, 10.3, 8000, 0.2]],
                        "indicators": {},
                    },
                },
                "reference_artifacts": {},
            },
        },
        {
            "tool_name": "financial_news_search",
            "status": "completed",
            "retention": {
                "render_artifacts": {},
                "reference_artifacts": {
                    "data": [
                        {
                            "title": "贵州茅台新闻",
                            "url": "https://example.com/news/1",
                            "site": "示例",
                            "publish_time": "2026-04-16 10:00:00",
                            "snippet": "摘要",
                        }
                    ]
                },
            },
        },
    ]

    render_payload = service._build_render_payload(
        execution_plan={"presentation_plan": {"page_type": "tool_plan_result", "layout": "report"}},
        final_output={"summary": "ok"},
        tool_runs=tool_runs,
    )

    blocks = [block for section in render_payload["sections"] for block in (section.get("blocks") or [])]
    assert any(block["type"] == "kline" for block in blocks)
    assert any(block["type"] == "line" for block in blocks)
    assert render_payload["reference_materials"][0]["url"] == "https://example.com/news/1"


def test_tool_plan_runtime_honors_manual_render_type_preferences():
    service = ToolPlanRuntimeService()
    tool_runs = [
        {
            "tool_name": "stock_funds",
            "status": "completed",
            "retention": {
                "render_artifacts": {
                    "data.bar_chart": {"categories": ["大单", "中单"], "series": [{"name": "净额", "data": [12, 8]}]},
                    "data.pie_chart": {"items": [{"label": "正面", "value": 7}, {"label": "中性", "value": 3}]},
                    "data.flow_steps": [{"title": "筛选涨停股"}, {"title": "选择前2家公司"}, {"title": "汇总新闻"}],
                },
                "render_preferences": {
                    "data.bar_chart": {"render_type": "bar", "strategy": "render"},
                    "data.pie_chart": {"render_type": "pie", "strategy": "render"},
                    "data.flow_steps": {"render_type": "flow", "strategy": "render"},
                },
                "reference_artifacts": {},
            },
        }
    ]

    render_payload = service._build_render_payload(
        execution_plan={"presentation_plan": {"page_type": "tool_plan_result", "layout": "report"}},
        final_output={"summary": "ok"},
        tool_runs=tool_runs,
    )

    blocks = [block for section in render_payload["sections"] for block in (section.get("blocks") or [])]
    assert {block["type"] for block in blocks} >= {"bar", "pie", "flow"}


def test_tool_plan_runtime_prefers_tool_render_blocks_protocol():
    service = ToolPlanRuntimeService()
    tool_runs = [
        {
            "tool_name": "stock_quote",
            "status": "completed",
            "result": {
                "tool": "stock_quote",
                "ok": True,
                "data": {
                    "render_blocks": [
                        {
                            "type": "metric_strip",
                            "title": "行情概览",
                            "data": {"items": [{"label": "现价", "value": 10.5}]},
                            "meta": {"source": "upchina"},
                        },
                        {
                            "type": "kline",
                            "title": "日线K线",
                            "data": {
                                "candles": [
                                    {
                                        "time": "2026-04-16",
                                        "open": 10.0,
                                        "close": 10.5,
                                        "low": 9.8,
                                        "high": 10.8,
                                        "volume": 10000,
                                        "pct": 1.2,
                                    }
                                ],
                                "indicators": [],
                            },
                            "meta": {"source": "kcrp_stock_price"},
                        },
                    ]
                },
                "error": "",
            },
            "retention": {
                "render_artifacts": {
                    "data.daily_kline": {
                        "kline": [["legacy", 1, 2, 0, 3, 4, 5]],
                        "indicators": {},
                    }
                },
                "reference_artifacts": {},
            },
        }
    ]

    render_payload = service._build_render_payload(
        execution_plan={"presentation_plan": {"page_type": "tool_plan_result", "layout": "report"}},
        final_output={"summary": "ok"},
        tool_runs=tool_runs,
    )

    blocks = [block for section in render_payload["sections"] for block in (section.get("blocks") or [])]
    assert any(block["type"] == "metric_strip" and block["title"] == "行情概览" for block in blocks)
    assert any(block["type"] == "kline" and block["data"].get("candles") for block in blocks)
    contract = render_payload.get("presentation_contract") or {}
    assert contract.get("layout") == "conversation_compact"
    assert any(section.get("section_id") == "metrics" for section in contract.get("sections") or [])


def test_tool_plan_runtime_builds_compact_presentation_contract():
    service = ToolPlanRuntimeService()
    render_payload = service._build_render_payload(
        execution_plan={"objective": "查看市场情况", "presentation_plan": {"page_type": "tool_plan_result", "layout": "report"}},
        final_output={
            "summary": "市场偏弱，资金观望。",
            "facts": [{"detail": "上涨家数少于下跌家数"}],
            "risks": [{"description": "短线波动较大"}],
        },
        tool_runs=[
            {
                "tool_name": "market_realtime_breadth",
                "status": "completed",
                "result": {
                    "tool": "market_realtime_breadth",
                    "ok": True,
                    "data": {
                        "render_blocks": [
                            {
                                "type": "metric_strip",
                                "title": "市场广度",
                                "data": {"items": [{"label": "上涨家数", "value": 1200}]},
                            },
                            {
                                "type": "bar",
                                "title": "涨跌分布",
                                "data": {"x_axis": ["上涨", "下跌"], "series": [{"name": "家数", "data": [1200, 2600]}]},
                            },
                        ]
                    },
                    "error": "",
                },
                "retention": {"render_artifacts": {}, "reference_artifacts": {}},
            }
        ],
    )

    contract = render_payload.get("presentation_contract") or {}
    assert contract.get("hero", {}).get("summary") == "市场偏弱，资金观望。"
    section_ids = [section.get("section_id") for section in contract.get("sections") or []]
    assert "metrics" in section_ids
    assert "charts" in section_ids
    assert "analysis" in section_ids


def test_tool_plan_runtime_refines_uncertain_blocks_with_llm(monkeypatch):
    service = ToolPlanRuntimeService()
    monkeypatch.setenv("RESULT_PRESENTATION_LLM_REFINE_ENABLED", "true")
    monkeypatch.setattr(
        service.registry,
        "render_messages",
        lambda prompt_key, variables: [{"role": "system", "content": str(variables)}],
    )
    monkeypatch.setattr(
        "src.services.tool_plan_runtime_service.chat_qwen_flash_json",
        lambda messages, enable_think=False: ({"type": "pie"}, {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}),
    )

    render_payload = service._build_render_payload(
        execution_plan={"objective": "查看资金分布", "presentation_plan": {"page_type": "tool_plan_result", "layout": "report"}},
        final_output={"summary": "ok"},
        tool_runs=[
            {
                "tool_name": "ambiguous_tool",
                "status": "completed",
                "result": {"tool": "ambiguous_tool", "ok": True, "data": {}, "error": ""},
                "retention": {
                    "render_artifacts": {
                        "data.distribution": {
                            "items": [
                                {"label": "a", "value": 10},
                                {"label": "b", "value": 20},
                                {"label": "c", "value": 30},
                            ]
                        }
                    },
                    "reference_artifacts": {},
                },
            }
        ],
    )

    blocks = [block for section in render_payload["sections"] for block in (section.get("blocks") or [])]
    refined = next(block for block in blocks if block.get("title") == "data / distribution")
    assert refined["type"] == "pie"
    assert refined["data"]["items"][0]["label"] == "a"
    assert "_llm_raw_value" not in (refined.get("meta") or {})


def test_runtime_exports_output_binding_into_step_named_outputs_for_step_refs():
    service = ToolPlanRuntimeService()
    shared_outputs = {}
    step_outputs = {
        "step_1": {
            "named_outputs": {},
            "result": {
                "tool": "get_company_taxonomy_profile",
                "ok": True,
                "data": {
                    "concepts": ["半导体", "电子", "5G"],
                },
                "error": "",
            },
            "retention": {},
        }
    }
    step = {
        "step_id": "step_1",
        "type": "tool",
        "name": "get_company_taxonomy_profile",
        "output_binding": {
            "concept_list": "$result.data.concepts"
        },
    }
    run_record = {
        "step_id": "step_1",
        "tool_name": "get_company_taxonomy_profile",
        "status": "completed",
        "result": step_outputs["step_1"]["result"],
        "retention": {},
        "named_outputs": {},
    }

    exported = service._merge_shared_outputs(
        shared_outputs=shared_outputs,
        step=step,
        run_record=run_record,
    )
    step_outputs["step_1"]["named_outputs"] = {
        **step_outputs["step_1"]["named_outputs"],
        **exported,
    }

    items = service._infer_foreach_items(
        step={
            "step_id": "step_2",
            "type": "tool",
            "name": "theme_leaders",
            "execution_mode": "foreach",
            "foreach_binding": {"items": "${step_1.concept_list}"},
            "input_binding": {"query": "${item}"},
        },
        shared_outputs=shared_outputs,
        step_outputs=step_outputs,
    )

    assert shared_outputs["concept_list"] == ["半导体", "电子", "5G"]
    assert step_outputs["step_1"]["named_outputs"]["concept_list"] == ["半导体", "电子", "5G"]
    assert items == [
        {"item": "半导体"},
        {"item": "电子"},
        {"item": "5G"},
    ]


def test_tool_registry_normalizes_arguments_against_schema():
    normalized = normalize_tool_args_for_definition(
        "company_news",
        {
            "keyword": "工业富联",
            "entity_type": "stock",
            "unknown_field": "should_drop",
        },
    )

    assert normalized["arguments"]["query"] == "工业富联"
    assert "entity_type" not in normalized["arguments"]
    assert "unknown_field" not in normalized["arguments"]
    assert "entity_type" in normalized["dropped_fields"]
    assert "unknown_field" in normalized["dropped_fields"]


def test_tool_registry_normalizes_legacy_stock_reports_arguments():
    normalized = normalize_tool_args_for_definition(
        "公司研报查询",
        {
            "stock_name": "贵州茅台",
            "begin_date": "2026-04-01 00:00:00",
            "limit": 3,
            "unknown_field": "should_drop",
        },
    )

    assert normalized["arguments"]["company"] == "贵州茅台"
    assert normalized["arguments"]["limit"] == 3
    assert normalized["arguments"]["since_time"] == "2026-04-01 00:00:00"
    assert "unknown_field" not in normalized["arguments"]
    assert "unknown_field" in normalized["dropped_fields"]


def test_tool_registry_normalizes_legacy_indicator_series_arguments():
    normalized = normalize_tool_args_for_definition(
        "指标序列查询",
        {
            "indicator_name": "close_ma_5",
            "subject_code": "000001.SH",
            "range_days": 5,
            "unknown_field": "should_drop",
        },
    )

    assert normalized["arguments"]["indicator_ids"] == ["close_ma_5"]
    assert normalized["arguments"]["subject_codes"] == ["000001.SH"]
    assert normalized["arguments"]["range_days"] == 5
    assert "unknown_field" not in normalized["arguments"]
    assert "unknown_field" in normalized["dropped_fields"]


def test_tool_registry_normalizes_taxonomy_and_theme_legacy_arguments():
    taxonomy = normalize_tool_args_for_definition(
        "get_company_taxonomy_profile",
        {"company": "工业富联", "query_date": "2026-04-11"},
    )
    theme = normalize_tool_args_for_definition(
        "theme_leaders",
        {"theme_name": "机器人", "sort_by": 2, "top_k": 5},
    )

    assert taxonomy["arguments"]["stock"] == "工业富联"
    assert taxonomy["arguments"]["as_of"] == "2026-04-11"
    assert theme["arguments"]["query"] == "机器人"
    assert "sort_by" in theme["dropped_fields"]
    assert "top_k" in theme["dropped_fields"]


def test_capability_search_exposes_tool_input_contract_to_planner():
    service = CapabilitySearchService()
    result = service.find_for_agent_runtime(
        query="查公司新闻",
        work_context={},
        application_context={"application_name": "investment_workbench"},
        tool_top_k=8,
    )
    company_news = next(
        item for item in result["planner_tools"]
        if item["tool_name"] == "financial_news_search"
    )

    assert "query" in company_news["required_inputs"]
    assert company_news["optional_inputs"] == []
    assert company_news["input_notes"]
    assert "score" not in company_news


def test_capability_search_hides_hidden_retired_stock_quote():
    service = CapabilitySearchService()
    result = service.find_for_agent_runtime(
        query="查个股行情",
        work_context={},
        application_context={"application_name": "investment_workbench"},
        tool_top_k=16,
    )
    tool_names = [item["tool_name"] for item in result["planner_tools"]]

    assert "stock_quote" not in tool_names


def test_capability_search_hides_hidden_retired_stock_funds():
    service = CapabilitySearchService()
    result = service.find_for_agent_runtime(
        query="查个股资金流向",
        work_context={},
        application_context={"application_name": "investment_workbench"},
        tool_top_k=16,
    )
    tool_names = [item["tool_name"] for item in result["planner_tools"]]

    assert "stock_funds" not in tool_names


def test_capability_search_hides_hidden_retired_market_overview():
    service = CapabilitySearchService()
    result = service.find_for_agent_runtime(
        query="查市场整体情况和涨跌家数",
        work_context={},
        application_context={"application_name": "investment_workbench"},
        tool_top_k=16,
    )
    tool_names = [item["tool_name"] for item in result["planner_tools"]]

    assert "大盘整体情况" not in tool_names


def test_capability_search_does_not_expose_embedding_scores():
    service = CapabilitySearchService()
    result = service.find_for_agent_runtime(
        query="查贵州茅台的行情和新闻",
        work_context={},
        application_context={"application_name": "investment_workbench"},
        tool_top_k=8,
        skill_top_k=5,
    )

    assert all("score" not in item for item in result["tools"])
    assert all("score" not in item for item in result["planner_tools"])
    assert all("score" not in item for item in result["skills"])
    assert all("score" not in item for item in result["planner_skills"])


class _EmbeddingScoreStub:
    def __init__(self, mapping):
        self.mapping = mapping

    def score(self, query, documents):
        doc_scores = self.mapping.get(query, {})
        return [doc_scores.get(doc, 0.0) for doc in documents]


def test_capability_search_merges_split_tool_queries_with_top3_cap():
    service = CapabilitySearchService(
        embedding_service=_EmbeddingScoreStub(
            {
                "子任务一": {
                    "用途A": 0.9,
                    "用途B": 0.8,
                    "用途C": 0.7,
                    "用途D": 0.6,
                },
                "子任务二": {
                    "用途C": 0.95,
                    "用途D": 0.85,
                    "用途E": 0.75,
                    "用途F": 0.65,
                },
            }
        )
    )
    tool_catalog = [
        {"tool_name": "tool_a", "purpose": "用途A"},
        {"tool_name": "tool_b", "purpose": "用途B"},
        {"tool_name": "tool_c", "purpose": "用途C"},
        {"tool_name": "tool_d", "purpose": "用途D"},
        {"tool_name": "tool_e", "purpose": "用途E"},
        {"tool_name": "tool_f", "purpose": "用途F"},
    ]

    ranked = service._rank_tools(  # noqa: SLF001
        "完整任务",
        tool_catalog,
        tool_queries=["子任务一", "子任务二"],
        top_k=8,
    )

    assert [item["tool_name"] for item in ranked] == ["tool_a", "tool_b", "tool_c", "tool_d", "tool_e"]


class _FakeDb:
    def close_db(self):
        return None


def test_realtime_market_ranking_tool_sorts_and_filters_rows():
    tool = RealtimeMarketRankingTool(db=_FakeDb())
    tool._resolve_latest_slot = lambda: {
        "trade_date": "2026-04-09",
        "minute_index": 631,
        "snapshot_time": "2026-04-09 10:31:00",
    }
    tool._load_rows = lambda *, trade_date, minute_index: [
        {
            "stk_code": "600519",
            "stk_name": "贵州茅台",
            "snapshot_time": "2026-04-09 10:31:00",
            "latest_price": 1688.0,
            "open_price": 1679.0,
            "high_price": 1692.0,
            "low_price": 1670.0,
            "preclose_price": 1660.0,
            "chg_value": 28.0,
            "chg_ratio": 1.68,
            "amount": 512000000.0,
            "volume": 30200.0,
        },
        {
            "stk_code": "000001",
            "stk_name": "平安银行",
            "snapshot_time": "2026-04-09 10:31:00",
            "latest_price": 10.5,
            "open_price": 10.2,
            "high_price": 10.6,
            "low_price": 10.1,
            "preclose_price": 10.0,
            "chg_value": 0.5,
            "chg_ratio": 5.0,
            "amount": 320000000.0,
            "volume": 41000.0,
        },
    ]

    result = tool.run({"market": "sz", "sort_by": "涨幅", "top_k": 5})

    assert result["ok"] is True
    assert len(result["data"]) == 1
    assert result["data"][0]["stock_code"] == "000001"
    assert result["meta"]["market"] == "sz"
