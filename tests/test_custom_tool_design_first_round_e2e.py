import json
from pathlib import Path

from src.services.llm_stream_block_service import LlmStreamBlockBuilder
from src.web import flask_app as web


class _ApplicationRuntimeStub:
    def get_application_context(self, application_name):
        return {
            "application_name": application_name,
            "display_name": "Investment Workbench",
        }


class _ConversationRuntimeStub:
    def __init__(self) -> None:
        self.context = {
            "custom_tool_state": {
                "status": "awaiting_design_confirmation",
                "design_round": 5,
                "design_revision": 4,
                "design_artifact_id": "old_artifact",
            }
        }
        self.completed = []
        self.updated = []

    def ensure_thread(self, **kwargs):
        return 101

    def get_thread_context(self, **kwargs):
        return dict(self.context)

    def get_context_window(self, **kwargs):
        return []

    def create_turn(self, **kwargs):
        return 202

    def update_thread_context(self, **kwargs):
        self.updated.append(kwargs)
        patch = kwargs.get("patch") or {}
        if "custom_tool_state" in patch:
            self.context["custom_tool_state"] = patch["custom_tool_state"]

    def complete_turn(self, **kwargs):
        self.completed.append(kwargs)


class _NormalFinanceDispatchPlannerStub:
    def plan_free_chat(self, **kwargs):
        return {
            "entry": "tool_plan_run",
            "turn_mode": "normal_qa",
            "selected_agent": "investment_analyst",
            "thread_context_patch_preview": {},
        }

    def plan_turn(self, **kwargs):
        return self.plan_free_chat(**kwargs)


class _ToolDevelopmentDispatchPlannerStub:
    def plan_free_chat(self, **kwargs):
        return {
            "entry": "custom_tool_flow",
            "turn_mode": "tool_development",
            "selected_agent": "investment_analyst",
            "thread_context_patch_preview": {},
        }

    def plan_turn(self, **kwargs):
        return self.plan_free_chat(**kwargs)


def test_natural_language_tool_development_starts_a_first_round_when_no_tool_is_active(monkeypatch) -> None:
    class _FirstRoundAgent:
        def __init__(self) -> None:
            self.calls = []

        def start_create(self, text, **kwargs):
            self.calls.append({"text": text, **kwargs})
            return {
                "message": "我先为这个选股工具梳理设计方案。",
                "state": {"status": "awaiting_design_confirmation", "design_round": 1},
                "surface_blocks": [],
            }

    conversations = _ConversationRuntimeStub()
    conversations.context = {}
    agent = _FirstRoundAgent()
    emitted = []

    monkeypatch.setattr(web, "application_runtime_service", _ApplicationRuntimeStub())
    monkeypatch.setattr(web, "runtime_conversation_service", conversations)
    monkeypatch.setattr(web, "assistant_dispatch_planner", _ToolDevelopmentDispatchPlannerStub())
    monkeypatch.setattr(web, "custom_tool_agent_service", agent)

    web._run_custom_tool_stream_payload(
        {
            "run_id": "natural_language_first_round",
            "text": "帮我创建一个选股工具，逻辑是最近20日涨幅超过10%且成交量放大。",
            "application_name": "investment_workbench",
            "guest_identity": {"user_id": "user_a"},
            "thread_id": 101,
        },
        emit=emitted.append,
    )

    assert agent.calls == [{
        "text": "帮我创建一个选股工具，逻辑是最近20日涨幅超过10%且成交量放大。",
        "owner_id": "user_a",
        "thread_id": 101,
        "turn_id": 202,
        "event_sink": agent.calls[0]["event_sink"],
    }]
    assert conversations.completed[0]["output_payload"]["state"]["design_round"] == 1
    assert conversations.context["custom_tool_state"]["status"] == "awaiting_design_confirmation"


