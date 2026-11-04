"""Offline contracts for authorized business-method discovery and `$` use."""
import pytest

from src.services.asset_invocation_service import AssetInvocationService


class EmptyToolStore:
    def exists(self, name):
        return False


class MethodCatalog:
    def __init__(self, names):
        self.names = names

    def public_entries(self):
        return [{"id": name, "description": "研报专业方法", "category": "research"} for name in self.names]

    def studio_detail(self, name):
        return {"display_name": name, "owner": "alice", "visibility": "private", "active_revision_no": 3}


def method_service(tmp_path, seen):
    def catalog_provider(*, owner_ids):
        seen.append(list(owner_ids))
        names = ["equity-report-analysis"]
        if "alice" in owner_ids:
            names.append("my-report-method")
        return MethodCatalog(names)

    return AssetInvocationService(
        custom_tool_store=EmptyToolStore(),
        tool_definitions_dir=str(tmp_path / "tools"),
        skills_root=str(tmp_path / "legacy"),
        business_catalog_provider=catalog_provider,
        llm_chat=lambda *args, **kwargs: pytest.fail("Method selection must not call the Tool argument LLM"),
    )


def test_dollar_business_method_preserves_question_and_uses_only_user_identity(tmp_path):
    seen = []
    service = method_service(tmp_path, seen)
    result = service.plan(
        text="$my-report-method 原料涨价10%有何影响？",
        owner_ids=["thread-18", "alice"],
        business_owner_id="alice",
        attachments=[{"attachment_id": "authorized-attachment"}],
    )
    assert result["status"] == "ready"
    assert result["contract"]["skill_type"] == "business_method"
    assert result["user_request"] == "原料涨价10%有何影响？"
    assert result["calls"] == [{"question": "原料涨价10%有何影响？"}]
    assert result["attachments"] == [{"attachment_id": "authorized-attachment"}]
    assert seen and all(owners == ["alice"] for owners in seen)


def test_private_skill_cannot_be_selected_through_legacy_tool_owner_alias(tmp_path):
    seen = []
    service = method_service(tmp_path, seen)
    result = service.plan(
        text="$my-report-method 查研报",
        owner_ids=["alice", "thread-18"],
        business_owner_id="bob",
    )
    assert result["status"] == "needs_input"
    assert not result["calls"]
    assert all(owners == ["bob"] for owners in seen)
    assert all(item["name"] != "my-report-method" for item in result["candidates"])


def test_business_skill_picker_and_selected_reference_use_same_catalog(tmp_path):
    service = method_service(tmp_path, [])
    assets = service.list_invocable_assets(business_owner_id="alice", kind="skill")
    selected = next(item for item in assets if item["name"] == "my-report-method")
    result = service.plan(text="分析机构分歧", selected_asset=selected, business_owner_id="alice")
    assert result["target"] == {"kind": "skill", "name": "my-report-method"}
    assert selected["visibility"] == "private"
    assert selected["active_revision_no"] == 3


def test_business_method_stays_in_finance_session_with_live_events(monkeypatch):
    from src.web import flask_app as web
    captured = {}
    events = []

    def answer(**kwargs):
        captured.update(kwargs)
        kwargs["event_sink"]({"event": "status", "message": "读取专业方法"})
        return {"message": "机构观点分析", "surface_blocks": [], "llm_usage": {}}

    monkeypatch.setattr(web.financial_qa_cc_service, "answer", answer)
    monkeypatch.setattr(web, "_submit_generic_skill_job", lambda *a, **k: pytest.fail("business method must not enter legacy SkillRunner"))
    result = web._execute_asset_invocation_payload(
        {"status": "ready", "target": {"kind": "skill", "name": "my-report-method"},
         "contract": {"skill_type": "business_method"}, "user_request": "机构怎么看？",
         "attachments": [{"attachment_id": "a1"}]},
        text="$my-report-method 机构怎么看？", application_context={"application_name": "investment_workbench"},
        thread_context={"_custom_tool_owner_ids": ["alice", "999"]},
        thread_id=7, turn_id=19, owner_id="alice", event_sink=events.append,
        financial_qa_runtime="dsh", research_mode="deep",
    )
    assert captured["explicit_skill_ids"] == ["my-report-method"]
    assert captured["owner_ids"] == ["alice"]
    assert captured["thread_id"] == 7 and captured["turn_id"] == 19
    assert captured["runtime"] == "dsh"
    assert captured["user_text"] == "机构怎么看？"
    assert captured["attachments"] == [{"attachment_id": "a1"}]
    assert events[0]["message"] == "读取专业方法"
    assert result["message"] == "机构观点分析"


