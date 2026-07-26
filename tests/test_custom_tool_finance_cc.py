from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from src.services.custom_tool_service import CustomToolAgentService
from src.services.llm_stream_block_service import LlmStreamBlockBuilder


ROOT = Path(__file__).resolve().parents[1]


class _FinanceCcStub:
    def __init__(self, result: Dict[str, Any]) -> None:
        self.result = result
        self.calls = []

    def run_turn(self, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append(kwargs)
        return dict(self.result)


def test_finance_cc_waits_for_requirement_confirmation_before_design() -> None:
    prompt = (ROOT / "src/prompts/finance_cc/main.system.md").read_text(encoding="utf-8")

    assert "即使没有需要补充的问题，也在这里停下" in prompt
    assert "不读取数据目录，不决定数据获取、聚合或代码实现方式" in prompt
    assert "不要向用户说 `notice`、`questions` 等协议字段名" in prompt


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
    assert result["design_artifact"]["design_revision"] == 1
    assert result["design_artifact"]["design_artifact_id"]


def test_finance_cc_natural_language_design_renders_once_with_current_revision(monkeypatch) -> None:
    monkeypatch.setenv("FINANCE_CC_TOOL_DEVELOPMENT_ENABLED", "1")
    finance_cc = _FinanceCcStub(
        {
            "ok": True,
            "result": "这里是模型生成的完整设计全文，不应再单独展示。",
            "artifact_updates": [
                {
                    "artifact_type": "design",
                    "payload": {
                        "design": "## 工具概述\n判断股票是否满足金叉条件。\n\n## 输入输出\n输入股票，输出判断结果。",
                    },
                },
                {"artifact_type": "flow", "payload": {"mermaid": "flowchart TD\nA --> B"}},
            ],
            "interaction_requests": [],
        }
    )
    service = CustomToolAgentService(use_codex=False, finance_cc_service=finance_cc)

    result = service.handle_turn(
        "根据确认需求形成设计",
        state={"requirement_brief": "判断指定股票近期是否出现金叉"},
        owner_id="owner-1",
        thread_id=21,
        turn_id=27,
    )
    blocks = LlmStreamBlockBuilder(run_id="finance_cc_design").final_to_blocks(
        {**result, "status": result["design_status"]},
        stage="design",
    )

    assert [block["block_type"] for block in blocks] == ["artifact", "interaction"]
    assert blocks[0]["data"]["details"]["document"].startswith("## 工具概述")
    assert blocks[0]["data"]["details"]["mermaid"] == "flowchart TD\nA --> B"
    assert blocks[1]["data"]["actions"][0]["expected_revision"] == 1


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


def test_finance_cc_coding_turn_uses_authoritative_failure_and_does_not_replay_design(monkeypatch) -> None:
    monkeypatch.setenv("FINANCE_CC_TOOL_DEVELOPMENT_ENABLED", "1")
    finance_cc = _FinanceCcStub(
        {
            "ok": True,
            "result": "实现已经完成，可以直接使用。",
            "artifact_updates": [],
            "interaction_requests": [],
            "implementation_runs": [
                {
                    "ok": False,
                    "message": "实现未完成：测试命令被权限拒绝。当前设计已保留。",
                    "coding_status": "coding_failed",
                    "coding_error": {
                        "code": "coding_tool_permission_denied",
                        "summary": "测试命令被权限拒绝。",
                    },
                    "test_result": {},
                    "state": {},
                }
            ],
        }
    )
    service = CustomToolAgentService(use_codex=False, finance_cc_service=finance_cc)

    result = service.handle_turn(
        "重试实现",
        state={
            "requirement_brief": "判断指定股票近期是否出现金叉。",
            "design_contract": {"document": "## 流程\n计算均线并识别金叉。"},
            "design_revision": 1,
        },
        owner_id="owner-1",
        thread_id=22,
        turn_id=28,
    )

    assert result["coding_status"] == "coding_failed"
    assert result["message"].startswith("实现未完成：测试命令被权限拒绝")
    assert result["design"] == {}
    assert result["design_artifact"] == {}
    assert result["understanding"] == {}