def test_active_custom_tool_stream_can_route_a_new_finance_question_outside_tool_flow(monkeypatch) -> None:
    conversations = _ConversationRuntimeStub()
    emitted = []

    monkeypatch.setattr(web, "application_runtime_service", _ApplicationRuntimeStub())
    monkeypatch.setattr(web, "runtime_conversation_service", conversations)
    monkeypatch.setattr(web, "assistant_dispatch_planner", _NormalFinanceDispatchPlannerStub())
    monkeypatch.setattr(
        web,
        "_build_chat_dispatch_payload",
        lambda *args, **kwargs: {
            "mode": "finance_quote",
            "message": "宁德时代当前行情已返回。",
            "thread_context_patch": {},
        },
    )

    web._run_custom_tool_stream_payload(
        {
            "run_id": "route_outside_custom_tool",
            "text": "宁德时代现在股价是多少？",
            "application_name": "investment_workbench",
            "guest_identity": {"user_id": "user_a"},
            "thread_id": 101,
        },
        emit=emitted.append,
    )

    output_payload = conversations.completed[0]["output_payload"]
    assert output_payload["mode"] == "finance_quote"
    assert output_payload["dispatch_plan"]["turn_mode"] == "normal_qa"
    assert "custom_tool_state" not in output_payload["thread_context_patch"]
    assert conversations.context["custom_tool_state"]["status"] == "awaiting_design_confirmation"


def test_active_tool_can_switch_to_finance_and_then_resume_design(monkeypatch) -> None:
    class SequencePlanner:
        def plan_turn(self, *, text, **kwargs):
            if text == "今天茅台的收盘价是多少？":
                return {
                    "entry": "tool_plan_run",
                    "turn_mode": "normal_qa",
                    "selected_agent": "investment_analyst",
                    "thread_context_patch_preview": {},
                }
            return {
                "entry": "custom_tool_flow",
                "turn_mode": "tool_development",
                "selected_agent": "investment_analyst",
                "thread_context_patch_preview": {},
            }

    class RevisionAgent:
        def __init__(self):
            self.calls = []

        def handle_turn(self, text, **kwargs):
            self.calls.append({"text": text, **kwargs})
            state = {**dict(kwargs["state"]), "design_revision": 5}
            return {
                "message": "已将计算周期调整为10个交易日。",
                "state": state,
                "thread_context_patch": {"custom_tool_state": state},
            }

    conversations = _ConversationRuntimeStub()
    agent = RevisionAgent()
    emitted = []
    monkeypatch.setattr(web, "application_runtime_service", _ApplicationRuntimeStub())
    monkeypatch.setattr(web, "runtime_conversation_service", conversations)
    monkeypatch.setattr(web, "assistant_dispatch_planner", SequencePlanner())
    monkeypatch.setattr(web, "custom_tool_agent_service", agent)
    monkeypatch.setattr(
        web,
        "_build_chat_dispatch_payload",
        lambda *args, **kwargs: {
            "mode": "finance_quote",
            "message": "贵州茅台今日收盘价已返回。",
            "thread_context_patch": {},
        },
    )

    web._run_custom_tool_stream_payload(
        {
            "run_id": "switch_to_finance",
            "text": "今天茅台的收盘价是多少？",
            "application_name": "investment_workbench",
            "guest_identity": {"user_id": "user_a"},
            "thread_id": 101,
        },
        emit=emitted.append,
    )

    assert conversations.context["custom_tool_state"]["status"] == "awaiting_design_confirmation"
    assert conversations.context["custom_tool_state"]["design_revision"] == 4

    web._run_custom_tool_stream_payload(
        {
            "run_id": "resume_tool_design",
            "text": "回到刚才的工具，把计算周期改成10个交易日",
            "application_name": "investment_workbench",
            "guest_identity": {"user_id": "user_a"},
            "thread_id": 101,
        },
        emit=emitted.append,
    )

    assert agent.calls[0]["state"]["status"] == "awaiting_design_confirmation"
    assert conversations.context["custom_tool_state"]["design_revision"] == 5
    assert conversations.completed[0]["output_payload"]["mode"] == "finance_quote"
    assert conversations.completed[1]["output_payload"]["message"] == "已将计算周期调整为10个交易日。"


