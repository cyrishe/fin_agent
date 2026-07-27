import json

from src.services.llm_stream_block_service import LlmStreamBlockBuilder


def test_view_stage_does_not_render_as_design_progress() -> None:
    builder = LlmStreamBlockBuilder(run_id="view_turn")

    block = builder.event_to_blocks({
        "source": "harness",
        "type": "stage_start",
        "content": "view turn",
        "metadata": {"stage": "view"},
    })[-1]

    assert block["block_id"] == "view_live_progress"
    assert block["title"] == "查看工具"
    assert block["data"]["stage"] == "view"


def test_requirement_stage_uses_interactive_confirmation_progress() -> None:
    builder = LlmStreamBlockBuilder(run_id="requirement_turn")

    block = builder.event_to_blocks({
        "source": "claude",
        "type": "stage_start",
        "content": "requirement turn",
        "metadata": {"stage": "requirement"},
    })[-1]

    assert block["block_id"] == "requirement_live_progress"
    assert block["title"] == "需求确认"
    assert block["data"]["stage"] == "requirement"
    assert block["block_type"] == "status"
    assert block["content"] == "正在理解你的需求…"
    assert "items" not in block["data"]


def test_requirement_brief_notice_and_questions_form_one_confirmation_surface() -> None:
    builder = LlmStreamBlockBuilder(run_id="requirement_notice")

    blocks = builder.final_to_blocks({
        "status": "clarification",
        "message": "我已经整理好当前需求。",
        "understanding": {"requirement_brief": "扫描A股异动股票。"},
        "notice": ["未指定市场时先按A股处理。", "异动先按单日涨幅超过5%处理。"],
        "questions": [{
            "question": "结果需要按涨幅排序还是按成交额排序？",
            "candidate": ["按涨幅排序", "按成交额排序"],
        }],
    }, stage="requirement")

    assert [block["block_id"] for block in blocks] == [
        "requirement_final_summary",
        "requirement_review",
    ]
    assert blocks[0]["title"] == "需求理解"
    assert blocks[0]["content"] == "扫描A股异动股票。"
    review = blocks[1]
    assert review["block_type"] == "interaction"
    assert review["title"] == "确认需求"
    assert review["data"]["notice"] == [
        "未指定市场时先按A股处理。",
        "异动先按单日涨幅超过5%处理。",
    ]
    assert review["data"]["questions"][0]["question"] == "结果需要按涨幅排序还是按成交额排序？"
    assert review["data"]["actions"][0]["label"] == "确认需求"


def test_requirement_without_questions_still_waits_for_confirmation() -> None:
    builder = LlmStreamBlockBuilder(run_id="requirement_confirm")

    blocks = builder.final_to_blocks({
        "status": "clarification",
        "message": "不要用这段重复 requirement。",
        "understanding": {"requirement_brief": "**目标**：判断股票近期是否出现金叉。"},
        "notice": ["未指定窗口时同时检查最近30和60个交易日。"],
        "questions": [],
    }, stage="requirement")

    assert [block["block_id"] for block in blocks] == [
        "requirement_final_summary",
        "requirement_review",
    ]
    assert blocks[0]["content"] == "**目标**：判断股票近期是否出现金叉。"
    assert blocks[1]["data"]["questions"] == []
    assert blocks[1]["data"]["actions"][0]["action_id"] == "custom_tool.submit_clarification"


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

    assert [block["block_type"] for block in blocks] == ["artifact", "interaction"]
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
                "question": "输出候选列表还是排名？",
                "candidate": ["候选列表", "排名"],
            }],
            "design": {"tool_name": "selector", "display_name": "选股工具", "description": "", "inputs": [], "outputs": [], "modules": [], "flow": {"steps": [], "links": []}, "rules": [], "data_requirements": [], "exceptions": [], "acceptance": []},
            "design_artifact": {"design_artifact_id": "finance_tool_spec_selector", "design_revision": 1},
            "existing_analysis": {"analyzed": False, "current_behavior": [], "gaps": [], "affected_areas": [], "evidence": []},
        },
        stage="design",
    )

    interaction = next(block for block in blocks if block["block_type"] == "interaction")
    narrative = blocks[0]
    assert narrative["title"] == ""
    assert narrative["content"].startswith("明白，我们先从“设计选股工具”这个方向开始。")
    assert "先不替你假定具体规则" in narrative["content"]
    assert "确认 1 个真正决定工具形态的问题" in narrative["content"]
    assert "需要补充信息" not in str(blocks)
    assert interaction["title"] == "有几个关键点想和你确认"
    assert interaction["data"]["interaction_id"] == "custom_tool.requirement_clarification"
    assert interaction["data"]["submission_mode"] == "conversation"
    assert interaction["data"]["subject_revision"] == 1
    assert interaction["data"]["actions"] == [{
        "action_id": "custom_tool.submit_clarification",
        "label": "确定",
        "intent": "submit",
        "style": "primary",
        "expected_revision": 1,
    }]
    assert interaction["data"]["questions"][0]["candidate"][0] == "候选列表"
    assert all(block["data"].get("interaction_id") != "custom_tool.design_review" for block in blocks)


