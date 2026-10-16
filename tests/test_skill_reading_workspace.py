"""Real system catalog with isolated identity/store; no model or production writes."""
from pathlib import Path

import pytest

from src.scenarios.financial_qa.business_skills import FinanceBusinessSkillCatalog
from src.services.skill_candidate_store_service import InMemorySkillCandidateStoreService
from src.services.skill_hub_catalog_service import SkillHubCatalogService
from src.services.skill_studio_service import SkillStudioService


@pytest.fixture
def workspace(monkeypatch, tmp_path):
    from src.web import flask_app as web
    hub = SkillHubCatalogService(
        business_catalog=FinanceBusinessSkillCatalog(snapshot_root=tmp_path / "snapshots"),
        legacy_skill_studio=SkillStudioService(skills_root=str(tmp_path / "legacy")),
        candidate_store=InMemorySkillCandidateStoreService(),
    )
    monkeypatch.setattr(web, "skill_hub_catalog_service", hub)
    monkeypatch.setattr(web, "_resolve_current_guest_identity", lambda: {"user_id": "reader-test"})
    monkeypatch.setattr(web, "_resolve_current_member_identity", lambda: {"user_id": "reader-test", "user_type": "member"})
    return web, web.app.test_client(), hub


def test_current_system_methods_are_readable_but_not_writable(workspace):
    _, client, _ = workspace
    listing = client.get("/api/skill-hub").get_json()
    items = listing["items"]
    assert len(items) >= 15
    for item in items:
        assert item["owner"] == "system" and item["editable"] is False
        response = client.get(f"/api/skill-hub/{item['skill_name']}", query_string={"catalog_id": item["catalog_id"]})
        assert response.status_code == 200
        detail = response.get_json()["skill"]
        assert detail["skill_markdown"] and detail["content_hash"]
        assert detail["editable"] is False
    assert client.put("/api/skill-hub/stock-research", json={"skill_markdown": "changed"}).status_code == 405
    assert client.delete("/api/skill-hub/stock-research").status_code == 405


def test_reference_is_read_from_the_same_revision_and_unknown_assets_not_substituted(workspace):
    _, client, _ = workspace
    detail = client.get("/api/skill-hub/fund-analysis").get_json()["skill"]
    ref = detail["references"][0]
    response = client.get(f"/api/skill-hub/fund-analysis/references/{ref['path']}", query_string={"revision": detail["revision"]})
    assert response.status_code == 200
    assert response.get_json()["content_hash"] == ref["content_hash"]
    assert client.get("/api/skill-hub/does-not-exist").status_code == 404
    mismatch = client.get(f"/api/skill-hub/fund-analysis/references/{ref['path']}?revision=stale")
    assert mismatch.status_code in (400, 409)


def test_reading_route_uses_react_and_preserves_explicit_authoring(workspace, tmp_path, monkeypatch):
    web, client, _ = workspace
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<main>reader-app</main>")
    monkeypatch.setattr(web, "REACT_FRONTEND_DIST_DIR", dist)
    assert b"reader-app" in client.get("/skills/studio").data
    assert b"reader-app" in client.get("/skills/studio/stock-research").data
    authoring = client.get("/skills/studio?mode=authoring")
    assert authoring.status_code == 200 and b"createCandidateBtn" in authoring.data
    monkeypatch.setattr(web, "REACT_FRONTEND_DIST_DIR", tmp_path / "missing")
    assert b"createCandidateBtn" in client.get("/skills/studio").data