def test_active_custom_tool_stream_renders_view_answer_without_design_or_coding(monkeypatch) -> None:
    class ViewAgent:
        def __init__(self):
            self.calls = []

        def handle_turn(self, text, **kwargs):
            self.calls.append({"text": text, **kwargs})
            state = dict(kwargs.get("state") or {})
            answer = "```python\ndef run(inputs):\n    return inputs\n```"
            return {
                "message": answer,
                "view_answer": answer,
                "tool_turn": {"action": "view", "request": text, "change_request": "", "ok": True},
                "state": state,
                "thread_context_patch": {"custom_tool_state": state},
            }

    agent = ViewAgent()
    conversations = _ConversationRuntimeStub()
    conversations.context["custom_tool_state"].update({
        "status": "draft_ready",
        "tool_name": "ct_demo",
        "implementation_revision": 2,
    })
    emitted = []
    monkeypatch.setattr(web, "custom_tool_agent_service", agent)
    monkeypatch.setattr(web, "application_runtime_service", _ApplicationRuntimeStub())
    monkeypatch.setattr(web, "runtime_conversation_service", conversations)
    monkeypatch.setattr(web, "assistant_dispatch_planner", _ToolDevelopmentDispatchPlannerStub())

    web._run_custom_tool_stream_payload(
        {
            "run_id": "view_custom_tool_answer",
            "text": "给我看一下这个工具的实际核心代码",
            "application_name": "investment_workbench",
            "guest_identity": {"user_id": "user_a"},
            "thread_id": 101,
        },
        emit=emitted.append,
    )

    output_payload = conversations.completed[0]["output_payload"]
    assert agent.calls[0]["text"] == "给我看一下这个工具的实际核心代码"
    assert output_payload["tool_turn"]["action"] == "view"
    assert output_payload["state"]["implementation_revision"] == 2
    assert [block["block_type"] for block in output_payload["surface_blocks"]] == ["markdown"]
    assert output_payload["surface_blocks"][0]["data"]["state_changed"] is False
    assert output_payload["surface_blocks"][0]["stage"] == "view"
    assert emitted[-1]["event"] == "done"


def test_view_assets_render_saved_code_and_flow_with_specialized_blocks() -> None:
    blocks = web._custom_tool_result_blocks(
        {
            "message": "已读取当前工具资产。",
            "view_answer": "已读取当前工具资产。",
            "tool_turn": {"action": "view"},
            "view_assets": [
                {
                    "type": "code",
                    "payload": {
                        "modules": [{
                            "module_id": "main",
                            "language": "python",
                            "source_code": "def run(inputs):\n    return inputs\n",
                        }],
                    },
                },
                {
                    "type": "flow",
                    "payload": {
                        "steps": [{"id": "read", "name": "读取数据"}, {"id": "result", "name": "返回结果"}],
                        "links": [{"from": "read", "to": "result"}],
                    },
                },
            ],
        },
        LlmStreamBlockBuilder(run_id="view_assets"),
    )

    assert [block["block_type"] for block in blocks] == ["markdown", "code", "flowchart"]
    assert blocks[1]["data"]["files"][0]["content"].startswith("def run")
    assert blocks[2]["data"]["nodes"][0]["label"] == "读取数据"


def test_requirement_confirmation_renders_before_design_exists() -> None:
    blocks = web._custom_tool_result_blocks(
        {
            "message": "我理解为识别最近30个交易日内的金叉，按这个实现可以吗？",
            "design_status": "clarification",
            "understanding": {
                "goal": "判断单只股票最近30个交易日内是否出现金叉",
            },
            "questions": [],
            "design": {},
            "tool_turn": {"action": "design"},
        },
        LlmStreamBlockBuilder(run_id="requirement_confirmation"),
    )

    assert [block["block_type"] for block in blocks] == ["narrative"]
    assert "按这个实现可以吗" in blocks[0]["content"]


def test_design_then_view_blocks_follow_declared_action_order() -> None:
    blocks = web._custom_tool_result_blocks(
        {
            "message": "设计已更新。",
            "design_status": "review",
            "design": {
                "tool_name": "demo",
                "display_name": "演示工具",
                "description": "演示多步输出。",
                "inputs": [],
                "outputs": [],
                "modules": [],
                "rules": [],
                "data_requirements": [],
                "exceptions": [],
                "acceptance": [],
                "flow": {"steps": [], "links": []},
            },
            "tool_turn": {"action": "design", "actions": ["design", "view"]},
            "view_answer": "已读取当前工具资产。",
            "view_assets": [{
                "type": "flow",
                "payload": {"steps": [{"id": "done", "name": "新流程"}], "links": []},
            }],
        },
        LlmStreamBlockBuilder(run_id="design_then_view"),
    )

    stages = [block["stage"] for block in blocks]
    assert stages[-2:] == ["view", "view"]
    assert "design" in stages[:-2]


