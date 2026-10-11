import src.services.tool_plan_runtime_service as runtime_module
from src.services.tool_plan_runtime_service import ToolPlanRuntimeService
from src.skill_runtime.context_retention import reduce_tool_result_for_runtime


def test_dynamic_tool_without_static_spec_keeps_business_output_for_summary_and_render(monkeypatch):
    result = {
        "tool": "ct_demo_assessment",
        "ok": True,
        "data": {
            "results": [
                {
                    "stock_code": "600320",
                    "status": "insufficient_data",
                    "reason": "股票代码需要交易所后缀。",
                }
            ],
            "summary": {
                "status": "insufficient_data",
                "insufficient_data_count": 1,
            },
        },
        "error": "",
    }

    retention = reduce_tool_result_for_runtime("ct_demo_assessment", result)

    assert retention["prompt_context"]["compressed"]["data"]["results"]["first"]["status"] == "insufficient_data"
    assert retention["render_artifacts"]["data.results"][0]["reason"] == "股票代码需要交易所后缀。"
    assert retention["render_artifacts"]["data.summary"]["status"] == "insufficient_data"

    service = ToolPlanRuntimeService(enable_tool_preflight=False)
    monkeypatch.setattr(service, "_refine_uncertain_render_blocks_with_llm", lambda sections: sections)
    render_payload = service._build_render_payload(
        execution_plan={"objective": "评价利润质量"},
        final_output={
            "summary": "输入数据不足。",
            "facts": [],
            "risks": [{"type": "data", "description": "股票代码需要交易所后缀。"}],
        },
        tool_runs=[
            {
                "tool_name": "ct_demo_assessment",
                "status": "completed",
                "result": result,
                "retention": retention,
            }
        ],
    )

    blocks = [block for section in render_payload["sections"] for block in section.get("blocks", [])]
    assert any(block.get("type") == "table" and block.get("data", {}).get("rows") for block in blocks)
    assert any(block.get("type") == "metric_strip" for block in blocks)


class _RuntimeStub:
    def begin_artifact_run(self, **_kwargs):
        return {"thread_id": None, "task_id": None, "turn_id": None}

    def append_event(self, **_kwargs):
        return None

    def finish_task(self, **_kwargs):
        return None

    def execute_tool(self, *, tool_name, args, executor):
        return executor(args)


