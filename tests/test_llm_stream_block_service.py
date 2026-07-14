from src.services.llm_stream_block_service import LlmStreamBlockBuilder


def test_design_final_uses_conversation_core_blocks_without_complex_renderers() -> None:
    builder = LlmStreamBlockBuilder(run_id="run_test")

    blocks = builder.final_to_blocks(
        {
            "status": "review",
            "understanding": {
                "goal": "判断市场状态",
                "usage": "盘后研究",
                "expected_result": "输出市场状态",
                "confirmed_requirements": ["使用日线"],
                "constraints": ["不产生交易动作"],
                "assumptions": [],
            },
            "design": {
                "tool_name": "market_state",
                "display_name": "市场状态判断",
                "description": "综合趋势、波动和市场宽度。",
                "inputs": [{"name": "as_of", "type": "string", "required": True}],
                "outputs": [{"name": "state", "type": "string"}],
                "modules": [{"name": "趋势", "responsibility": "判断方向", "depends_on": ["行情"]}],
                "rules": [{"name": "趋势规则", "logic": "收盘价高于 MA20", "parameters": ["20日"], "on_missing_data": "数据不足"}],
                "flow": {"steps": [], "links": []},
                "data_requirements": [{"name": "日线", "fields": ["close"], "frequency": "1d", "purpose": "计算趋势", "source_ref": "stock_daily_kline_query", "availability": "verified", "fallback": "数据不足"}],
                "exceptions": [],
                "acceptance": [{"scenario": "价格高于均线", "expected": "输出满足"}],
            },
            "questions": [],
            "design_artifact": {
                "design_artifact_id": "finance_tool_spec_market_state",
                "design_revision": 2,
            },
            "design_context": {
                "scenario": "create_first_round",
                "round": 1,
                "is_first_round": True,
            },
            "existing_analysis": {"analyzed": False, "current_behavior": [], "gaps": [], "affected_areas": [], "evidence": []},
        },
        stage="design",
    )

    assert [block["block_type"] for block in blocks] == ["narrative", "artifact", "interaction"]
    assert not {"table", "flowchart", "code", "action"} & {block["block_type"] for block in blocks}

    artifact = next(block for block in blocks if block["block_type"] == "artifact")
    assert artifact["data"]["artifact_type"] == "finance.tool_spec"
    assert artifact["data"]["artifact_id"] == "finance_tool_spec_market_state"
    assert artifact["data"]["revision"] == 2
    assert artifact["data"]["design_context"]["scenario"] == "create_first_round"
    assert artifact["data"]["design_context"]["round"] == 1
    assert artifact["data"]["items"][1]["value"] == "1 个字段"
    assert artifact["data"]["details"]["rules"][0]["name"] == "趋势规则"
    assert artifact["data"]["details"]["data_requirements"][0]["availability"] == "verified"

    interaction = next(block for block in blocks if block["block_type"] == "interaction")
    assert interaction["data"]["interaction_id"] == "custom_tool.design_review"
    assert interaction["data"]["submission_mode"] == "action"
    assert interaction["data"]["subject_revision"] == 2
    assert interaction["data"]["actions"][0]["expected_revision"] == 2
    assert [action["intent"] for action in interaction["data"]["actions"]] == ["accept", "edit"]
    assert all("command" not in action for action in interaction["data"]["actions"])


def test_design_clarification_keeps_structured_questions_for_ui() -> None:
    builder = LlmStreamBlockBuilder(run_id="run_test")

    blocks = builder.final_to_blocks(
        {
            "status": "clarification",
            "understanding": {"goal": "设计选股工具", "expected_result": "尚待确认"},
            "questions": [{
                "id": "Q1",
                "question": "输出候选列表还是排名？",
                "reason": "影响接口结构",
                "answer_type": "single_choice",
                "required": True,
                "options": [{"value": "list", "label": "候选列表", "description": "返回命中标的", "recommended": True}],
                "allow_custom": True,
            }],
            "design": {"tool_name": "selector", "display_name": "选股工具", "description": "", "inputs": [], "outputs": [], "modules": [], "flow": {"steps": [], "links": []}, "rules": [], "data_requirements": [], "exceptions": [], "acceptance": []},
            "existing_analysis": {"analyzed": False, "current_behavior": [], "gaps": [], "affected_areas": [], "evidence": []},
        },
        stage="design",
    )

    interaction = next(block for block in blocks if block["block_type"] == "interaction")
    assert interaction["data"]["interaction_id"] == "custom_tool.requirement_clarification"
    assert interaction["data"]["submission_mode"] == "conversation"
    assert interaction["data"]["questions"][0]["options"][0]["recommended"] is True
    assert all(block["data"].get("interaction_id") != "custom_tool.design_review" for block in blocks)