def test_design_question_rendering_preserves_every_question_from_the_skill() -> None:
    builder = LlmStreamBlockBuilder(run_id="all_questions")
    questions = [
        {
            "question": f"问题 {index}",
            "candidate": ["默认处理"],
        }
        for index in range(1, 7)
    ]

    blocks = builder.final_to_blocks({
        "status": "clarification",
        "understanding": {"goal": "设计工具", "expected_result": "待确认"},
        "questions": questions,
        "design": {"tool_name": "demo", "display_name": "演示工具", "description": "", "inputs": [], "outputs": [], "modules": [], "flow": {"steps": [], "links": []}, "rules": [], "data_requirements": [], "exceptions": [], "acceptance": []},
        "design_artifact": {"design_artifact_id": "finance_tool_spec_demo", "design_revision": 1},
        "existing_analysis": {"analyzed": False, "current_behavior": [], "gaps": [], "affected_areas": [], "evidence": []},
    }, stage="design")

    interaction = next(block for block in blocks if block["block_type"] == "interaction")
    assert [item["question"] for item in interaction["data"]["questions"]] == [f"问题 {index}" for index in range(1, 7)]


def test_extremely_vague_design_can_stay_partial_and_ask_only_blocking_questions() -> None:
    builder = LlmStreamBlockBuilder(run_id="vague_design")

    blocks = builder.final_to_blocks({
        "status": "clarification",
        "understanding": {
            "goal": "创建一个选股工具",
            "usage": "",
            "expected_result": "输出符合条件的股票",
            "confirmed_requirements": ["工具类型为选股工具"],
            "constraints": [],
            "assumptions": [],
        },
        "questions": [
            {"question": "你希望根据什么核心条件选股？", "candidate": ["技术面条件"]},
            {"question": "选股范围是什么？", "candidate": ["A股"]},
        ],
        "design": {
            "tool_name": "", "display_name": "选股工具", "description": "",
            "inputs": [], "outputs": [], "modules": [],
            "flow": {"steps": [], "links": []}, "rules": [],
            "data_requirements": [], "exceptions": [], "acceptance": [],
        },
        "existing_analysis": {"analyzed": False, "current_behavior": [], "gaps": [], "affected_areas": [], "evidence": []},
    }, stage="design")

    assert [block["block_type"] for block in blocks] == ["narrative", "artifact", "interaction"]
    assert "创建一个选股工具" in blocks[0]["content"]
    assert "先不替你假定具体规则" in blocks[0]["content"]
    assert "确认 2 个真正决定工具形态的问题" in blocks[0]["content"]
    assert blocks[1]["data"]["details"]["rules"] == []
    assert len(blocks[2]["data"]["questions"]) == 2


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


def test_high_frequency_sdk_deltas_only_emit_complete_semantic_units() -> None:
    builder = LlmStreamBlockBuilder(run_id="run_test")

    assert builder.event_to_blocks({"source": "codex", "type": "agent_delta", "content": "{"}) == []
    assert builder.event_to_blocks({"source": "codex", "type": "reasoning_delta", "content": "raw token"}) == []
    assert builder.event_to_blocks({"source": "codex", "type": "event", "content": "thread/tokenUsage/updated"}) == []

    summary = builder.event_to_blocks({"source": "codex", "type": "reasoning_summary_delta", "content": "正在核对金融口径"})
    assert len(summary) == 1
    assert summary[0]["block_type"] == "status"
    assert summary[0]["block_id"] == "design_reasoning_summary_delta"
    assert summary[0]["data"]["role"] == "process"
    assert summary[0]["data"]["format"] == "markdown"
    assert summary[0]["content"] == "正在核对金融口径"


