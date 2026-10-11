import json

from src.services.runtime_artifact_service import RuntimeArtifactService


def _write_skill(root, *, lifecycle: str) -> None:
    skill_dir = root / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Demo\n", encoding="utf-8")
    (skill_dir / "schema.json").write_text("{}\n", encoding="utf-8")
    (skill_dir / "skill.json").write_text(
        json.dumps(
            {
                "purpose": "demo",
                "availability": {
                    "lifecycle": lifecycle,
                    "retrieval_mode": "direct_only",
                },
            }
        ),
        encoding="utf-8",
    )


def test_runtime_artifact_sync_disables_retired_skill(tmp_path, monkeypatch):
    _write_skill(tmp_path, lifecycle="retired")
    service = RuntimeArtifactService(skills_root=tmp_path)
    captured = {}
    monkeypatch.setattr(
        service,
        "_upsert_artifact",
        lambda payload: captured.update(payload)
        or {"artifact_id": 7, "current_revision_no": 1},
    )
    monkeypatch.setattr(service, "_replace_edges", lambda **_kwargs: None)

    service.sync_skill("demo")

    assert captured["status"] == "deprecated"
    assert captured["enabled"] == 0


def test_runtime_artifact_sync_keeps_active_skill_enabled(tmp_path, monkeypatch):
    _write_skill(tmp_path, lifecycle="active")
    service = RuntimeArtifactService(skills_root=tmp_path)
    captured = {}
    monkeypatch.setattr(
        service,
        "_upsert_artifact",
        lambda payload: captured.update(payload)
        or {"artifact_id": 8, "current_revision_no": 1},
    )
    monkeypatch.setattr(service, "_replace_edges", lambda **_kwargs: None)

    service.sync_skill("demo")

    assert captured["status"] == "active"
    assert captured["enabled"] == 1
