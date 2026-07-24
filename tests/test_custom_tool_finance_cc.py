from __future__ import annotations

from typing import Any, Dict

from src.services.custom_tool_service import CustomToolAgentService


class _FinanceCcStub:
    def __init__(self, result: Dict[str, Any]) -> None:
        self.result = result
        self.calls = []

    def run_turn(self, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append(kwargs)
        return dict(self.result)


def test_finance_cc_requirement_updates_existing_conversation_contract(monkeypatch) -> None:
    monkeypatch.setenv("FINANCE_CC_TOOL_DEVELOPMENT_ENABLED", "1")
    requirement = {
        "summary": "先确认筛选范围。",
        "requirement_brief": "从A股中筛选满足用户条件的股票，并返回候选股票列表。",
        "notice": ["未特别说明时先按最新完整交易日处理。"],
        "questions": [{"question": "是否全A股？", "candidate": ["是", "否"]}],
    }
    finance_cc = _FinanceCcStub(
        {
            "ok": True,
            "result": "先确认筛选范围。",
            "artifact_updates": [{"artifact_type": "requirement", "payload": requirement}],
            "interaction_requests": [],
        }
    )
    service = CustomToolAgentService(
        use_codex=False,
        finance_cc_service=finance_cc,
    )

    result = service.handle_turn(
        "做一个选股工具",
        state={},
        owner_id="owner-1",
        thread_id=17,
        turn_id=23,
    )

    assert result["design_status"] == "clarification"
    assert result["questions"] == requirement["questions"]
    assert result["notice"] == requirement["notice"]
    assert result["state"]["notice"] == requirement["notice"]
    assert result["state"]["requirement_brief"] == requirement["requirement_brief"]
    assert result["state"]["feedback_ledger"][0]["text"] == "做一个选股工具"
    assert finance_cc.calls[0]["thread_id"] == 17


def test_finance_cc_design_and_flow_are_merged_without_action_mapping(monkeypatch) -> None:
    monkeypatch.setenv("FINANCE_CC_TOOL_DEVELOPMENT_ENABLED", "1")
    design = {
        "tool_name": "golden_cross_scan",
        "display_name": "金叉扫描",
        "description": "扫描近期金叉",
    }
    finance_cc = _FinanceCcStub(
        {
            "ok": True,
            "result": "方案和流程已经形成。",
            "artifact_updates": [
                {"artifact_type": "design", "payload": {"design": design}},
                {"artifact_type": "flow", "payload": {"mermaid": "flowchart TD"}},
            ],
            "interaction_requests": [],
        }
    )
    service = CustomToolAgentService(use_codex=False, finance_cc_service=finance_cc)

    result = service.handle_turn(
        "按这个方案继续",
        state={"requirement_text": "扫描近期金叉"},
        owner_id="owner-1",
        thread_id=18,
        turn_id=24,
    )

    assert result["design_status"] == "review"
    assert result["state"]["tool_name"] == "golden_cross_scan"
    assert result["design"]["mermaid"] == "flowchart TD"


def test_finance_cc_requirement_and_design_in_same_turn_do_not_create_a_gate(monkeypatch) -> None:
    monkeypatch.setenv("FINANCE_CC_TOOL_DEVELOPMENT_ENABLED", "1")
    finance_cc = _FinanceCcStub(
        {
            "ok": True,
            "result": "需求已经收敛，设计方案也已形成。",
            "artifact_updates": [
                {
                    "artifact_type": "requirement",
                    "payload": {
                        "requirement_brief": "判断指定A股最近30或60日是否出现MA5上穿MA20。",
                        "questions": [],
                    },
                },
                {
                    "artifact_type": "design",
                    "payload": {
                        "design": "## 流程\n读取日线，计算均线，识别金叉并返回日期。",
                    },
                },
            ],
            "interaction_requests": [],
        }
    )
    service = CustomToolAgentService(use_codex=False, finance_cc_service=finance_cc)

    result = service.handle_turn(
        "按默认口径继续",
        state={"requirement_text": "做一个金叉识别工具"},
        owner_id="owner-1",
        thread_id=20,
        turn_id=26,
    )

    assert result["design_status"] == "review"
    assert result["questions"] == []
    assert result["state"]["requirement_brief"].startswith("判断指定A股")
    assert "计算均线" in result["state"]["design_contract"]["document"]
    assert "status" not in result["state"]


def test_finance_cc_keeps_valid_artifact_when_turn_ends_with_runtime_error(monkeypatch) -> None:
    monkeypatch.setenv("FINANCE_CC_TOOL_DEVELOPMENT_ENABLED", "1")
    requirement = {
        "summary": "需求已经确认。",
        "requirement_brief": "从A股中筛选股票并返回股票列表。",
        "notice": [],
        "questions": [],
    }
    finance_cc = _FinanceCcStub(
        {
            "ok": False,
            "error": "max turns reached",
            "result": "",
            "artifact_updates": [{"artifact_type": "requirement", "payload": requirement}],
            "interaction_requests": [],
        }
    )
    service = CustomToolAgentService(use_codex=False, finance_cc_service=finance_cc)

    result = service.handle_turn(
        "都按默认值处理",
        state={"requirement_text": "选股工具"},
        owner_id="owner-1",
        thread_id=19,
        turn_id=25,
    )

    assert result["error"] == "max turns reached"
    assert result["state"]["requirement_brief"] == "从A股中筛选股票并返回股票列表。"
    assert "requirement" in result["message"]
    assert "notice" not in result["state"]
    assert "questions" not in result["state"]
