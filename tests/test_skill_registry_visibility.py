"""Offline active registry / permission contracts; no production database."""
import json
from copy import deepcopy

import pytest

from src.scenarios.financial_qa.business_skills import FinanceBusinessSkillCatalog
from src.services.skill_candidate_store_service import (
    DatabaseSkillCandidateStoreService, InMemorySkillCandidateStoreService,
    SkillCandidateConflictError, SkillCandidateNotFoundError, SkillCandidateStoreError,
)
from src.services.skill_hub_catalog_service import SkillHubCatalogService
from src.services.skill_studio_service import SkillStudioService


def candidate(name="personal-research", revision=1, text="Method one", references=None):
    return {
        "skill_id": name, "revision_no": revision, "description": "A research method",
        "display_name": "My research", "content_hash": "candidate-hash",
        "skill_markdown": f"---\nname: {name}\ndescription: A research method\n"
            "allowed-tools: [mcp__finance__general_search]\nhooks: {PreToolUse: unsafe}\n---\n" + text,
        "references": references or {"references/method.md": "Reference one"},
    }


@pytest.fixture
def hub(tmp_path):
    root = tmp_path / "system"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "test-finance"}))
    (root / "catalog.json").write_text(json.dumps({"skills": [{
        "id": "system-research", "path": "skills/system-research", "category": "research",
    }]}))
    skill = root / "skills/system-research"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: system-research\ndescription: System research\n---\nSystem method")
    return SkillHubCatalogService(
        business_catalog=FinanceBusinessSkillCatalog(root=root, snapshot_root=tmp_path / "runtime"),
        legacy_skill_studio=SkillStudioService(skills_root=str(tmp_path / "legacy")),
        candidate_store=InMemorySkillCandidateStoreService(),
    )


def activate(hub, name="personal-research", owner="alice", revision=1, previous=0):
    return hub.activate_candidate(name, owner_id=owner, expected_candidate_revision=revision,
        expected_active_revision=previous)


def test_draft_private_public_use_one_authorized_snapshot(hub):
    hub.candidate_store.create_candidate(candidate(), owner_id="alice")
    assert [item["skill_name"] for item in hub.list_skills(owner_ids=["alice"])] == ["system-research"]
    assert hub.detail("personal-research", owner_ids=["bob"]) is None
    activate(hub)
    alice = hub.catalog(owner_ids=["alice"])
    assert [(item["skill_name"], item["scope"]) for item in alice["items"]] == [
        ("system-research", "system"), ("personal-research", "private")]
    private = alice["items"][1]
    assert private["owned"] and private["invocation_enabled"]
    assert private["active_revision_no"] == 1
    assert hub.detail("personal-research", owner_ids=["bob"]) is None
    assert "error" in hub.load_business_reference("personal-research", "references/method.md", owner_ids=["bob"])
    hub.set_visibility("personal-research", owner_id="alice", visibility="public", expected_active_revision=1)
    bob = hub.detail("personal-research", owner_ids=["bob"])
    assert bob["scope"] == "public" and not bob["owned"]
    runtime = hub.runtime_catalog(owner_ids=["bob"])
    assert runtime.revision == bob["revision"]
    assert runtime.load("personal-research")["method"] == bob["skill_markdown"]
    assert hub.load_business_reference("personal-research", "references/method.md",
        expected_revision=bob["revision"], owner_ids=["bob"])["content"] == "Reference one"
    hub.set_visibility("personal-research", owner_id="alice", visibility="private", expected_active_revision=1)
    assert hub.detail("personal-research", owner_ids=["bob"]) is None


