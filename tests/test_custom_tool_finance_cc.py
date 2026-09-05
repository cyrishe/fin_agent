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


class _ProgressAwareFinanceCcStub(_FinanceCcStub):
    def initial_progress_event(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "source": "claude",
            "type": "reasoning_summary_delta",
            "content": "正在理解自定义工具需求。",
            "metadata": {
                "stage": "requirement",
                "progress_id": "custom_tool_understanding",
                "title": "工具需求与设计",
                "status": "running",
            },
        }


def _confirmed_requirement_state(
    requirement_brief: str,
    **values: Any,
) -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "custom_tool_flow_id": "flow-confirmed-requirement",
        "requirement_brief": requirement_brief,
        **values,
    }
    state.update(
        CustomToolAgentService._requirement_artifact_identity(
            requirement_brief,
            state=state,
        )
    )
    state["confirmed_requirement_revision"] = state["requirement_revision"]
    return state


def test_finance_cc_waits_for_requirement_confirmation_before_design() -> None:
    prompt = (ROOT / "src/prompts/finance_cc/main.system.md").read_text(encoding="utf-8")

    assert "即使没有需要补充的问题，也在这里停下" in prompt
    assert "不读取数据目录，不决定数据获取、聚合或代码实现方式" in prompt
    assert "不要向用户说 `notice`、`questions` 等协议字段名" in prompt
    assert "流程图不再是可选项" in prompt
    assert "只有两份资产都保存成功" in prompt
    assert "没有已保存的 `flow` 时不得进入 Coding" in prompt


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
    assert result["state"]["requirement_revision"] == 1
    assert result["state"]["requirement_artifact_id"]
    assert result["state"]["requirement_fingerprint"]
    assert result["requirement_artifact"]["requirement_revision"] == 1
    assert result["state"]["feedback_ledger"][0]["text"] == "做一个选股工具"
    assert finance_cc.calls[0]["thread_id"] == 17


def test_each_new_start_create_allocates_a_distinct_system_flow_id(
    monkeypatch,
) -> None:
    monkeypatch.setenv("FINANCE_CC_TOOL_DEVELOPMENT_ENABLED", "1")
    finance_cc = _FinanceCcStub(
        {
            "ok": True,
            "result": "需求已整理。",
            "artifact_updates": [{
                "artifact_type": "requirement",
                "payload": {
                    "requirement_brief": "创建一个股票分析工具。",
                    "questions": [],
                },
            }],
            "interaction_requests": [],
        }
    )
    service = CustomToolAgentService(
        use_codex=False,
        finance_cc_service=finance_cc,
    )

    first = service.start_create(
        "创建第一个股票分析工具",
        owner_id="owner-1",
        thread_id=17,
    )
    second = service.start_create(
        "创建第二个股票分析工具",
        owner_id="owner-1",
        thread_id=17,
    )

    first_flow = first["state"]["custom_tool_flow_id"]
    second_flow = second["state"]["custom_tool_flow_id"]
    assert first_flow
    assert second_flow
    assert first_flow != second_flow
    assert (
        finance_cc.calls[0]["context"]["custom_tool_flow_id"]
        == first_flow
    )
    assert (
        finance_cc.calls[1]["context"]["custom_tool_flow_id"]
        == second_flow
    )


def test_design_only_provider_output_falls_back_to_user_requirement_review(
    monkeypatch,
) -> None:
    monkeypatch.setenv("FINANCE_CC_TOOL_DEVELOPMENT_ENABLED", "1")
    finance_cc = _FinanceCcStub(
        {
            "ok": True,
            "result": "设计已经形成。",
            "artifact_updates": [{
                "artifact_type": "design",
                "payload": {"design": "## 方案\n直接开始实现。"},
            }],
            "interaction_requests": [],
        }
    )
    service = CustomToolAgentService(
        use_codex=False,
        finance_cc_service=finance_cc,
    )

    result = service.start_create(
        "创建一个识别金叉的工具",
        owner_id="owner-1",
        thread_id=17,
    )

    assert result["design_status"] == "clarification"
    assert result["understanding"]["requirement_brief"] == "创建一个识别金叉的工具"
    assert result["requirement_artifact"]["requirement_revision"] == 1
    assert "design_contract" not in result["state"]
    blocks = LlmStreamBlockBuilder(run_id="design_only_fallback").final_to_blocks(
        {**result, "status": result["design_status"]},
        stage="requirement",
    )
    assert [block["block_id"] for block in blocks] == [
        "requirement_final_summary",
        "requirement_review",
    ]
    assert (
        blocks[1]["data"]["actions"][0]["expected_revision"]
        == 1
    )


