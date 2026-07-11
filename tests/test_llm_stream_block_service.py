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
            "code_summary": "实现市场状态合成逻辑。",
            "files": [
                {"path": "tool.py", "role": "tool", "content": "def run(inputs):\n    return inputs\n"},
            ],
            "tests": [
                {"name": "basic", "status": "passed", "summary": "基础场景通过"},
            ],
            "risks": [],
        },
        stage="coding",
    )

    assert [block["block_type"] for block in blocks] == ["narrative", "artifact", "assessment"]
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