def test_failed_stage_result_is_not_rendered_as_completed() -> None:
    builder = LlmStreamBlockBuilder(run_id="run_failed")

    blocks = builder.event_to_blocks({
        "source": "harness",
        "type": "stage_result",
        "content": "skill stage failed",
        "metadata": {"stage": "design", "ok": False},
    })

    progress = next(block for block in blocks if block["block_type"] == "status")
    assert progress["data"]["status"] == "error"
    assert progress["data"]["summary"] == "处理失败，请查看错误信息。"
    assert "items" not in progress["data"]


def test_design_json_stream_emits_complete_questions_and_flow_without_raw_json() -> None:
    builder = LlmStreamBlockBuilder(run_id="run_semantic")
    prefix = '{"status":"clarification","questions":['
    question = '{"question":"计算周期是多少？","candidate":["30日","60日"]}'

    assert builder.event_to_blocks({
        "source": "codex", "type": "agent_delta", "content": prefix + question[:-1],
        "metadata": {"stage": "design"},
    }) == []
    question_blocks = builder.event_to_blocks({
        "source": "codex", "type": "agent_delta", "content": question[-1] + '],"design":{"flow":{"steps":[',
        "metadata": {"stage": "design"},
    })
    assert [block["block_id"] for block in question_blocks] == ["design_questions"]
    assert question_blocks[0]["data"]["provisional"] is True
    assert question_blocks[0]["data"]["questions"][0]["question"] == "计算周期是多少？"
    assert "{\"question\"" not in str(question_blocks)

    step = '{"id":"load","type":"process","name":"读取行情","description":"加载日线"}'
    flow_blocks = builder.event_to_blocks({
        "source": "codex", "type": "agent_delta", "content": step + '],"links":[]}}}',
        "metadata": {"stage": "design"},
    })
    assert [block["block_id"] for block in flow_blocks] == ["design_artifact"]
    assert flow_blocks[0]["block_type"] == "artifact"
    assert flow_blocks[0]["data"]["details"]["flow"]["steps"][0]["name"] == "读取行情"


def test_semantic_stream_ignores_commentary_message_and_uses_final_answer_item() -> None:
    builder = LlmStreamBlockBuilder(run_id="run_phases")
    builder.event_to_blocks({
        "source": "codex",
        "type": "event",
        "content": "item/started",
        "metadata": {"stage": "design", "item": {"id": "draft", "type": "agentMessage", "phase": "commentary"}},
    })
    ignored = builder.event_to_blocks({
        "source": "codex",
        "type": "agent_delta",
        "content": '{"questions":[{"question":"临时问题","candidate":["默认"]}]}',
        "metadata": {"stage": "design", "item_id": "draft"},
    })
    assert ignored == []

    builder.event_to_blocks({
        "source": "codex",
        "type": "event",
        "content": "item/started",
        "metadata": {"stage": "design", "item": {"id": "final", "type": "agentMessage", "phase": "final_answer"}},
    })
    blocks = builder.event_to_blocks({
        "source": "codex",
        "type": "agent_delta",
        "content": '{"questions":[{"question":"正式问题","candidate":["默认"]}]}',
        "metadata": {"stage": "design", "item_id": "final"},
    })
    assert len(blocks) == 1
    assert blocks[0]["data"]["questions"][0]["question"] == "正式问题"


def test_coding_json_stream_emits_each_complete_module_as_one_code_preview() -> None:
    builder = LlmStreamBlockBuilder(run_id="run_coding_semantic")
    module = {
        "module_id": "main",
        "role": "入口",
        "language": "python",
        "entrypoint": "run",
        "functions": [],
        "source_code": "def run(inputs):\n    return inputs\n",
    }
    text = '{"implementation":{"modules":[' + __import__("json").dumps(module, ensure_ascii=False)
    assert builder.event_to_blocks({
        "source": "codex", "type": "agent_delta", "content": text[:-1],
        "metadata": {"stage": "coding"},
    }) == []
    blocks = builder.event_to_blocks({
        "source": "codex", "type": "agent_delta", "content": text[-1],
        "metadata": {"stage": "coding"},
    })
    assert len(blocks) == 1
    assert blocks[0]["block_id"] == "custom_tool_draft_summary"
    assert blocks[0]["data"]["files"][0]["content"].endswith("return inputs\n")


