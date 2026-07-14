from src.experiments.staged_data_protocol.phase2 import constitution_provider, context_builder, engine
from src.experiments.staged_data_protocol.phase2.api_runner import execute_api_call
from src.experiments.staged_data_protocol.phase2.call_parser import parse_api_call
from src.experiments.staged_data_protocol.phase2.call_validator import validate_call
from src.experiments.staged_data_protocol.phase2.catalog import resolve_api
from src.experiments.staged_data_protocol.phase2.models import ApiCall, ResultHandle, Step


def test_parse_phase2_response_accepts_ok_request_string():
    response = engine.parse_phase2_response(
        '{"status":"ok","res":"r1 = stock.quote() -> code, name"}'
    )

    assert response["status"] == "ok"
    assert response["res"] == "r1 = stock.quote() -> code, name"


def test_static_validation_feedback_is_used_in_next_loop(monkeypatch):
    responses = iter(
        [
            '{"status":"ok","res":"r1 = stock.quote() -> code, name, value"}',
            '{"status":"ok","res":"r1 = stock.quote() -> code, name"}',
        ]
    )

    def fake_call_llm(*, question, context_sections):
        return next(responses)

    def fake_execute_api_call(call, previous_results=None):
        return ResultHandle(
            name=call.result_id,
            api=call.api,
            columns=["code", "name"],
            data={"status": "ok", "rows": []},
        )

    monkeypatch.setattr(engine, "_call_llm", fake_call_llm)
    monkeypatch.setattr(engine, "execute_api_call", fake_execute_api_call)

    result = engine.run_phase2_steps(
        question="查询股票行情",
        step_lines=["S1 | stock | quote | 查询股票行情"],
        use_llm=True,
        max_iterations=2,
    )

    assert result["valid"] is True
    assert len(result["calls"]) == 2
    assert result["calls"][0]["validation"]["ok"] is False
    assert "STATIC_VALIDATION_FEEDBACK" in result["calls"][1]["api_context_sections"]["validation_feedback"]
    assert "`value` is not a standard output field of `stock.quote`" in result["calls"][1]["api_context_sections"]["validation_feedback"]


def test_empty_previous_result_rollback_is_retried_as_current_step_feedback(monkeypatch):
    responses = iter(
        [
            '{"status":"ok","res":"r1 = industry.base_info() -> industry_code, industry_name"}',
            '{"status":"roll_back","res":"r1 has row_count=0, no industry list available to query constitution."}',
            '{"status":"ok","res":"r2 = industry.constitution(filter = \\"industry_code in r1.industry_code\\") -> industry_code, industry_name, stock_code, stock_name"}',
        ]
    )

    def fake_call_llm(*, question, context_sections):
        return next(responses)

    def fake_execute_api_call(call, previous_results=None):
        if call.result_id == "r1":
            return ResultHandle(
                name=call.result_id,
                api=call.api,
                columns=["industry_code", "industry_name"],
                data={"status": "prepared", "provider": "pending_real_api_adapter", "rows": []},
            )
        return ResultHandle(
            name=call.result_id,
            api=call.api,
            columns=["industry_code", "industry_name", "stock_code", "stock_name"],
            data={"status": "ok", "rows": []},
        )

    monkeypatch.setattr(engine, "_call_llm", fake_call_llm)
    monkeypatch.setattr(engine, "execute_api_call", fake_execute_api_call)

    result = engine.run_phase2_steps(
        question="按申万一级行业统计今天股票平均涨跌幅。",
        step_lines=[
            "S1 | industry | base_info | 查询所有申万一级行业列表（名称、代码）",
            "S2 | industry | constitution | 查询S1中每个行业的成分股列表",
        ],
        use_llm=True,
        max_iterations=3,
    )

    assert result["valid"] is True
    assert len(result["calls"]) == 3
    assert result["calls"][1]["validation"]["errors"][0].startswith("ROLLBACK_RULE_ERROR")
    assert "rollback_to" not in result["calls"][1]
    assert "Do not roll_back only because" in result["calls"][2]["api_context_sections"]["validation_feedback"]


