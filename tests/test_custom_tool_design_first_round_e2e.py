import json
from pathlib import Path

from src.services.custom_tool_service import CustomToolAgentService
from src.services.llm_stream_block_service import LlmStreamBlockBuilder
from src.web import flask_app as web


SKILL_DIR = Path("src/skills/financial-tool-requirement-design-v3")


class _RecordingDesigner:
    def __init__(self) -> None:
        self.calls = []

    def design(self, requirement_text, context=None, event_sink=None):
        self.calls.append({
            "requirement_text": requirement_text,
            "context": dict(context or {}),
        })
        if event_sink:
            event_sink({
                "source": "harness",
                "type": "stage_start",
                "content": "design started",
                "metadata": {"stage": "design"},
            })
            event_sink({
                "source": "harness",
                "type": "turn_started",
                "content": "turn started",
                "metadata": {"stage": "design"},
            })
        return json.loads((SKILL_DIR / "assets/sample-review.json").read_text(encoding="utf-8"))


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


def test_create_first_round_runs_command_to_persisted_surface_without_inheriting_old_state(monkeypatch) -> None:
    designer = _RecordingDesigner()
    agent = CustomToolAgentService(designer=designer, use_codex=False)
    conversations = _ConversationRuntimeStub()
    emitted = []

    monkeypatch.setattr(web, "custom_tool_agent_service", agent)
    monkeypatch.setattr(web, "application_runtime_service", _ApplicationRuntimeStub())
    monkeypatch.setattr(web, "runtime_conversation_service", conversations)

    web._run_custom_tool_stream_payload(
        {
            "run_id": "run_first_round",
            "text": "/custom_tool create 创建一个放量突破识别工具",
            "application_name": "investment_workbench",
            "guest_identity": {"user_id": "user_a"},
            "thread_id": 101,
        },
        emit=emitted.append,
    )

    assert designer.calls == [{
        "requirement_text": "创建一个放量突破识别工具",
        "context": {
            "owner_id": "user_a",
            "state": {},
            "design_scenario": "create_first_round",
            "design_round": 1,
            "design_policy": {
                "scope_mode": "minimum_viable_core",
                "progressive_expansion": True,
                "implicit_adjacent_features": False,
                "first_round_budget": {
                    "required_questions": 3,
                    "modules": 3,
                    "rules": 5,
                    "outputs": 5,
                    "exceptions": 3,
                    "acceptance": 5,
                    "flow_steps": 7,
                },
            },
        },
    }]
    assert len(conversations.completed) == 1
    output_payload = conversations.completed[0]["output_payload"]
    assert output_payload["state"]["status"] == "awaiting_design_confirmation"
    assert output_payload["state"]["design_round"] == 1
    assert output_payload["state"]["design_artifact_id"] != "old_artifact"
    assert output_payload["event_summary"] == {
        "total": 2,
        "persisted": 2,
        "by_type": {
            "harness:stage_start": 1,
            "harness:turn_started": 1,
        },
    }
    artifact = next(block for block in output_payload["surface_blocks"] if block["block_type"] == "artifact")
    assert artifact["data"]["artifact_type"] == "finance.tool_spec"
    assert artifact["data"]["design_context"] == {
        "scenario": "create_first_round",
        "round": 1,
        "is_first_round": True,
    }
    interaction = next(block for block in output_payload["surface_blocks"] if block["block_type"] == "interaction")
    assert interaction["data"]["interaction_id"] == "custom_tool.design_review"
    assert interaction["data"]["subject_revision"] == 1
    first_artifact_index = next(index for index, event in enumerate(emitted) if event.get("block_type") == "artifact")
    assert any(event.get("block_type") == "status" for event in emitted[:first_artifact_index])
    assert emitted[-1]["event"] == "done"
    assert emitted[-1]["thread_id"] == 101
    assert emitted[-1]["turn_id"] == 202


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


def test_custom_tool_stream_persists_failure_instead_of_leaving_turn_running(monkeypatch) -> None:
    class FailingAgent:
        def start_create(self, *args, **kwargs):
            raise ValueError("invalid value_json for design.rules")

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
    assert conversations.completed[0]["output_payload"]["error"] == "invalid value_json for design.rules"
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
                "gate_passed": True,
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


def test_execution_success_without_full_test_gate_does_not_offer_activation() -> None:
    blocks = web._custom_tool_result_blocks(
        {
            "tool": {
                "manifest": {"tool_name": "ct_demo", "display_name": "演示工具", "status": "draft", "current_revision": 1},
            },
            "test_result": {
                "ok": True,
                "gate_passed": False,
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