def test_coding_final_renders_natural_implementation_and_alignment_without_status_enum() -> None:
    builder = LlmStreamBlockBuilder(run_id="run_coding_final")

    blocks = builder.final_to_blocks({
        "message": "工具实现已完成。",
        "implementation_summary": "实现了数据读取、核心计算和结果组装三个内聚函数。",
        "verification": "实际运行代表性样例未报错；需求、设计与代码一致。",
        "sample_input_json": "{\"code\":\"600519.SH\"}",
    }, stage="coding")

    assert [block["block_id"] for block in blocks] == [
        "coding_final_summary",
        "coding_alignment",
    ]
    assert blocks[0]["title"] == "实现结果"
    assert "三个内聚函数" in blocks[0]["content"]
    assert blocks[1]["title"] == "运行验证与需求对齐"


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

    assert [block["block_type"] for block in start] == ["status"]
    assert start[0]["content"] == "正在整理实现方案…"
    assert context[0]["content"] == "已准备好本次需要的上下文。"
    assert context[-1]["data"]["current_step"] == "context_ready"
    assert reasoning[0]["data"]["current_step"] == "reasoning_summary_delta"
    assert reasoning[0]["data"]["summary"] == "正在整理实现方案…"
    assert "/private/tmp" not in str(context)
    assert "raw model text" not in str(reasoning[0])


def test_coding_context_and_codex_commentary_become_module_progress() -> None:
    builder = LlmStreamBlockBuilder(run_id="run_coding_progress")
    blocks = builder.event_to_blocks({
        "source": "harness",
        "type": "context_ready",
        "content": "context bundle prepared",
        "metadata": {
            "stage": "coding",
            "api_sources": ["stock.quote"],
            "module_plan": [
                {"id": "loader", "title": "数据读取", "message": "读取行情", "status": "pending"},
                {"id": "signal", "title": "信号计算", "message": "计算金叉", "status": "pending"},
            ],
        },
    })
    assert [block["block_id"] for block in blocks] == ["coding_live_progress"]

    builder.event_to_blocks({
        "source": "codex",
        "type": "event",
        "content": "item/started",
        "metadata": {
            "stage": "coding",
            "item": {"id": "progress_1", "type": "agentMessage", "phase": "commentary"},
        },
    })
    update = builder.event_to_blocks({
        "source": "codex",
        "type": "item_completed",
        "content": "数据读取函数已经完成，并通过两组聚焦样例。",
        "metadata": {"stage": "coding", "item": {"id": "progress_1", "phase": "commentary"}},
    })
    assert update[0]["block_id"] == "coding_module_progress"
    assert update[0]["data"]["role"] == "conversation_progress"
    assert update[0]["data"]["summary"] == "数据读取函数已经完成，并通过两组聚焦样例。"
    assert "raw" not in str(update[0])

    api_progress = builder.event_to_blocks({
        "source": "codex",
        "type": "event",
        "content": "item/started",
        "metadata": {
            "stage": "coding",
            "item": {
                "id": "exec_api",
                "type": "commandExecution",
                "command": "sed -n '1,200p' api_catalog/task_context.json",
            },
        },
    })
    assert api_progress[0]["data"]["summary"] == "正在定位所需的数据接口与运行约定。"
    assert api_progress[0]["data"]["role"] == "process"

    write_progress = builder.event_to_blocks({
        "source": "codex",
        "type": "event",
        "content": "item/started",
        "metadata": {"stage": "coding", "item": {"id": "edit_1", "type": "fileChange"}},
    })
    assert write_progress[0]["data"]["summary"] == "正在写入本轮工具实现。"

    test_progress = builder.event_to_blocks({
        "source": "codex",
        "type": "event",
        "content": "item/started",
        "metadata": {
            "stage": "coding",
            "item": {
                "id": "exec_test",
                "type": "commandExecution",
                "command": "python -m py_compile implementation/modules/main.py",
            },
        },
    })
    assert test_progress[0]["data"]["summary"] == "正在检查动态模块能否加载并运行。"