def test_structured_clarification_is_submitted_as_text_with_ui_context(monkeypatch) -> None:
    class FeedbackAgent:
        def __init__(self):
            self.calls = []

        def handle_turn(self, text, **kwargs):
            self.calls.append({"text": text, **kwargs})
            state = {**dict(kwargs["state"]), "status": "awaiting_design_confirmation"}
            return {
                "message": "设计已按确认信息更新。",
                "state": state,
                "thread_context_patch": {"custom_tool_state": state},
            }

    agent = FeedbackAgent()
    conversations = _ConversationRuntimeStub()
    conversations.context["custom_tool_state"].update({"status": "collect_requirement", "design_revision": 4})
    emitted = []
    monkeypatch.setattr(web, "custom_tool_agent_service", agent)
    monkeypatch.setattr(web, "application_runtime_service", _ApplicationRuntimeStub())
    monkeypatch.setattr(web, "runtime_conversation_service", conversations)
    monkeypatch.setattr(web, "assistant_dispatch_planner", _ToolDevelopmentDispatchPlannerStub())

    web._run_custom_tool_stream_payload(
        {
            "run_id": "submit_design_clarification",
            "text": "",
            "interaction_response": {
                "interaction_id": "custom_tool.requirement_clarification",
                "action_id": "custom_tool.submit_clarification",
                "action": "submit",
                "expected_revision": 4,
                "answers": [
                    {"question": "金额单位是什么？", "answer": "系统依据数据接口口径处理"},
                    {"question": "计算周期是什么？", "answer": "最近 20 个交易日"},
                ],
            },
            "application_name": "investment_workbench",
            "guest_identity": {"user_id": "user_a"},
            "thread_id": 101,
        },
        emit=emitted.append,
    )

    assert agent.calls[0]["text"] == "关于「金额单位是什么？」，我的回答是：系统依据数据接口口径处理。\n关于「计算周期是什么？」，我的回答是：最近 20 个交易日。"
    assert agent.calls[0]["ui_action"]["action_id"] == "custom_tool.submit_clarification"
    assert conversations.completed[0]["output_payload"]["state"]["status"] == "awaiting_design_confirmation"


def test_confirmation_button_with_text_uses_semantic_route_instead_of_action_shortcut(monkeypatch) -> None:
    class SemanticAgent:
        def __init__(self):
            self.calls = []

        def handle_turn(self, text, **kwargs):
            self.calls.append({"text": text, **kwargs})
            state = {**dict(kwargs["state"]), "design_revision": 5}
            return {
                "message": "设计窗口已调整为 60 个交易日，尚未进入实现。",
                "tool_turn": {"version": "v1", "action": "design", "request": text, "change_request": text, "ok": True},
                "state": state,
                "thread_context_patch": {"custom_tool_state": state},
            }

        def continue_flow_action(self, *args, **kwargs):
            raise AssertionError("button with text must not execute the action shortcut")

    agent = SemanticAgent()
    conversations = _ConversationRuntimeStub()
    monkeypatch.setattr(web, "custom_tool_agent_service", agent)
    monkeypatch.setattr(web, "application_runtime_service", _ApplicationRuntimeStub())
    monkeypatch.setattr(web, "runtime_conversation_service", conversations)
    monkeypatch.setattr(web, "assistant_dispatch_planner", _ToolDevelopmentDispatchPlannerStub())

    web._run_custom_tool_stream_payload(
        {
            "run_id": "confirm_with_text",
            "text": "先不要实现，把窗口改成 60 个交易日",
            "interaction_response": {
                "interaction_id": "custom_tool.design_review",
                "action_id": "custom_tool.confirm_design",
                "action": "accept",
                "expected_revision": 4,
            },
            "application_name": "investment_workbench",
            "guest_identity": {"user_id": "user_a"},
            "thread_id": 101,
        },
        emit=lambda event: None,
    )

    assert agent.calls[0]["text"] == "先不要实现，把窗口改成 60 个交易日"
    assert agent.calls[0]["ui_action"] == {
        "interaction_id": "custom_tool.design_review",
        "action_id": "custom_tool.confirm_design",
        "action": "accept",
        "expected_revision": 4,
    }
    output = conversations.completed[0]["output_payload"]
    assert output["tool_turn"]["action"] == "design"
    assert output["state"]["design_revision"] == 5