def test_revisions_pin_method_and_reference_and_require_explicit_activation(hub):
    hub.candidate_store.create_candidate(candidate(), owner_id="alice")
    activate(hub)
    first = hub.runtime_catalog(owner_ids=["alice"])
    first_binding = first.runtime_binding()
    first.validate_runtime_binding(first_binding)
    hub.candidate_store.save_revision(candidate(revision=2, text="Method two",
        references={"references/method.md": "Reference two"}), owner_id="alice", expected_base_revision=1)
    assert hub.candidate_store.load_latest("personal-research", owner_id="alice")["published"] is False
    assert hub.runtime_catalog(owner_ids=["alice"]).revision == first.revision
    assert hub.candidate_store.load_revision("personal-research", 1, owner_id="alice")["references"] == {
        "references/method.md": "Reference one"}
    activate(hub, revision=2, previous=1)
    second = hub.runtime_catalog(owner_ids=["alice"])
    assert second.revision != first.revision
    assert "Method one" in first.load("personal-research")["method"]
    assert first.load_reference("personal-research", "references/method.md")["content"] == "Reference one"
    assert second.load_reference("personal-research", "references/method.md")["content"] == "Reference two"
    assert "error" in second.load_reference("personal-research", "references/method.md", expected_revision=first.revision)
    pinned = second.method_snapshot(allowed_skill_ids=["personal-research"])
    assert list(pinned["skills"]) == ["personal-research"]
    assert pinned["revision"] == second.revision
    assert pinned["skills"]["personal-research"]["references"]["references/method.md"]["content"] == "Reference two"


def test_owner_and_revision_cas_guard_activation_and_sharing(hub):
    hub.candidate_store.create_candidate(candidate(), owner_id="alice")
    with pytest.raises(SkillCandidateNotFoundError):
        activate(hub, owner="bob")
    with pytest.raises(SkillCandidateConflictError):
        hub.set_visibility("personal-research", owner_id="alice", visibility="public", expected_active_revision=0)
    with pytest.raises(SkillCandidateConflictError):
        activate(hub, revision=2)
    activate(hub)
    with pytest.raises(SkillCandidateConflictError):
        activate(hub, previous=0)
    with pytest.raises(SkillCandidateNotFoundError):
        hub.set_visibility("personal-research", owner_id="bob", visibility="public", expected_active_revision=1)
    with pytest.raises(SkillCandidateConflictError):
        hub.set_visibility("personal-research", owner_id="alice", visibility="public", expected_active_revision=0)
    with pytest.raises(SkillCandidateStoreError):
        hub.set_visibility("personal-research", owner_id="alice", visibility="system", expected_active_revision=1)


def test_personal_content_cannot_grant_runtime_tools_or_override_system(hub):
    source = {**candidate(), "owner_id": "system", "owner": "system", "visibility": "public"}
    source["skill_markdown"] = source["skill_markdown"].replace("hooks:", "owner: system\nvisibility: public\nhooks:")
    stored = hub.candidate_store.create_candidate(source, owner_id="alice")
    assert stored["owner_id"] == "alice" and stored["visibility"] == "private"
    activate(hub)
    catalog = hub.runtime_catalog(owner_ids=["alice"])
    assert catalog.allowed_tools_by_skill()["personal-research"] == []
    method = catalog.load("personal-research")["method"]
    assert "allowed-tools" not in method and "hooks:" not in method
    assert "owner: system" not in method and "visibility: public" not in method
    assert catalog.studio_detail("personal-research")["scope"] == "private"
    assert (catalog.runtime_root / "skills/personal-research/SKILL.md").read_text().strip() == method
    hub.candidate_store.create_candidate(candidate(name="system-research"), owner_id="alice")
    with pytest.raises(RuntimeError, match="conflicts"):
        activate(hub, name="system-research")
    assert hub.candidate_store.load_latest("system-research", owner_id="alice")["active_revision_no"] == 0


def test_activation_rechecks_candidate_after_snapshot_validation(hub, monkeypatch):
    hub.candidate_store.create_candidate(candidate(), owner_id="alice")
    compile_snapshot = hub.business_catalog.with_active_skills

    def concurrent_revision(records):
        validated = compile_snapshot(records)
        hub.candidate_store.save_revision(candidate(revision=2, text="Concurrent edit"),
            owner_id="alice", expected_base_revision=1)
        return validated

    monkeypatch.setattr(hub.business_catalog, "with_active_skills", concurrent_revision)
    with pytest.raises(SkillCandidateConflictError, match="candidate revision changed"):
        activate(hub)
    assert hub.candidate_store.load_latest("personal-research", owner_id="alice")["active_revision_no"] == 0