def test_final_check_feedback_reruns_output_step(monkeypatch):
    responses = iter(
        [
            '{"status":"ok","res":"r1 = stock.quote() -> code, name, close"}',
            '{"status":"ok","res":"r2 = stock.quote() -> code, name, pct"}',
        ]
    )
    final_checks = iter(
        [
            '{"status":"need_check","feedback":"Original question also requires pct, but result schema only has close."}',
            '{"status":"OK","feedback":"close and pct are both present."}',
        ]
    )

    def fake_call_llm(*, question, context_sections):
        return next(responses)

    def fake_final_check(prompt):
        return next(final_checks)

    def fake_phase1_repair(prompt):
        return (
            '{"analyze":"keep old step and add missing pct step",'
            '"steps":["S1 | stock | quote | 查询股票最新价和涨跌幅(output)",'
            '"S2 | stock | quote | 补充查询股票涨跌幅(output)"]}'
        )

    def fake_execute_api_call(call, previous_results=None):
        return ResultHandle(
            name=call.result_id,
            api=call.api,
            columns=list(call.outputs),
            data={"status": "ok", "rows": []},
        )

    monkeypatch.setattr(engine, "_call_llm", fake_call_llm)
    monkeypatch.setattr(engine, "_call_final_check_llm", fake_final_check)
    monkeypatch.setattr(engine, "_call_phase1_repair_llm", fake_phase1_repair)
    monkeypatch.setattr(engine, "execute_api_call", fake_execute_api_call)

    result = engine.run_phase2_steps(
        question="查询股票最新价和涨跌幅",
        step_lines=["S1 | stock | quote | 查询股票最新价和涨跌幅(output)"],
        use_llm=True,
        enable_final_check=True,
        max_iterations=5,
    )

    assert result["valid"] is True
    assert [item["status"] for item in result["final_checks"]] == ["need_check", "OK"]
    assert len(result["calls"]) == 2
    assert result["steps"][1] == "S2 | stock | quote | 补充查询股票涨跌幅(output)"
    assert "FINAL_CHECK_FEEDBACK" in result["calls"][1]["api_context_sections"]["validation_feedback"]
    assert "pct" in result["calls"][1]["raw_call"]


def test_phase1_repair_response_accepts_step_change_flags():
    response = engine.parse_phase1_repair_response(
        '{"analyze":"补充缺失指标","steps":['
        '"S1 | industry | constitution | 获取行业成分股 | 0",'
        '"S2 | industry | constitution | 统计PE中位数(output) | 1"'
        "]}"
    )

    assert response["status"] == "OK"
    assert response["steps"] == [
        "S1 | industry | constitution | 获取行业成分股",
        "S2 | industry | constitution | 统计PE中位数(output)",
    ]
    assert response["step_flags"] == [0, 1]


def test_final_check_repair_flags_choose_rerun_start(monkeypatch):
    responses = iter(
        [
            '{"status":"ok","res":"r1 = industry.constitution() -> industry_code, industry_name, stock_code"}',
            '{"status":"ok","res":"r2 = industry.constitution.agg(metric = stock.pricevalue.pe, agg = median, group_by = \\"industry_code, industry_name\\") -> industry_code, industry_name, median(pe) as median_pe"}',
            '{"status":"ok","res":"r2 = industry.constitution.agg(metric = stock.pricevalue.pb, agg = median, group_by = \\"industry_code, industry_name\\") -> industry_code, industry_name, median(pb) as median_pb"}',
        ]
    )
    final_checks = iter(
        [
            '{"status":"need_check","feedback":"缺少PB中位数，S1可沿用。"}',
            '{"status":"OK","feedback":"PB中位数已补充。"}',
        ]
    )

    def fake_call_llm(*, question, context_sections):
        return next(responses)

    def fake_final_check(prompt):
        return next(final_checks)

    def fake_phase1_repair(prompt):
        return (
            '{"analyze":"S1正确，S2改为PB中位数",'
            '"steps":["S1 | industry | constitution | 获取行业成分股 | 0",'
            '"S2 | industry | constitution | 补充统计PB中位数(output) | 1"]}'
        )

    def fake_execute_api_call(call, previous_results=None):
        return ResultHandle(
            name=call.result_id,
            api=call.api,
            columns=list(call.outputs),
            data={"status": "ok", "rows": []},
        )

    monkeypatch.setattr(engine, "_call_llm", fake_call_llm)
    monkeypatch.setattr(engine, "_call_final_check_llm", fake_final_check)
    monkeypatch.setattr(engine, "_call_phase1_repair_llm", fake_phase1_repair)
    monkeypatch.setattr(engine, "execute_api_call", fake_execute_api_call)

    result = engine.run_phase2_steps(
        question="按行业统计A股PE中位数和PB中位数。",
        step_lines=[
            "S1 | industry | constitution | 获取行业成分股",
            "S2 | industry | constitution | 统计PE中位数(output)",
        ],
        use_llm=True,
        enable_final_check=True,
        max_iterations=6,
    )

    assert result["valid"] is True
    assert len(result["calls"]) == 2
    assert [call["call"]["result_id"] for call in result["calls"]] == ["r1", "r2"]
    assert "获取行业成分股" in result["calls"][0]["step"]
    assert "补充统计PB中位数" in result["calls"][1]["step"]
    assert result["final_checks"][0]["repair_step_flags"] == [0, 1]
    assert result["final_checks"][0]["repair_steps"][1] == "S2 | industry | constitution | 补充统计PB中位数(output)"
    assert result["steps"] == [
        "S1 | industry | constitution | 获取行业成分股",
        "S2 | industry | constitution | 补充统计PB中位数(output)",
    ]


