from src.web import flask_app as web


class _ApplicationRuntime:
    @staticmethod
    def get_application_context(application_name):
        return {
            "application_name": application_name,
            "display_name": "Investment Workbench",
        }


class _ConversationRuntime:
    def __init__(self) -> None:
        self.context = {}
        self.completed = []

    @staticmethod
    def ensure_thread(**_kwargs):
        return 101

    def get_thread_context(self, **_kwargs):
        return dict(self.context)

    @staticmethod
    def get_context_window(**_kwargs):
        return []

    @staticmethod
    def create_turn(**_kwargs):
        return 202

    def update_thread_context(self, **kwargs):
        self.context.update(kwargs.get("patch") or {})

    def complete_turn(self, **kwargs):
        self.completed.append(kwargs)
        return {"duration_ms": 12}


class _ToolDevelopmentPlanner:
    @staticmethod
    def plan_turn(**_kwargs):
        return {
            "entry": "custom_tool_flow",
            "turn_mode": "tool_development",
            "selected_agent": "investment_analyst",
            "thread_context_patch_preview": {},
        }


class _EditAgent:
    finance_cc_enabled = False

    def __init__(self) -> None:
        self.calls = []

    def start_edit(self, tool_name, requirement_text, **kwargs):
        self.calls.append({
            "tool_name": tool_name,
            "requirement_text": requirement_text,
            **kwargs,
        })
        kwargs["event_sink"]({
            "source": "harness",
            "type": "stage_start",
            "content": "edit plan started",
            "metadata": {"stage": "edit_plan"},
        })
        kwargs["event_sink"]({
            "source": "harness",
            "type": "stage_result",
            "content": "edit plan completed",
            "metadata": {"stage": "edit_plan", "ok": True},
        })
        return {
            "message": "候选版本已生成，当前版本未改变。",
            "coding_status": "implemented",
            "tool": {
                "manifest": {
                    "tool_name": tool_name,
                    "display_name": "合成动量筛选",
                    "description": "筛选五日累计涨幅不低于3.5%的股票。",
                    "status": "draft",
                    "current_revision": 2,
                    "active_revision": 1,
                },
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
            },
            "test_result": {
                "ok": True,
                "execution_ok": True,
                "summary": "2 项构造样本验证通过；未扫描真实市场全量数据。",
                "cases": [],
            },
            "edit_summary": {
                "tool_name": tool_name,
                "display_name": "合成动量筛选",
                "route": "local_patch",
                "impact_summary": "只调整五日涨幅阈值。",
                "base_revision": 1,
                "candidate_revision": 2,
                "affected_assets": ["design", "implementation"],
                "changes": [{
                    "title": "策略设计",
                    "asset": "design",
                    "before": "3%",
                    "after": "3.5%",
                    "reason": "按本轮要求调整。",
                }],
                "verification": {
                    "status": "passed",
                    "summary": "2 项构造样本验证通过；未扫描真实市场全量数据。",
                    "cases": [],
                },
            },
            "state": {
                "tool_name": tool_name,
                "implementation_revision": 2,
                "edit_target": {"base_revision": 1},
            },
        }


def test_edit_command_runs_selected_tool_and_streams_reviewable_candidate(monkeypatch) -> None:
    conversations = _ConversationRuntime()
    agent = _EditAgent()
    emitted = []
    monkeypatch.setattr(web, "application_runtime_service", _ApplicationRuntime())
    monkeypatch.setattr(web, "runtime_conversation_service", conversations)
    monkeypatch.setattr(web, "assistant_dispatch_planner", _ToolDevelopmentPlanner())
    monkeypatch.setattr(web, "custom_tool_agent_service", agent)

    web._run_custom_tool_stream_payload(
        {
            "run_id": "edit_stream",
            "text": "/custom_tool edit ct_synthetic_momentum 把阈值从3%改成3.5%",
            "application_name": "investment_workbench",
            "guest_identity": {"user_id": "edit_owner"},
            "thread_id": 101,
            "stream_kind": "custom_tool",
        },
        emit=emitted.append,
    )

    assert len(agent.calls) == 1
    call = agent.calls[0]
    assert call["tool_name"] == "ct_synthetic_momentum"
    assert call["requirement_text"] == "把阈值从3%改成3.5%"
    assert call["owner_id"] == "edit_owner"
    assert call["owner_ids"] == ["edit_owner", "101"]
    assert any(
        item.get("block_id") == "edit_plan_live_progress"
        and item.get("data", {}).get("status") == "completed"
        for item in emitted
    )
    done = emitted[-1]
    assert done["event"] == "done"
    assert done["result"]["edit_summary"]["candidate_revision"] == 2
    blocks = done["result"]["surface_blocks"]
    activation = next(block for block in blocks if block.get("block_type") == "interaction")
    assert activation["data"]["actions"][0]["action_id"] == "custom_tool.activate_draft"
    assert activation["data"]["actions"][0]["expected_revision"] == 2
    assert conversations.context["custom_tool_state"]["implementation_revision"] == 2