def test_text_only_first_turn_still_creates_requirement_review(
    monkeypatch,
) -> None:
    monkeypatch.setenv("FINANCE_CC_TOOL_DEVELOPMENT_ENABLED", "1")
    finance_cc = _FinanceCcStub(
        {
            "ok": True,
            "result": "我先理解一下你的目标。",
            "artifact_updates": [],
            "interaction_requests": [],
        }
    )
    service = CustomToolAgentService(
        use_codex=False,
        finance_cc_service=finance_cc,
    )

    result = service.start_create(
        "创建一个判断市场趋势的工具",
        owner_id="owner-1",
        thread_id=17,
    )

    assert result["design_status"] == "clarification"
    assert (
        result["understanding"]["requirement_brief"]
        == "创建一个判断市场趋势的工具"
    )
    assert result["state"]["requirement_revision"] == 1
    assert result["state"].get("confirmed_requirement_revision", 0) == 0
    blocks = LlmStreamBlockBuilder(run_id="text_only_fallback").final_to_blocks(
        {**result, "status": result["design_status"]},
        stage="requirement",
    )
    assert blocks[-1]["block_id"] == "requirement_review"
    assert blocks[-1]["data"]["actions"][0]["expected_revision"] == 1


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
        state=_confirmed_requirement_state(
            "扫描近期金叉",
            requirement_text="扫描近期金叉",
        ),
        owner_id="owner-1",
        thread_id=18,
        turn_id=24,
    )

    assert result["design_status"] == "review"
    assert result["state"]["tool_name"] == "golden_cross_scan"
    assert result["design"]["mermaid"] == "flowchart TD"
    assert result["design_artifact"]["design_revision"] == 1
    assert result["design_artifact"]["design_artifact_id"]


def test_confirmed_design_without_flow_stays_a_non_reviewable_draft(monkeypatch) -> None:
    monkeypatch.setenv("FINANCE_CC_TOOL_DEVELOPMENT_ENABLED", "1")
    finance_cc = _FinanceCcStub(
        {
            "ok": True,
            "result": "设计已经形成。",
            "artifact_updates": [
                {
                    "artifact_type": "design",
                    "payload": {
                        "design": "## 流程\n读取行情，涨幅超过 5% 时返回命中。",
                    },
                }
            ],
            "interaction_requests": [],
        }
    )
    service = CustomToolAgentService(use_codex=False, finance_cc_service=finance_cc)

    result = service.handle_turn(
        "形成设计",
        state=_confirmed_requirement_state("判断指定股票涨幅是否超过 5%。"),
        owner_id="owner-1",
        thread_id=18,
        turn_id=25,
    )

    assert result["design_status"] == "design_draft"
    assert result["design"] == {}
    assert result["design_artifact"] == {}
    assert "流程图尚未完成" in result["message"]
    assert "design_contract" in result["state"]
    assert "mermaid" not in result["state"]["design_contract"]


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
        state=_confirmed_requirement_state(
            "判断指定股票近期是否出现金叉"
        ),
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


def test_finance_cc_requirement_and_design_in_same_turn_stops_for_confirmation(monkeypatch) -> None:
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

    assert result["design_status"] == "clarification"
    assert result["questions"] == []
    assert result["state"]["requirement_brief"].startswith("判断指定A股")
    assert result["state"]["requirement_revision"] == 1
    assert result["state"].get("confirmed_requirement_revision", 0) == 0
    assert "design_contract" not in result["state"]
    assert result["design"] == {}
    assert result["design_artifact"] == {}
    assert result["requirement_artifact"]["requirement_revision"] == 1
    assert "请先确认需求" in result["message"]
    assert "status" not in result["state"]