def test_phase1_repair_prompt_injects_previous_subject_dataview_catalog():
    steps = [
        Step(
            step_id="S1",
            subject="industry",
            dataview="constitution",
            condition_desc="获取行业成分股",
            raw="S1 | industry | constitution | 获取行业成分股",
        )
    ]

    prompt = engine.build_phase1_repair_prompt(
        question="按行业统计A股PE中位数和PB中位数。",
        steps=steps,
        calls=[],
        previous_results={},
        final_check_feedback="缺少PB中位数",
    )

    subject_section = prompt.split("# subject and data_views", 1)[1]
    assert "## `industry`" in subject_section
    assert "`constitution` (used in previous steps)" in subject_section
    assert "按行业聚合成分股" in subject_section
    assert "估值" in subject_section
    assert "## `stock`" not in subject_section
    assert "按行业统计A股PE中位数和PB中位数。" in prompt
    assert "# Original Question" in prompt
    assert "# Output" in prompt


def test_margin_pct_change_method_is_resolved_and_validated():
    resolved = resolve_api("stock.margin.kd_financing_balance_pct_change")

    assert resolved["type"] == "kd"
    assert resolved["subject"] == "stock"
    assert resolved["dataview"] == "margin"
    assert resolved["field"] == "financing_balance"
    assert resolved["method"] == "pct_change"

    call = parse_api_call(
        "r1 = stock.margin.kd_financing_balance_pct_change(k = 5, order = \"value desc\", limit = 10) "
        "-> code, name, value as financing_balance_pct_change_5d, start_value, current_value"
    )

    validation = validate_call(call, previous_results={})

    assert validation.ok is True


def test_margin_allows_requested_method_for_any_metric_field():
    call = parse_api_call(
        "r1 = stock.margin.kd_financing_balance_sum(k = 5, order = \"value desc\", limit = 10) "
        "-> code, name, value as financing_balance_sum_5d"
    )

    validation = validate_call(call, previous_results={})

    assert validation.ok is True


def test_margin_allows_unregistered_kd_method_for_provider_fallback():
    call = parse_api_call(
        "r1 = stock.margin.kd_financing_balance_latest(k = 5, order = \"value desc\", limit = 10) "
        "-> code, name, value as financing_balance_latest_5d"
    )

    validation = validate_call(call, previous_results={})

    assert validation.ok is True


def test_margin_context_exposes_base_and_kday_api():
    sections = context_builder.build_context_sections(
        step=Step(
            step_id="S1",
            subject="stock",
            dataview="margin",
            condition_desc="查询近5日融资余额变化最大的股票",
            raw="S1 | stock | margin | 查询近5日融资余额变化最大的股票",
        ),
        previous_results={},
        result_id="r1",
    )

    assert "`stock.margin`" in sections["available_apis"]
    assert "`stock.margin.kd_<field>_<method>`" in sections["available_apis"]
    assert "financing_balance: sum, avg, max, min, median, change, pct_change" in sections["current_dataview"]
    assert "financing_balance: 融资余额（元）" in sections["current_dataview"]


def test_finance_catalog_context_exposes_verified_field_units():
    quote_sections = context_builder.build_context_sections(
        step=Step(
            step_id="S1",
            subject="stock",
            dataview="quote",
            condition_desc="查询成交额和成交量",
            raw="S1 | stock | quote | 查询成交额和成交量",
        ),
        previous_results={},
        result_id="r1",
    )
    fund_sections = context_builder.build_context_sections(
        step=Step(
            step_id="S2",
            subject="fund",
            dataview="quote",
            condition_desc="查询基金折价和份额",
            raw="S2 | fund | quote | 查询基金折价和份额",
        ),
        previous_results={},
        result_id="r2",
    )

    assert "amount: 截至该分钟累计成交额（元）" in quote_sections["current_dataview"]
    assert "volumn: 累计成交量（历史日线：股；实时分钟快照：手，1手=100股）" in quote_sections["current_dataview"]
    assert "pct: 涨跌幅（%，3.5%记为3.5）" in quote_sections["current_dataview"]
    assert "discount: 折价额（元，单位净值-收盘价）" in fund_sections["current_dataview"]
    assert "unit_total: 基金份额（份）" in fund_sections["current_dataview"]