def test_activation_and_sharing_require_authenticated_owner_and_expected_revision(monkeypatch):
    from src.web import flask_app as web
    calls = []
    monkeypatch.setattr(web, "_resolve_current_member_identity", lambda: {"user_id": "alice"})
    monkeypatch.setattr(web.skill_hub_catalog_service, "activate_candidate", lambda name, **kwargs: calls.append((name, kwargs)) or {"skill_id": name, "active_revision_no": 2, "visibility": "private"})
    monkeypatch.setattr(web.skill_hub_catalog_service, "set_visibility", lambda name, **kwargs: calls.append((name, kwargs)) or {"skill_id": name, "active_revision_no": 2, "visibility": kwargs["visibility"]})
    client = web.app.test_client()
    activated = client.post("/api/skill-hub/candidates/mine/activate", json={"owner_id": "victim", "expected_candidate_revision": 2, "expected_active_revision": 0})
    assert activated.status_code == 200
    assert activated.get_json()["candidate"]["visibility"] == "private"
    assert calls[-1] == ("mine", {"owner_id": "alice", "expected_candidate_revision": 2, "expected_active_revision": 0})
    shared = client.patch("/api/skill-hub/candidates/mine/visibility", json={"visibility": "public", "expected_active_revision": 2})
    assert shared.status_code == 200 and calls[-1][1]["owner_id"] == "alice"
    invalid = client.post("/api/skill-hub/candidates/mine/activate", json={"expected_candidate_revision": 2})
    assert invalid.status_code == 400
    monkeypatch.setattr(web, "_resolve_current_member_identity", lambda: None)
    denied = client.patch("/api/skill-hub/candidates/mine/visibility", json={"visibility": "public", "expected_active_revision": 2})
    assert denied.status_code == 403


def test_skill_hub_routes_scope_details_and_references_to_current_user(monkeypatch):
    from src.web import flask_app as web
    seen = []
    monkeypatch.setattr(web, "_resolve_current_guest_identity", lambda: {"user_id": "alice"})
    monkeypatch.setattr(web.skill_hub_catalog_service, "catalog", lambda **kwargs: seen.append(kwargs) or {"items": []})
    monkeypatch.setattr(web.skill_hub_catalog_service, "detail", lambda *args, **kwargs: seen.append(kwargs) or {"skill_id": "mine"})
    monkeypatch.setattr(web.skill_hub_catalog_service, "load_business_reference", lambda *args, **kwargs: seen.append(kwargs) or {"content": "reference"})
    client = web.app.test_client()
    for url in ["/api/skill-hub?owner_id=victim", "/api/skill-hub/mine?owner_id=victim", "/api/skill-hub/mine/references/references/method.md?owner_id=victim&revision=r1"]:
        assert client.get(url).status_code == 200
    assert all(call["owner_ids"] == ["alice"] for call in seen)
    assert seen[-1]["expected_revision"] == "r1"


def test_multiple_selected_methods_preserve_order_and_share_one_execution(tmp_path, monkeypatch):
    from src.web import flask_app as web
    service = method_service(tmp_path, [])
    selected = [{"kind": "skill", "name": name} for name in ["my-report-method", "equity-report-analysis", "my-report-method"]]
    invocation = service.plan(text="综合分析并补充行情", selected_assets=selected, business_owner_id="alice")
    assert invocation["explicit_skill_ids"] == ["my-report-method", "equity-report-analysis"]
    calls = []
    monkeypatch.setattr(web.financial_qa_cc_service, "answer", lambda **kwargs: calls.append(kwargs) or {"message": "综合结果", "surface_blocks": []})
    result = web._execute_asset_invocation_payload(invocation, text="综合分析并补充行情",
        application_context={}, thread_context={}, thread_id=1, turn_id=2, owner_id="alice", financial_qa_runtime="dsh")
    assert len(calls) == 1
    assert calls[0]["explicit_skill_ids"] == ["my-report-method", "equity-report-analysis"]
    assert calls[0]["user_text"] == "综合分析并补充行情"
    assert result["message"] == "综合结果"
    assert result["surface_blocks"][0]["data"]["items"][1]["value"] == "$my-report-method → $equity-report-analysis"
    assert web._asset_invocation_user_display_text("分析", None, selected[:2]) == "$my-report-method $equity-report-analysis 分析"


def test_multiple_selection_checks_every_method_permission_before_execution(tmp_path):
    service = method_service(tmp_path, [])
    result = service.plan(text="分析", selected_assets=[{"kind": "skill", "name": name} for name in ["equity-report-analysis", "my-report-method"]], business_owner_id="bob")
    assert result["status"] == "needs_input"
    assert result["calls"] == []
    assert "explicit_skill_ids" not in result


def test_single_list_selection_retains_legacy_contract(tmp_path):
    service = method_service(tmp_path, [])
    selected = {"kind": "skill", "name": "my-report-method"}
    assert service.plan(text="分析", selected_assets=[selected], business_owner_id="alice") == service.plan(text="分析", selected_asset=selected, business_owner_id="alice")


@pytest.mark.parametrize("value", ["skill:a", ["a"], [{}]])
def test_malformed_selected_assets_rejected_at_transport(value):
    from src.web import flask_app as web
    with pytest.raises(ValueError):
        web._selected_assets({"selected_assets": value})


