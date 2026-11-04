"""Permission/immutable revision contracts with real catalog, no production writes."""
import pytest

from tests.test_skill_registry_visibility import hub, candidate, activate
from src.services.skill_candidate_store_service import SkillCandidateConflictError, SkillCandidateNotFoundError


def edit_payload(base, text="Revised method"):
    return {"expected_candidate_revision": base["candidate_revision_no"],
        "expected_content_hash": base["content_hash"], "display_name": "Edited",
        "skill_markdown": base["skill_markdown"].split("\n---\n", 1)[0] + "\n---\n" + text,
        "references": {"references/method.md": "Revised reference"},
        "control_manifest": {"related_skills": [{"skill_id": "system-research", "purpose": "Use evidence"}]}}


def test_system_edit_is_admin_only_versioned_and_not_source_write(hub):
    root_text = (hub.business_catalog.root / "skills/system-research/SKILL.md").read_text()
    for is_admin in [False]:
        with pytest.raises(PermissionError):
            hub.definition("system-research", actor_id="alice", is_admin=is_admin)
        with pytest.raises(PermissionError):
            hub.save_definition("system-research", {}, actor_id="alice", is_admin=is_admin)
    base = hub.definition("system-research", actor_id="admin-a", is_admin=True)
    frozen = hub.runtime_catalog(owner_ids=["alice"])
    saved = hub.save_definition("system-research", edit_payload(base), actor_id="admin-a", is_admin=True)
    assert saved["owner_id"] == "system" and saved["authoring_evidence"]["edited_by"] == "admin-a"
    assert hub.runtime_catalog(owner_ids=["alice"]).revision == frozen.revision
    with pytest.raises(SkillCandidateConflictError):
        hub.save_definition("system-research", edit_payload(base), actor_id="admin-b", is_admin=True)
    with pytest.raises(PermissionError):
        hub.activate_definition("system-research", actor_id="alice", expected_candidate_revision=1, expected_active_revision=0)
    hub.activate_definition("system-research", actor_id="admin-a", is_admin=True, expected_candidate_revision=1, expected_active_revision=0)
    current = hub.runtime_catalog(owner_ids=["alice"])
    assert "Revised method" in current.load("system-research")["method"]
    assert "System method" in frozen.load("system-research")["method"]
    assert current.studio_detail("system-research")["category"] == "research"
    assert current.studio_detail("system-research")["owner"] == "system"
    assert hub.detail("system-research", owner_ids=["alice"])["viewable"]
    assert not hub.detail("system-research", owner_ids=["alice"])["editable"]
    assert hub.detail("system-research", owner_ids=["admin-a"], is_admin=True)["editable"]
    assert (hub.business_catalog.root / "skills/system-research/SKILL.md").read_text() == root_text


def test_shared_personal_owner_only_even_for_admin_and_unpublished_revision_is_hidden(hub):
    hub.candidate_store.create_candidate(candidate(), owner_id="alice")
    activate(hub)
    hub.set_visibility("personal-research", owner_id="alice", visibility="public", expected_active_revision=1)
    for actor, admin in [("bob", False), ("admin", True)]:
        detail = hub.detail("personal-research", owner_ids=[actor], is_admin=admin)
        assert detail["invocation_enabled"] and not detail["viewable"] and not detail["editable"]
        assert not any(key in detail for key in ("skill_markdown", "references", "definition", "companion_files", "controls"))
        with pytest.raises(SkillCandidateNotFoundError):
            hub.definition("personal-research", actor_id=actor, is_admin=admin)
        with pytest.raises(SkillCandidateNotFoundError):
            hub.save_definition("personal-research", {}, actor_id=actor, is_admin=admin)
        assert "error" in hub.load_business_reference("personal-research", "references/method.md", owner_ids=[actor])
    base = hub.definition("personal-research", actor_id="alice")
    hub.save_definition("personal-research", edit_payload(base, "UNPUBLISHED"), actor_id="alice")
    active = hub.detail("personal-research", owner_ids=["alice"])
    assert active["editable"] and "UNPUBLISHED" not in active["skill_markdown"]
    assert "UNPUBLISHED" in hub.definition("personal-research", actor_id="alice")["skill_markdown"]
    hub.activate_definition("personal-research", actor_id="alice", expected_candidate_revision=2, expected_active_revision=1)
    assert "UNPUBLISHED" in hub.runtime_catalog(owner_ids=["bob"]).load("personal-research")["method"]


@pytest.mark.parametrize("change", [
    {"skill_markdown": "no frontmatter"},
    {"skill_markdown": "---\nname: system-research\ndescription: x\n---\n"},
    {"references": {"../secrets": "text"}},
    {"control_manifest": {"related_skills": ["invalid"]}},
])
def test_invalid_executable_inputs_never_save(hub, change):
    base = hub.definition("system-research", actor_id="admin", is_admin=True)
    from src.services.skill_candidate_store_service import SkillCandidateStoreError
    with pytest.raises((ValueError, SkillCandidateStoreError)):
        hub.save_definition("system-research", {**edit_payload(base), **change}, actor_id="admin", is_admin=True)
    assert hub.definition("system-research", actor_id="admin", is_admin=True)["candidate_revision_no"] == 0


def test_http_permissions_ignore_client_role_and_keep_usage_metadata(hub, monkeypatch):
    from src.web import flask_app as web
    identity = {"user_id": "alice", "user_type": "member"}
    monkeypatch.setattr(web, "skill_hub_catalog_service", hub)
    monkeypatch.setattr(web, "_resolve_current_guest_identity", lambda: identity)
    monkeypatch.setattr(web, "_resolve_current_member_identity", lambda: identity)
    client = web.app.test_client()
    assert client.get("/api/skill-hub/system-research/definition?is_admin=true").status_code == 403
    assert client.put("/api/skill-hub/system-research/definition", json={"is_admin": True}).status_code == 403
    identity.update(user_id="admin", user_type="admin")
    base = client.get("/api/skill-hub/system-research/definition").get_json()["candidate"]
    assert client.put("/api/skill-hub/system-research/definition", json=edit_payload(base)).status_code == 200
    assert client.put("/api/skill-hub/system-research/definition", json=edit_payload(base)).status_code == 409
    assert client.post("/api/skill-hub/system-research/definition/activate", json={"expected_candidate_revision": 1, "expected_active_revision": 0}).status_code == 200
    assert client.get("/api/skill-hub/system-research").get_json()["skill"]["editable"]
    hub.candidate_store.create_candidate(candidate(), owner_id="alice")
    activate(hub)
    hub.set_visibility("personal-research", owner_id="alice", visibility="public", expected_active_revision=1)
    detail = client.get("/api/skill-hub/personal-research").get_json()["skill"]
    assert detail["invocation_enabled"] and "skill_markdown" not in detail
    assert client.get("/api/skill-hub/personal-research/references/references/method.md").status_code == 404
    assert client.get("/api/skill-hub/personal-research/definition").status_code == 404


def test_edit_accepts_crlf_and_preserves_omitted_optional_assets(hub):
    hub.candidate_store.create_candidate(candidate(), owner_id="alice")
    base = hub.definition("personal-research", actor_id="alice")
    payload = edit_payload(base)
    payload["skill_markdown"] = payload["skill_markdown"].replace("\n", "\r\n")
    del payload["references"]
    del payload["control_manifest"]
    saved = hub.save_definition("personal-research", payload, actor_id="alice")
    assert saved["references"] == base["references"]
    assert saved["control_manifest"] == base["control_manifest"]
    assert "\r" not in saved["skill_markdown"]
