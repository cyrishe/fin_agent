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