@pytest.mark.parametrize("path", ["../secret", "references/../../secret", "/references/x", "scripts/x.py"])
def test_reference_assets_reject_escaping_paths(path):
    store = InMemorySkillCandidateStoreService()
    with pytest.raises(SkillCandidateStoreError):
        store.create_candidate(candidate(references={path: "secret"}), owner_id="alice")


def test_default_catalog_does_not_connect_registry_at_startup(hub, monkeypatch):
    service = SkillHubCatalogService(business_catalog=hub.business_catalog,
        legacy_skill_studio=hub.legacy_skill_studio)
    monkeypatch.setattr(DatabaseSkillCandidateStoreService, "_connect",
        lambda: pytest.fail("must not connect during default system discovery"))
    assert service.runtime_catalog() is hub.business_catalog
    assert len(service.catalog()["items"]) == 1


class FakeDatabase:
    """Minimal transactional registry fake exercising actual SQL service methods."""
    def __init__(self):
        self.artifact = {"artifact_id": 1, "artifact_type": "skill_v2", "name": "personal-research",
            "owner": "alice", "enabled": 0, "current_revision_no": 0,
            "source_manifest_json": json.dumps({"candidate_revision_no": 1, "visibility": "private"})}
        self.revisions = {1: {"revision_no": 1, "markdown_text": candidate()["skill_markdown"],
            "definition_json": {"references": {"references/method.md": "Reference one"}}}}
        self.revision_reads = 0
        self.commits = 0
        self.rollbacks = 0
        self.sql = []

    def connect(self): return self
    def cursor(self): return self
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def close(self): pass
    def commit(self): self.commits += 1
    def rollback(self): self.rollbacks += 1
    def fetchone(self): return deepcopy(self.result)
    def fetchall(self): return deepcopy(self.result)

    def execute(self, sql, params=()):
        sql = " ".join(sql.split())
        self.sql.append((sql, params))
        if sql.startswith("SELECT * FROM aiia_runtime_artifact_revision"):
            self.revision_reads += 1
            self.result = self.revisions.get(params[1])
        elif sql.startswith("SELECT * FROM aiia_runtime_artifact"):
            if "name=%s" in sql:
                self.result = self.artifact if params[1:] == (self.artifact["name"], self.artifact["owner"]) else None
            else:
                # Deliberately return all rows; the store must enforce access
                # before loading revision bodies, in addition to SQL scope.
                self.result = [self.artifact]
        elif sql.startswith("UPDATE aiia_runtime_artifact"):
            self.artifact.update(current_revision_no=params[0], enabled=1, source_manifest_json=params[1])
        else:
            raise AssertionError(sql)


def test_database_store_active_pointer_scope_and_atomic_updates():
    db = FakeDatabase()
    store = DatabaseSkillCandidateStoreService(connection_factory=db.connect)
    assert store.list_available(owner_ids=["alice"]) == []
    assert db.revision_reads == 0
    with pytest.raises(SkillCandidateNotFoundError):
        store.activate_candidate("personal-research", owner_id="bob", expected_candidate_revision=1, expected_active_revision=0)
    active = store.activate_candidate("personal-research", owner_id="alice",
        expected_candidate_revision=1, expected_active_revision=0)
    assert active["published"] and active["visibility"] == "private"
    assert active["references"] == {"references/method.md": "Reference one"}
    count = db.revision_reads
    assert store.list_available(owner_ids=["bob"]) == []
    assert db.revision_reads == count
    assert store.list_available(owner_ids=["alice"])[0]["revision_no"] == 1
    with pytest.raises(SkillCandidateConflictError):
        store.activate_candidate("personal-research", owner_id="alice", expected_candidate_revision=1, expected_active_revision=0)
    store.set_visibility("personal-research", owner_id="alice", visibility="public", expected_active_revision=1)
    assert store.list_available(owner_ids=["bob"])[0]["revision_no"] == 1
    assert db.commits == 2 and db.rollbacks == 2
    assert any("FOR UPDATE" in sql for sql, _ in db.sql)
    assert any("JSON_EXTRACT" in sql for sql, _ in db.sql)