def test_confirmation_button_without_text_enters_coding_directly(monkeypatch) -> None:
    class ShortcutPlanner(_ToolDevelopmentDispatchPlannerStub):
        def plan_turn(self, **kwargs):
            return {
                **super().plan_turn(**kwargs),
                "shortcut": {"handler": "custom_tool.action"},
            }

    class DirectAgent:
        finance_cc_enabled = True

        def __init__(self):
            self.actions = []

        def continue_flow_action(self, action_id, **kwargs):
            self.actions.append({"action_id": action_id, **kwargs})
            return {
                "message": "已直接进入 Coding。",
                "state": dict(kwargs["state"]),
                "thread_context_patch": {"custom_tool_state": dict(kwargs["state"])},
            }

        def handle_turn(self, *args, **kwargs):
            raise AssertionError("pure confirmation must not start Finance CC")

    agent = DirectAgent()
    conversations = _ConversationRuntimeStub()
    monkeypatch.setattr(web, "custom_tool_agent_service", agent)
    monkeypatch.setattr(web, "application_runtime_service", _ApplicationRuntimeStub())
    monkeypatch.setattr(web, "runtime_conversation_service", conversations)
    monkeypatch.setattr(web, "assistant_dispatch_planner", ShortcutPlanner())

    web._run_custom_tool_stream_payload(
        {
            "run_id": "confirm_design_direct",
            "text": "",
            "interaction_response": {
                "interaction_id": "custom_tool.design_review",
                "action_id": "custom_tool.confirm_design",
                "action": "accept",
                "expected_revision": 4,
            },
            "application_name": "investment_workbench",
            "guest_identity": {"user_id": "user_a"},
            "thread_id": 101,
        },
        emit=lambda event: None,
    )

    assert agent.actions[0]["action_id"] == "custom_tool.confirm_design"
    assert agent.actions[0]["expected_revision"] == 4


def test_structured_coding_feedback_is_submitted_as_text_with_ui_context(monkeypatch) -> None:
    class FeedbackAgent:
        def __init__(self):
            self.calls = []

        def handle_turn(self, text, **kwargs):
            self.calls.append({"text": text, **kwargs})
            state = dict(kwargs["state"])
            return {"message": "实现已更新。", "state": state, "thread_context_patch": {"custom_tool_state": state}}

    agent = FeedbackAgent()
    conversations = _ConversationRuntimeStub()
    conversations.context["custom_tool_state"] = {
        "status": "draft_ready",
        "tool_name": "ct_demo",
        "implementation_revision": 2,
    }
    monkeypatch.setattr(web, "custom_tool_agent_service", agent)
    monkeypatch.setattr(web, "application_runtime_service", _ApplicationRuntimeStub())
    monkeypatch.setattr(web, "runtime_conversation_service", conversations)
    monkeypatch.setattr(web, "assistant_dispatch_planner", _ToolDevelopmentDispatchPlannerStub())

    web._run_custom_tool_stream_payload(
        {
            "run_id": "submit_coding_feedback",
            "text": "",
            "interaction_response": {
                "interaction_id": "custom_tool.coding_review",
                "action_id": "custom_tool.revise_implementation",
                "action": "edit",
                "expected_revision": 2,
                "feedback_text": "测试结果中补充核心中间指标",
            },
            "application_name": "investment_workbench",
            "guest_identity": {"user_id": "user_a"},
            "thread_id": 101,
        },
        emit=lambda event: None,
    )

    assert agent.calls[0]["text"] == "测试结果中补充核心中间指标"
    assert agent.calls[0]["ui_action"]["action_id"] == "custom_tool.revise_implementation"


