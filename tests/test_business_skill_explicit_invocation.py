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
