from src.web import flask_app as web


def test_invocable_asset_catalog_is_owner_scoped_and_searchable(monkeypatch) -> None:
    captured = {}

    def list_assets(**kwargs):
        captured.update(kwargs)
        return [{
            "ref": "tool:ct_market_buy_decision",
            "kind": "tool",
            "name": "ct_market_buy_decision",
            "display_name": "大盘状态与买入决策",
            "summary": "判断当前市场状态并给出买入决策。",
            "invocation": "$ct_market_buy_decision",
            "input_fields": [],
            "custom_tool": True,
        }]

    monkeypatch.setattr(web, "_resolve_current_guest_identity", lambda: {"user_id": "user-42"})
    monkeypatch.setattr(web.asset_invocation_service, "list_invocable_assets", list_assets)

    response = web.app.test_client().get("/api/assets/invocable?q=大盘&kind=tool&limit=8")

    assert response.status_code == 200
    assert response.get_json()["items"][0]["ref"] == "tool:ct_market_buy_decision"
    assert captured == {
        "owner_ids": ["user-42"],
        "query": "大盘",
        "kind": "tool",
        "limit": 8,
    }


def test_selected_asset_identity_is_preserved_in_persisted_user_text() -> None:
    selected = {
        "ref": "tool:ct_market_buy_decision",
        "kind": "tool",
        "name": "ct_market_buy_decision",
    }

    assert web._asset_invocation_user_display_text("分析贵州茅台", selected) == (
        "$ct_market_buy_decision 分析贵州茅台"
    )
    assert web._asset_invocation_user_display_text("", selected) == (
        "$ct_market_buy_decision"
    )
    assert web._asset_invocation_user_display_text(
        "$ct_market_buy_decision 分析贵州茅台",
        selected,
    ) == "$ct_market_buy_decision 分析贵州茅台"


def test_asset_invocation_forwards_live_runtime_events(monkeypatch) -> None:
    emitted = []
    event_sink = emitted.append
    captured = {}

    class RuntimeStub:
        def execute_for_assistant(self, **kwargs):
            captured.update(kwargs)
            kwargs["event_sink"]({
                "event": "block",
                "block_id": "runtime_scan",
                "block_type": "status",
                "content": "已提交 3 个标的，正在批量执行。",
                "data": {"role": "process", "status": "running"},
            })
            return {
                "mode": "tool_plan_completed",
                "message": "已完成",
                "surface_blocks": [],
                "llm_usage": {},
            }

    monkeypatch.setattr(
        web.asset_invocation_service,
        "build_tool_execution_plan",
        lambda _invocation: {"objective": "扫描测试板块", "work_items": []},
    )
    monkeypatch.setattr(web, "tool_plan_runtime_service", RuntimeStub())
    result = web._execute_asset_invocation_payload(
        {
            "status": "ready",
            "user_request": "扫描测试板块",
            "target": {"kind": "tool", "name": "ct_batch_probe"},
            "preview": {"target": {"kind": "tool", "name": "ct_batch_probe"}},
        },
        text="扫描测试板块",
        application_context={},
        thread_context={},
        thread_id=1,
        turn_id=1,
        event_sink=event_sink,
    )

    assert captured["event_sink"] is event_sink
    assert emitted[0]["data"]["status"] == "running"
    assert result["surface_blocks"][0]["block_id"] == "asset_invocation_preview"