def test_aggregation_step_can_see_run_result_handles():
    step = Step(
        step_id="S4",
        subject="industry",
        dataview="constitution",
        condition_desc="基于前面步骤的结果，按行业聚合计算成分股收益率平均值",
        raw="S4 | industry | constitution | 基于前面步骤的结果，按行业聚合计算成分股收益率平均值",
    )
    previous_results = {
        "r1": ResultHandle(
            name="r1",
            api="industry.constitution",
            columns=["industry_code", "industry_name", "stock_code"],
            data={"status": "ok", "rows": []},
            step_id="S1",
            task="查询行业成分股",
        ),
        "r3": ResultHandle(
            name="r3",
            api="stock.quote.dynamic_cal",
            columns=["code", "name", "interval_return"],
            data={"status": "ok", "rows": [{"code": "600000.SH", "interval_return": 0.1}]},
            step_id="S3",
            task="计算每只股票过去20个交易日区间收益率",
        ),
    }

    sections = context_builder.build_context_sections(
        step=step,
        previous_results=previous_results,
        result_id="r4",
    )

    assert "`r1`" in sections["session_results"]
    assert "`r3`" in sections["session_results"]
    assert "Step 1" in sections["session_results"]
    assert "Step 3" in sections["session_results"]
    assert "计算每只股票过去20个交易日区间收益率" in sections["session_results"]
    assert "interval_return" in sections["session_results"]


def test_constitution_agg_accepts_previous_result_metric():
    call = ApiCall(
        result_id="r2",
        api="industry.constitution.agg",
        args={
            "filter": "stock_code in r1.code",
            "metric": "r1.interval_return",
            "agg": "avg",
            "group_by": "industry_code, industry_name",
        },
        outputs=["industry_code", "industry_name", "avg(interval_return) as avg_return"],
        raw="",
    )
    previous_results = {
        "r1": ResultHandle(
            name="r1",
            api="stock.quote.dynamic_cal",
            columns=["code", "name", "interval_return"],
            data={"status": "ok", "rows": []},
        )
    }

    validation = validate_call(call, previous_results)

    assert validation.ok is True


def test_constitution_agg_executes_previous_result_metric(monkeypatch):
    def fake_execute_constitution_api(*, subject, args, outputs):
        return {
            "status": "ok",
            "columns": ["industry_code", "industry_name", "stock_code"],
            "rows": [
                {"industry_code": "801010", "industry_name": "银行", "stock_code": "600000.SH"},
                {"industry_code": "801010", "industry_name": "银行", "stock_code": "600001.SH"},
                {"industry_code": "801020", "industry_name": "煤炭", "stock_code": "600002.SH"},
            ],
        }

    monkeypatch.setattr(constitution_provider, "execute_constitution_api", fake_execute_constitution_api)
    call = ApiCall(
        result_id="r2",
        api="industry.constitution.agg",
        args={
            "filter": "stock_code in r1.code",
            "metric": "r1.interval_return",
            "agg": "avg",
            "group_by": "industry_code, industry_name",
            "order": "avg_return desc",
        },
        outputs=["industry_code", "industry_name", "avg(interval_return) as avg_return"],
        raw="",
    )
    previous_results = {
        "r1": ResultHandle(
            name="r1",
            api="stock.quote.dynamic_cal",
            columns=["code", "name", "interval_return"],
            data={
                "status": "ok",
                "rows": [
                    {"code": "600000.SH", "name": "A", "interval_return": 0.1},
                    {"code": "600001.SH", "name": "B", "interval_return": 0.3},
                    {"code": "600002.SH", "name": "C", "interval_return": -0.2},
                ],
            },
        )
    }

    result = execute_api_call(call, previous_results=previous_results)

    assert result.columns == ["industry_code", "industry_name", "avg_return"]
    assert result.data["status"] == "ok"
    assert result.data["row_count"] == 2
    assert result.data["rows"][0] == {"industry_code": "801010", "industry_name": "银行", "avg_return": 0.2}
    assert result.data["diagnostics"]["joined_row_count"] == 3