def test_custom_tool_stream_persists_failure_instead_of_leaving_turn_running(monkeypatch) -> None:
    class FailingAgent:
        def start_create(self, *args, **kwargs):
            raise ValueError("merged design is invalid")

    conversations = _ConversationRuntimeStub()
    emitted = []
    monkeypatch.setattr(web, "custom_tool_agent_service", FailingAgent())
    monkeypatch.setattr(web, "application_runtime_service", _ApplicationRuntimeStub())
    monkeypatch.setattr(web, "runtime_conversation_service", conversations)

    web._run_custom_tool_stream_payload(
        {
            "run_id": "run_failed",
            "text": "/custom_tool create 创建工具",
            "application_name": "investment_workbench",
            "guest_identity": {"user_id": "user_a"},
            "thread_id": 101,
        },
        emit=emitted.append,
    )

    assert len(conversations.completed) == 1
    assert conversations.completed[0]["status"] == "failed"
    assert conversations.completed[0]["output_payload"]["error"] == "merged design is invalid"
    assert emitted[-1]["event"] == "error"


def test_conversation_frontend_routes_finance_tool_spec_to_the_design_renderer() -> None:
    template = Path("src/web/templates/conversation_workbench.html").read_text(encoding="utf-8")

    assert 'data.artifact_type === "finance.tool_spec"' in template
    assert 'create_first_round: "新建工具 · 首轮设计"' in template
    assert 'data.artifact_type === "finance.custom_tool_spec"' not in template


def test_conversation_frontend_uses_one_unified_shell_and_keeps_design_inside_messages() -> None:
    template = Path("src/web/templates/conversation_workbench.html").read_text(encoding="utf-8")

    assert '<div class="app">' in template
    assert 'id="historyPanel"' in template
    assert 'id="messages"' in template
    assert 'id="harnessPanel"' in template
    assert 'id="harnessContent"' in template
    assert 'id="inputBox"' in template
    assert '所有场景共用同一工作台；差异只体现在回答卡片与交互控件。' in template
    assert '${agentRun}' in template
    assert 'artifacts.map((block) => renderStreamBlock(testEvidenceBlock(block, resultPayload)))' in template
    assert 'const mermaidScriptUrl =' in template
    assert 'function buildMermaidFlow(flow)' in template
    assert 'securityLevel: "strict"' in template
    assert 'renderMermaidDiagrams(root);' in template
    assert 'data.role === "live_progress"' in template
    assert 'block?.data?.role !== "live_progress" || block?.block_id === payload.block_id' in template
    assert 'function renderTestCase(test)' in template
    assert 'function renderTestRows(rows)' in template
    assert 'function renderExecutionLogs(logs)' in template
    assert 'function testEvidenceBlock(block, resultPayload)' in template
    assert '实际结果' in template
    assert '核心计算过程' in template
    assert 'class="agent-live-workflow"' in template
    assert '<h3>阶段时间线</h3>' in template
    assert 'block?.data?.role !== "live_progress"' in template
    assert 'id="workspaceContent"' not in template