def test_coding_final_defaults_to_summary_artifact_and_assessment() -> None:
    builder = LlmStreamBlockBuilder(run_id="run_test")

    blocks = builder.final_to_blocks(
        {
            "status": "code_ready",
            "message": "实现和样例测试已完成。",
            "implementation": {
                "summary": "实现市场状态合成逻辑。",
                "entry_module": "main",
                "modules": [
                    {
                        "module_id": "main",
                        "role": "动态执行入口",
                        "entrypoint": "run",
                        "source_code": "def run(inputs):\n    return inputs\n",
                    },
                ],
            },
            "tests": [
                {"name": "basic", "status": "passed", "summary": "基础场景通过"},
            ],
            "risks": [],
        },
        stage="coding",
    )

    assert [block["block_type"] for block in blocks] == ["narrative", "artifact", "assessment"]
    assert blocks[1]["data"]["items"][0] == {"label": "实现模块", "value": "1 个"}
    assert "files" not in blocks[1]["data"]["details"]
    assert not {"table", "flowchart", "code"} & {block["block_type"] for block in blocks}
    assessment = next(block for block in blocks if block["block_type"] == "assessment")
    assert assessment["data"]["overall"] == "pass"
    assert assessment["data"]["summary"] == "1 / 1 项样例通过"


def test_explicit_legacy_action_block_is_not_forwarded() -> None:
    builder = LlmStreamBlockBuilder(run_id="run_test")

    blocks = builder.final_to_blocks(
        {
            "status": "need_more_info",
            "message": "请补充需求。",
            "render_blocks": [
                {
                    "type": "action",
                    "title": "不可信动作",
                    "actions": [{"label": "执行", "command": "do_something"}],
                }
            ],
        },
        stage="design",
    )

    assert all(block["block_type"] != "action" for block in blocks)


def test_high_frequency_sdk_deltas_are_not_forwarded_as_surface_blocks() -> None:
    builder = LlmStreamBlockBuilder(run_id="run_test")

    assert builder.event_to_blocks({"source": "codex", "type": "agent_delta", "content": "{"}) == []
    assert builder.event_to_blocks({"source": "codex", "type": "reasoning_delta", "content": "raw token"}) == []
    assert builder.event_to_blocks({"source": "codex", "type": "event", "content": "thread/tokenUsage/updated"}) == []

    summary = builder.event_to_blocks({"source": "codex", "type": "reasoning_summary_delta", "content": "正在核对金融口径"})
    assert len(summary) == 1
    assert summary[0]["block_type"] == "workflow"
    assert summary[0]["block_id"] == "design_live_progress"
    assert summary[0]["data"]["role"] == "live_progress"
    assert "正在核对金融口径" not in str(summary[0])


def test_runtime_events_become_user_facing_progress_without_raw_sdk_text() -> None:
    builder = LlmStreamBlockBuilder(run_id="run_progress")

    start = builder.event_to_blocks({
        "source": "harness",
        "type": "stage_start",
        "content": "codex sdk skill stage started: design",
        "metadata": {"stage": "design"},
    })
    context = builder.event_to_blocks({
        "source": "harness",
        "type": "context_ready",
        "content": "/private/tmp/raw/context.json",
        "metadata": {"stage": "design", "bundle_dir": "/private/tmp/raw"},
    })
    reasoning = builder.event_to_blocks({
        "source": "codex",
        "type": "reasoning_summary_delta",
        "content": '{"partial":"raw model text"}',
        "metadata": {"stage": "design"},
    })

    assert [block["block_type"] for block in start] == ["status", "workflow"]
    assert start[0]["content"] == "正在准备任务"
    assert context[0]["content"] == "需求上下文已整理"
    assert context[-1]["data"]["current_step"] == "scope"
    assert reasoning[0]["data"]["current_step"] == "interface"
    assert reasoning[0]["data"]["summary"] == "正在整理输入输出…"
    assert "/private/tmp" not in str(context)
    assert "raw model text" not in str(reasoning[0])