@pytest.mark.parametrize("transport", ["sync", "stream"])
def test_multi_method_chat_transport_reaches_one_agent_and_persists_order(tmp_path, monkeypatch, transport):
    import json
    from src.web import flask_app as web
    from src.services.follow_up_question_service import FollowUpQuestionService
    seen = []
    saved = []
    monkeypatch.setattr(web, "asset_invocation_service", method_service(tmp_path, []))
    monkeypatch.setattr(web, "_resolve_current_guest_identity", lambda: {"user_id": "alice"})
    monkeypatch.setattr(web, "_guest_question_denial", lambda _: None)
    monkeypatch.setattr(web, "_schedule_thread_title", lambda **kwargs: None)
    monkeypatch.setattr(web.application_runtime_service, "get_application_context", lambda _: {})
    monkeypatch.setattr(web.attachment_service, "list_attachments", lambda *a, **kw: [])
    conversation = web.runtime_conversation_service
    monkeypatch.setattr(conversation, "ensure_thread", lambda **kw: 123)
    monkeypatch.setattr(conversation, "get_thread_context", lambda **kw: {})
    monkeypatch.setattr(conversation, "get_context_window", lambda **kw: [])
    monkeypatch.setattr(conversation, "create_turn", lambda **kw: saved.append(kw) or 456)
    monkeypatch.setattr(conversation, "complete_turn", lambda **kw: saved.append(kw) or {})
    monkeypatch.setattr(FollowUpQuestionService, "generate", lambda *a, **kw: {"questions": ["还需要哪些证据？"], "llm_usage": {}})
    monkeypatch.setattr(web.financial_qa_cc_service, "answer", lambda **kw: seen.append(kw) or {"mode": "financial_qa_dsh", "message": "综合结果", "financial_qa": {"runtime": "dsh"}, "surface_blocks": []})
    selected = [{"kind": "skill", "name": name} for name in ["my-report-method", "equity-report-analysis"]]
    client = web.app.test_client()
    request = {"text": "综合分析", "thread_id": 123, "selected_assets": selected, "financial_qa_runtime": "dsh"}
    if transport == "sync":
        response = client.post("/api/chat/dispatch", json=request)
        assert response.status_code == 200, response.get_data(as_text=True)
        result = response.get_json()
    else:
        start = client.post("/api/chat/stream/start", json=request)
        assert start.status_code == 200
        response = client.get(start.get_json()["stream_url"])
        events = [json.loads(line[6:]) for line in response.get_data(as_text=True).splitlines() if line.startswith("data: ")]
        assert not [item for item in events if item.get("event") == "error"], events
        result = next(item["result"] for item in events if item.get("event") == "done")
    assert len(seen) == 1
    assert seen[0]["explicit_skill_ids"] == ["my-report-method", "equity-report-analysis"]
    assert saved[0]["user_input_text"] == "$my-report-method $equity-report-analysis 综合分析"
    assert saved[-1]["output_payload"]["follow_up_questions"] == ["还需要哪些证据？"]
    assert result["follow_up_questions"] == ["还需要哪些证据？"]


def test_generic_finance_entry_preserves_natural_question_without_inventing_dsl(tmp_path, monkeypatch):
    service = method_service(tmp_path, [])
    target = {"kind": "tool", "name": "finance_data_query"}
    monkeypatch.setattr(service, "_resolve_invocation_target", lambda **kw: {"status": "resolved", "target": target})
    checked = []
    def contract(**kw):
        checked.append(kw)
        return {"display_name": "金融数据协议查询", "input_schema": {"type": "object", "properties": {"request": {"type": "string"}}}}
    monkeypatch.setattr(service, "load_contract", contract)
    result = service.plan(text="第二个怎么样？", selected_asset=target, business_owner_id="alice")
    assert result["status"] == "ready"
    assert result["user_request"] == "第二个怎么样？"
    assert checked[0]["business_owner_id"] == "alice"


def test_generic_finance_entry_uses_shared_session_and_owner(monkeypatch):
    from src.web import flask_app as web
    calls = []
    monkeypatch.setattr(web.financial_qa_cc_service, "answer", lambda **kw: calls.append(kw) or {"message": "第二个是五粮液", "surface_blocks": []})
    monkeypatch.setattr(web.tool_plan_runtime_service, "execute_for_assistant", lambda **kw: pytest.fail("generic natural-language entry must not invent a standalone DSL call"))
    result = web._execute_asset_invocation_payload(
        {"status": "ready", "target": {"kind": "tool", "name": "finance_data_query"},
         "contract": {}, "user_request": "第二个怎么样？"},
        text="第二个怎么样？", application_context={}, thread_context={}, thread_id=7, turn_id=20,
        owner_id="alice", financial_qa_runtime="dsh")
    assert calls[0]["thread_id"] == 7 and calls[0]["owner_ids"] == ["alice"]
    assert calls[0]["explicit_skill_ids"] == [] and calls[0]["user_text"] == "第二个怎么样？"
    assert result["message"] == "第二个是五粮液"