def test_coding_progress_extracts_human_summary_from_structured_commentary() -> None:
    builder = LlmStreamBlockBuilder(run_id="run_coding_structured_progress")

    update = builder.event_to_blocks({
        "source": "codex",
        "type": "item_completed",
        "content": json.dumps({
            "tool_contract": {"tool_name": "golden_cross"},
            "implementation_summary": "金叉窗口判断已完成，正在接入日线行情。",
        }, ensure_ascii=False),
        "metadata": {"stage": "coding", "item": {"id": "progress_1", "phase": "commentary"}},
    })

    assert update[0]["data"]["summary"] == "金叉窗口判断已完成，正在接入日线行情。"
    assert "tool_contract" not in str(update[0])


def test_structured_commentary_streams_only_the_human_summary_into_main_conversation() -> None:
    builder = LlmStreamBlockBuilder(run_id="run_coding_commentary_stream")
    builder.event_to_blocks({
        "source": "codex",
        "type": "event",
        "content": "item/started",
        "metadata": {
            "stage": "coding",
            "item": {"id": "progress_1", "type": "agentMessage", "phase": "commentary"},
        },
    })

    first = builder.event_to_blocks({
        "source": "codex",
        "type": "agent_delta",
        "content": '{"tool_contract":{"tool_name":"golden_cross"},"implementation_summary":"正在实现均线',
        "metadata": {"stage": "coding", "item_id": "progress_1"},
    })
    second = builder.event_to_blocks({
        "source": "codex",
        "type": "agent_delta",
        "content": '计算模块。"}',
        "metadata": {"stage": "coding", "item_id": "progress_1"},
    })

    assert first[0]["block_id"] == "coding_module_progress"
    assert first[0]["data"]["items"][-1]["summary"] == "正在实现均线"
    assert first[0]["data"]["streaming"] is True
    assert second[0]["data"]["items"][-1]["summary"] == "正在实现均线计算模块。"
    assert second[0]["data"]["items"][-1]["status"] == "completed"
    assert "tool_contract" not in str(first + second)


def test_requirement_and_design_documents_stream_as_markdown_and_reuse_final_block_ids() -> None:
    requirement_builder = LlmStreamBlockBuilder(run_id="run_requirement_stream")
    requirement = requirement_builder.event_to_blocks({
        "source": "claude",
        "type": "agent_delta",
        "content": '{"requirement_brief":"## 目标\\n识别近期金叉',
        "metadata": {"stage": "requirement"},
    })
    assert requirement[0]["block_id"] == "requirement_final_summary"
    assert requirement[0]["content"] == "## 目标\n识别近期金叉"
    assert requirement[0]["data"]["provisional"] is True

    design_builder = LlmStreamBlockBuilder(run_id="run_design_stream")
    design = design_builder.event_to_blocks({
        "source": "claude",
        "type": "agent_delta",
        "content": '{"document":"## 处理流程\\n1. 读取行情\\n2. 计算均线"}',
        "metadata": {"stage": "design"},
    })
    assert design[0]["block_id"] == "design_artifact"
    assert design[0]["content"].startswith("## 处理流程")
    assert design[0]["data"]["format"] == "markdown"
    assert design[0]["data"]["provisional"] is False


def test_json_command_output_is_kept_as_structured_process_data() -> None:
    builder = LlmStreamBlockBuilder(run_id="run_structured_output")

    first = builder.event_to_blocks({
        "source": "tool",
        "type": "command_output",
        "content": '{"ok":true,',
        "metadata": {"stage": "coding"},
    })
    complete = builder.event_to_blocks({
        "source": "tool",
        "type": "command_output",
        "content": '"count":2}',
        "metadata": {"stage": "coding"},
    })

    assert first == []
    assert complete[0]["data"]["format"] == "json"
    assert complete[0]["data"]["value"] == {"ok": True, "count": 2}
    assert complete[0]["data"]["role"] == "process"