def test_batch_tool_without_result_evidence_is_not_summarized_as_no_signal(monkeypatch):
    monkeypatch.setattr(
        runtime_module,
        "run_tool",
        lambda _name, _args, runtime_ctx=None: {
            "tool": "ct_batch_probe",
            "ok": True,
            "data": {"key_process_info": {}},
            "error": "",
        },
    )
    monkeypatch.setattr(
        runtime_module,
        "chat_qwen_json",
        lambda *_args, **_kwargs: (
            {"summary": "所有标的均无信号", "facts": [{"detail": "不应保留"}], "risks": []},
            {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        ),
    )
    events = []
    service = ToolPlanRuntimeService(
        runtime_execution_service=_RuntimeStub(),
        enable_tool_preflight=False,
    )

    result = service.execute_for_assistant(
        execution_plan={
            "objective": "扫描测试板块",
            "work_items": [{
                "step_id": "scan",
                "type": "tool",
                "name": "ct_batch_probe",
                "arguments": {"universe": ["000001.SZ", "000002.SZ", "000003.SZ"]},
            }],
        },
        user_text="扫描测试板块",
        event_sink=events.append,
    )

    assert "无法确认所有输入标的均已扫描" in result["final_output"]["summary"]
    assert result["final_output"]["facts"] == []
    assert result["runtime_trace"].get("_event_sink") is None
    assert any(
        item["data"]["status"] == "running" and "3 个标的" in item["content"]
        for item in events
    )
    assert any(
        "无法确认逐项覆盖" in item["content"]
        for item in events
    )
    assert events[-1]["data"]["event_type"] == "tool_plan_completed"
    blocks = [
        block
        for section in result["render_payload"]["sections"]
        for block in section.get("blocks", [])
    ]
    warning = next(block for block in blocks if block.get("block_id") == "ct_batch_probe_coverage_warning")
    assert warning["type"] == "assessment"
    assert warning["data"]["overall"] == "warn"
    assert not any(block.get("title") == "data / key process info" for block in blocks)


def test_repeated_strategy_runs_render_coverage_signal_and_data_gaps():
    service = ToolPlanRuntimeService(enable_tool_preflight=False)
    tool_runs = [
        {
            "tool_name": "ct_strategy",
            "status": "completed",
            "result": {
                "ok": True,
                "data": {
                    "entry_signal": False,
                    "reason": "double-pullback conditions are not all satisfied",
                    "key_process_info": {"stock_code": "000001.SZ", "candle_count": 48},
                },
            },
            "retention": {},
        },
        {
            "tool_name": "ct_strategy",
            "status": "completed",
            "result": {
                "ok": True,
                "data": {
                    "entry_signal": False,
                    "reason": "insufficient completed 60-minute candles",
                    "key_process_info": {"stock_code": "000002.SZ", "candle_count": 0},
                },
            },
            "retention": {},
        },
        {"tool_name": "ct_strategy", "status": "failed", "result": {}, "retention": {}},
    ]

    final = service._normalize_final_output(  # noqa: SLF001
        {"summary": "错误的全量结论", "facts": [], "risks": []},
        tool_runs,
    )
    render_payload = service._build_render_payload(  # noqa: SLF001
        execution_plan={"objective": "扫描板块"},
        final_output=final,
        tool_runs=tool_runs,
    )

    assert final["summary"] == "共计划运行 3 项，成功返回 2 项，未返回 1 项；触发信号 0 项，其中 1 项数据不足。"
    blocks = [
        block
        for section in render_payload["sections"]
        for block in section.get("blocks", [])
    ]
    overview = next(block for block in blocks if block.get("block_id") == "ct_strategy_batch_overview")
    assert overview["data"]["items"] == [
        {"label": "计划执行", "value": 3},
        {"label": "成功返回", "value": 2},
        {"label": "未返回", "value": 1},
        {"label": "触发信号", "value": 0},
        {"label": "数据不足", "value": 1},
    ]
    exceptions = next(block for block in blocks if block.get("block_id") == "ct_strategy_exceptions")
    assert exceptions["data"]["rows"] == [{
        "研究目标": "000002.SZ",
        "结果": "insufficient completed 60-minute candles",
        "数据量": 0,
    }]


def test_repeated_entity_list_results_preserve_targets_and_chinese_data_gaps():
    service = ToolPlanRuntimeService(enable_tool_preflight=False)
    tool_runs = [
        {
            "tool_name": "ct_list_strategy",
            "status": "completed",
            "plan": {"arguments": {"stock_codes": ["000001.SZ"]}},
            "result": {
                "ok": True,
                "data": {
                    "results": [{
                        "stock_code": "000001.SZ",
                        "entry_signal": 0,
                        "reason": ["未满足双回撤条件"],
                        "key_process_info": {"candle_count": 48},
                    }],
                    "key_process_info": {},
                },
            },
            "retention": {},
        },
        {
            "tool_name": "ct_list_strategy",
            "status": "completed",
            "plan": {"arguments": {"stock_codes": ["000002.SZ"]}},
            "result": {
                "ok": True,
                "data": {
                    "results": [{
                        "stock_code": "000002.SZ",
                        "entry_signal": 0,
                        "reason": "60分钟K线数据不足",
                        "key_process_info": {"candle_count": 3},
                    }],
                    "key_process_info": {},
                },
            },
            "retention": {},
        },
    ]

    final = service._normalize_final_output(  # noqa: SLF001
        {"summary": "不可采信的全量结论", "facts": [], "risks": []},
        tool_runs,
    )
    render_payload = service._build_render_payload(  # noqa: SLF001
        execution_plan={"objective": "扫描板块"},
        final_output=final,
        tool_runs=tool_runs,
    )

    assert final["summary"] == "共计划运行 2 项，成功返回 2 项；触发信号 0 项，其中 1 项数据不足。"
    blocks = [
        block
        for section in render_payload["sections"]
        for block in section.get("blocks", [])
    ]
    exceptions = next(block for block in blocks if block.get("block_id") == "ct_list_strategy_exceptions")
    assert exceptions["data"] == {
        "columns": ["研究目标", "结果", "数据量"],
        "rows": [{
            "研究目标": "000002.SZ",
            "结果": "60分钟K线数据不足",
            "数据量": 3,
        }],
    }
    distribution = next(block for block in blocks if block.get("block_id") == "ct_list_strategy_reason_distribution")
    assert {row["结果"] for row in distribution["data"]["rows"]} == {
        "未满足双回撤条件",
        "60分钟K线数据不足",
    }


def test_single_list_native_strategy_streams_progress_and_renders_batch_evidence(
    monkeypatch,
):
    def fake_run_tool(_name, args, runtime_ctx=None):
        progress_sink = (runtime_ctx or {}).get("_progress_sink")
        progress_sink({
            "level": "info",
            "message": "正在读取60分钟行情",
            "data": {"target_count": 3},
        })
        progress_sink({
            "level": "info",
            "message": "行情读取完成，开始逐标的判断",
            "data": {"bar_count": 123},
        })
        return {
            "tool": "ct_list_strategy",
            "ok": True,
            "data": {
                "ok": True,
                "results": [
                    {
                        "stock_code": "000001.SZ",
                        "entry_signal": False,
                        "condition_pass_count": 4,
                        "failed_condition_count": 2,
                        "latest_close": 10.5,
                        "reason": "未满足双回撤条件",
                        "key_process_info": {"candle_count": 60},
                    },
                    {
                        "stock_code": "000002.SZ",
                        "entry_signal": False,
                        "reason": "60分钟K线数据不足",
                        "key_process_info": {"candle_count": 3},
                    },
                    {
                        "stock_code": "000003.SZ",
                        "entry_signal": True,
                        "key_process_info": {"candle_count": 60},
                    },
                ],
                "key_process_info": {
                    "target_count": 3,
                    "signal_count": 1,
                    "query_count": 1,
                },
            },
            "error": "",
        }

    monkeypatch.setattr(runtime_module, "run_tool", fake_run_tool)
    monkeypatch.setattr(
        runtime_module,
        "chat_qwen_json",
        lambda *_args, **_kwargs: (
            {
                "summary": "含糊总结",
                "facts": [{"category": "观察", "detail": "000001.SZ 最接近触发条件。"}],
                "risks": [],
            },
            {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        ),
    )
    events = []
    service = ToolPlanRuntimeService(
        runtime_execution_service=_RuntimeStub(),
        enable_tool_preflight=False,
    )

    result = service.execute_for_assistant(
        execution_plan={
            "objective": "扫描测试板块",
            "work_items": [{
                "step_id": "scan",
                "type": "tool",
                "name": "ct_list_strategy",
                "arguments": {
                    "stock_codes": ["000001.SZ", "000002.SZ", "000003.SZ"],
                },
            }],
        },
        user_text="扫描测试板块",
        event_sink=events.append,
    )

    assert result["final_output"]["summary"] == (
        "已扫描 3 个目标，返回 3 条逐目标结果；触发信号 1 项，其中 1 项数据不足。"
    )
    assert result["message"] == (
        "已扫描 3 个目标，返回 3 条逐目标结果；触发信号 1 项，其中 1 项数据不足。\n"
        "关键发现：\n"
        "- 000001.SZ 最接近触发条件。"
    )
    assert [
        item["content"]
        for item in events
        if item["data"]["event_type"] == "tool_progress"
    ] == ["正在读取60分钟行情", "行情读取完成，开始逐标的判断"]
    run = result["runtime_contract"]["modules"][0]
    assert run["status"] == "completed"
    blocks = [
        block
        for section in result["render_payload"]["sections"]
        for block in section.get("blocks", [])
    ]
    overview = next(
        block
        for block in blocks
        if block.get("block_id") == "ct_list_strategy_batch_overview"
    )
    assert overview["data"]["items"] == [
        {"label": "研究目标", "value": 3},
        {"label": "返回结果", "value": 3},
        {"label": "触发信号", "value": 1},
        {"label": "数据不足", "value": 1},
    ]
    exceptions = next(
        block
        for block in blocks
        if block.get("block_id") == "ct_list_strategy_exceptions"
    )
    assert {row["研究目标"] for row in exceptions["data"]["rows"]} == {
        "000002.SZ",
        "000003.SZ",
    }
    result_table = next(
        block
        for block in blocks
        if block.get("block_id") == "data.results"
    )
    assert result_table["type"] == "table"
    assert len(result_table["data"]["rows"]) == 3
    near_matches = next(
        block
        for block in blocks
        if block.get("block_id") == "ct_list_strategy_near_matches"
    )
    assert near_matches["data"]["rows"][0] == {
        "研究目标": "000001.SZ",
        "通过条件": 4,
        "未满足条件": 2,
        "最新价": 10.5,
        "原因": "未满足双回撤条件",
    }