def test_coding_surface_uses_modules_test_and_activation_without_file_concepts() -> None:
    blocks = web._custom_tool_result_blocks(
        {
            "tool": {
                "manifest": {
                    "tool_name": "ct_demo",
                    "display_name": "演示工具",
                    "description": "动态工具",
                    "status": "draft",
                    "current_revision": 2,
                },
                "storage": {"kind": "database_code_module", "artifact_id": 10, "revision_id": 20},
                "modules": [{"module_id": "main", "role": "动态入口", "entrypoint": "run"}],
            },
            "test_result": {
                "ok": True,
                "execution_ok": True,
                "summary": "1 / 1 项运行测试通过",
                "cases": [{
                    "test_id": "sample_smoke",
                    "status": "passed",
                    "purpose": "动态加载测试",
                    "input": {"stock_codes": ["600519.SH"], "lookback_trading_days": 5},
                    "expected": {"business_result": "no top-level error"},
                    "actual": {
                        "results": [{
                            "code": "600519.SH",
                            "name": "贵州茅台",
                            "status": "success",
                            "period_return": 0.0186,
                            "momentum": 80.83,
                        }],
                        "success_count": 1,
                        "failed_count": 0,
                    },
                    "logs": [
                        {
                            "level": "info",
                            "message": "行情准备完成",
                            "data": {"stock_count": 1, "trading_days": 5},
                        },
                        {
                            "level": "debug",
                            "message": "动量计算",
                            "data": {
                                "start_close": 1188.8,
                                "current_price": 1210.99,
                                "period_return": 0.01866588,
                                "average_amount_10k": 433043.03,
                                "momentum": 80.83129886,
                            },
                        },
                    ],
                }],
            },
        },
        LlmStreamBlockBuilder(run_id="coding_surface"),
    )

    assert [block["block_type"] for block in blocks] == ["artifact", "assessment", "interaction"]
    artifact = blocks[0]
    assert {"label": "加载方式", "value": "数据库动态加载"} in artifact["data"]["items"]
    assert "files" not in artifact["data"]["details"]
    test_case = blocks[1]["data"]["details"]["tests"][0]
    assert test_case["input"]["stock_codes"] == ["600519.SH"]
    assert test_case["actual"]["results"][0]["name"] == "贵州茅台"
    assert test_case["logs"][0]["message"] == "行情准备完成"
    assert test_case["logs"][1]["data"]["momentum"] == 80.83129886
    assert blocks[-1]["data"]["interaction_id"] == "custom_tool.coding_review"
    assert blocks[-1]["data"]["actions"][0]["action_id"] == "custom_tool.activate_draft"


def test_coding_surface_shows_implementation_and_alignment_as_natural_language() -> None:
    blocks = web._custom_tool_result_blocks(
        {
            "implementation_explanation": {
                "summary": "实现了行情读取、信号计算和结果组装三个内部函数。",
            },
            "implementation_review": {
                "summary": "代表性样例运行未报错，需求、设计与代码一致。",
            },
            "tool": {
                "manifest": {
                    "tool_name": "ct_demo",
                    "display_name": "演示工具",
                    "status": "draft",
                    "current_revision": 1,
                },
            },
        },
        LlmStreamBlockBuilder(run_id="coding_narrative"),
    )

    assert [block["block_id"] for block in blocks[:2]] == [
        "custom_tool_implementation_summary",
        "custom_tool_implementation_alignment",
    ]
    assert blocks[0]["block_type"] == "narrative"
    assert "三个内部函数" in blocks[0]["content"]
    assert "需求、设计与代码一致" in blocks[1]["content"]


def test_execution_failure_does_not_offer_activation() -> None:
    blocks = web._custom_tool_result_blocks(
        {
            "tool": {
                "manifest": {"tool_name": "ct_demo", "display_name": "演示工具", "status": "draft", "current_revision": 1},
            },
            "test_result": {
                "ok": False,
                "execution_ok": False,
                "summary": "0 / 1 项运行测试通过",
                "cases": [],
            },
        },
        LlmStreamBlockBuilder(run_id="coding_gate_failed"),
    )

    assert [block["block_type"] for block in blocks] == ["artifact", "assessment"]
    assert blocks[1]["data"]["overall"] == "fail"


def test_coding_failure_surface_keeps_design_and_offers_retry() -> None:
    blocks = web._custom_tool_result_blocks(
        {
            "coding_status": "coding_failed",
            "message": "Coding 服务本次未能生成有效实现，当前设计已保留，可以重试。",
            "coding_error": {
                "code": "coding_schema_invalid",
                "summary": "字段 source 缺少严格 Schema 所需的类型声明。",
            },
        },
        LlmStreamBlockBuilder(run_id="coding_failure"),
    )

    assert [block["block_type"] for block in blocks] == ["assessment", "interaction"]
    assert blocks[0]["data"]["issues"] == ["字段 source 缺少严格 Schema 所需的类型声明。"]
    assert blocks[0]["data"]["details"]["error_code"] == "coding_schema_invalid"
    assert blocks[-1]["data"]["interaction_id"] == "custom_tool.coding_failure"
    assert blocks[-1]["data"]["actions"][0]["action_id"] == "custom_tool.retry_coding"