def test_requirement_submission_confirms_resulting_revision_before_design(
    monkeypatch,
) -> None:
    monkeypatch.setenv("FINANCE_CC_TOOL_DEVELOPMENT_ENABLED", "1")
    finance_cc = _FinanceCcStub(
        {
            "ok": True,
            "result": "需求已按回答补全，设计方案已经形成。",
            "artifact_updates": [
                {
                    "artifact_type": "requirement",
                    "payload": {
                        "requirement_brief": "扫描指定A股最近60日的MA5上穿MA20信号。",
                        "questions": [],
                    },
                },
                {
                    "artifact_type": "design",
                    "payload": {
                        "design": "## 流程\n读取60日日线并识别金叉。",
                    },
                },
                {
                    "artifact_type": "flow",
                    "payload": {
                        "mermaid": "flowchart TD\nA[读取60日日线] --> B{MA5上穿MA20?}\nB -- 是 --> C[返回金叉日期]\nB -- 否 --> D[返回未命中]",
                    },
                },
            ],
            "interaction_requests": [],
        }
    )
    service = CustomToolAgentService(
        use_codex=False,
        finance_cc_service=finance_cc,
    )
    state = _confirmed_requirement_state(
        "扫描指定A股近期的MA5上穿MA20信号。"
    )
    state.pop("confirmed_requirement_revision")

    result = service.handle_turn(
        "窗口使用最近60个交易日。",
        state=state,
        ui_action={
            "action_id": "custom_tool.submit_clarification",
            "expected_revision": 1,
        },
        owner_id="owner-1",
        thread_id=20,
        turn_id=27,
    )

    assert result["design_status"] == "review"
    assert result["state"]["requirement_revision"] == 2
    assert result["state"]["confirmed_requirement_revision"] == 2
    assert "读取60日日线" in result["state"]["design_contract"]["document"]
    assert result["state"]["design_contract"]["mermaid"].startswith("flowchart TD")
    assert (
        finance_cc.calls[0]["context"]["custom_tool_state"][
            "confirmed_requirement_revision"
        ]
        == 1
    )


def test_finance_cc_design_profile_survives_artifact_merge_and_flow(
    monkeypatch,
) -> None:
    monkeypatch.setenv("FINANCE_CC_TOOL_DEVELOPMENT_ENABLED", "1")
    profile = {
        "protocol": "finance_tool_profile.v1",
        "family": "analytics",
        "execution_shape": "aggregate_context",
        "output_semantic": "assessment",
        "summary": "计算并解释大盘强度。",
    }
    finance_cc = _FinanceCcStub(
        {
            "ok": True,
            "result": "分析工具设计完成。",
            "artifact_updates": [
                {
                    "artifact_type": "design",
                    "payload": {
                        "design": "## 大盘强度\n聚合市场数据并输出强度判断。",
                        "finance_tool_profile": profile,
                    },
                },
                {
                    "artifact_type": "flow",
                    "payload": {
                        "mermaid": "flowchart TD\nA[聚合市场数据] --> B[输出强度判断]",
                    },
                },
            ],
            "interaction_requests": [],
        }
    )
    service = CustomToolAgentService(use_codex=False, finance_cc_service=finance_cc)

    result = service.handle_turn(
        "按已确认需求形成设计",
        state=_confirmed_requirement_state("计算并解释大盘强度。"),
        owner_id="owner-1",
        thread_id=22,
        turn_id=28,
    )

    assert result["design_status"] == "review"
    assert result["state"]["design_contract"]["finance_tool_profile"] == profile
    assert result["design"]["finance_tool_profile"] == profile
    assert result["design"]["mermaid"].startswith("flowchart TD")


def test_stale_requirement_submission_is_rejected_before_finance_cc(
    monkeypatch,
) -> None:
    monkeypatch.setenv("FINANCE_CC_TOOL_DEVELOPMENT_ENABLED", "1")
    finance_cc = _FinanceCcStub({"ok": True, "artifact_updates": []})
    service = CustomToolAgentService(
        use_codex=False,
        finance_cc_service=finance_cc,
    )
    state = _confirmed_requirement_state("扫描近期金叉。")
    state.pop("confirmed_requirement_revision")

    try:
        service.handle_turn(
            "确认需求",
            state=state,
            ui_action={
                "action_id": "custom_tool.submit_clarification",
                "expected_revision": 0,
            },
            owner_id="owner-1",
            thread_id=20,
            turn_id=28,
        )
    except Exception as exc:
        assert "requirement revision changed" in str(exc)
    else:
        raise AssertionError("stale requirement confirmation must fail")

    assert finance_cc.calls == []


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

    assert result["diagnostic_warning"] == "max turns reached"
    assert result["state"]["requirement_brief"] == "从A股中筛选股票并返回股票列表。"
    assert "requirement" in result["message"]
    assert "notice" not in result["state"]
    assert "questions" not in result["state"]


def test_finance_cc_timeout_keeps_requirement_and_uses_tool_specific_recovery(monkeypatch) -> None:
    monkeypatch.setenv("FINANCE_CC_TOOL_DEVELOPMENT_ENABLED", "1")
    finance_cc = _FinanceCcStub(
        {
            "ok": False,
            "error": "Finance CC first response timed out after 45s",
            "failure_kind": "first_response_timeout",
            "result": "",
            "artifact_updates": [],
            "interaction_requests": [],
        }
    )
    service = CustomToolAgentService(use_codex=False, finance_cc_service=finance_cc)

    failed = service.handle_turn(
        "创建一个识别MA5上穿MA20的工具",
        state={},
        owner_id="owner-1",
        thread_id=29,
        turn_id=35,
    )

    assert "已进入自定义工具创建" in failed["message"]
    assert "需求已经保留" in failed["message"]
    assert "金融问答" not in failed["message"]
    assert failed["state"]["requirement_text"] == "创建一个识别MA5上穿MA20的工具"
    assert len(failed["state"]["feedback_ledger"]) == 1

    finance_cc.result = {
        "ok": True,
        "result": "需求已重新整理。",
        "artifact_updates": [{
            "artifact_type": "requirement",
            "payload": {
                "requirement_brief": "识别指定股票的MA5上穿MA20信号。",
                "questions": [],
            },
        }],
        "interaction_requests": [],
    }
    retried = service.handle_turn(
        "重试当前阶段",
        state=failed["state"],
        ui_action={"action_id": "custom_tool.retry_design"},
        owner_id="owner-1",
        thread_id=29,
        turn_id=36,
    )

    assert finance_cc.calls[-1]["user_text"] == "创建一个识别MA5上穿MA20的工具"
    assert len(retried["state"]["feedback_ledger"]) == 1
    assert retried["state"]["requirement_brief"].startswith("识别指定股票")


def test_custom_tool_orchestrator_owns_initial_progress_event(monkeypatch) -> None:
    monkeypatch.setenv("FINANCE_CC_TOOL_DEVELOPMENT_ENABLED", "1")
    finance_cc = _ProgressAwareFinanceCcStub(
        {
            "ok": True,
            "result": "需求已整理。",
            "artifact_updates": [
                {
                    "artifact_type": "requirement",
                    "payload": {
                        "requirement_brief": "查询指定股票的最近完整交易日收盘价。",
                        "questions": [],
                    },
                }
            ],
            "interaction_requests": [],
        }
    )
    service = CustomToolAgentService(use_codex=False, finance_cc_service=finance_cc)
    emitted = []

    service.handle_turn(
        "创建一个收盘价查询工具",
        state={},
        owner_id="owner-1",
        thread_id=30,
        turn_id=37,
        event_sink=emitted.append,
    )

    assert len(emitted) == 1
    assert emitted[0]["metadata"]["progress_id"] == "custom_tool_understanding"
    assert (
        finance_cc.calls[0]["context"]["_initial_progress_emitted"]
        is True
    )


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
