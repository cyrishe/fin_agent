import json
from pathlib import Path

import pytest

from src.scenarios.financial_qa.business_skills import FinanceBusinessSkillCatalog
from src.services.agent_runtime_service import AgentRuntimeService


EXPECTED_SKILLS = [
    "market-overview",
    "sector-theme-analysis",
    "stock-research",
    "earnings-analysis",
    "stock-screening",
    "factor-analysis",
    "valuation-analysis",
    "financial-quality-analysis",
    "stock-comparison",
    "technical-structure-analysis",
    "dividend-analysis",
]


def _build_snapshot_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "finance-business"
    skill_dir = root / "skills" / "test-research"
    references_dir = skill_dir / "references"
    agents_dir = skill_dir / "agents"
    scripts_dir = skill_dir / "scripts"
    (root / ".claude-plugin").mkdir(parents=True)
    references_dir.mkdir(parents=True)
    agents_dir.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "finance-test"}),
        encoding="utf-8",
    )
    (root / "catalog.json").write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "id": "test-research",
                        "category": "stock",
                        "path": "skills/test-research",
                        "description": "test research method",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(
        "\n".join(
            [
                "---",
                "name: test-research",
                "description: test research method",
                "allowed-tools:",
                "  - mcp__finance__financial_news_search",
                "---",
                "",
                "# Original method",
                "",
            ]
        ),
        encoding="utf-8",
    )
    reference_path = references_dir / "method.md"
    reference_path.write_text("original reference", encoding="utf-8")
    (agents_dir / "openai.yaml").write_text(
        "interface:\n  display_name: Test research\n",
        encoding="utf-8",
    )
    (scripts_dir / "run.py").write_text(
        "raise RuntimeError('must not be packaged')\n",
        encoding="utf-8",
    )
    return root, skill_path, reference_path


def test_finance_business_catalog_is_the_runtime_skill_source() -> None:
    catalog = FinanceBusinessSkillCatalog()

    entries = catalog.public_entries()

    assert [item["id"] for item in entries] == EXPECTED_SKILLS
    assert catalog.qualified_skill_names() == [
        f"fin-agent-finance-business:{skill_id}"
        for skill_id in EXPECTED_SKILLS
    ]
    assert all(item["description"] for item in entries)
    assert all(
        set(item)
        == {"id", "category", "path", "description", "execution_budget"}
        for item in entries
    )
    assert next(
        item for item in entries if item["id"] == "stock-research"
    )["execution_budget"] == "long"
    assert all(
        item["execution_budget"] == "standard"
        for item in entries
        if item["id"] != "stock-research"
    )


def test_finance_business_catalog_skips_unavailable_or_unauthorized_skills(
    tmp_path: Path,
) -> None:
    root = tmp_path / "finance-business"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / "skills" / "available").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "finance-test"}),
        encoding="utf-8",
    )
    (root / "catalog.json").write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "id": "available",
                        "path": "skills/available",
                        "description": "available skill",
                    },
                    {
                        "id": "missing",
                        "path": "skills/missing",
                        "description": "missing skill",
                    },
                    {
                        "id": "escape",
                        "path": "../escape",
                        "description": "invalid path",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    (root / "skills" / "available" / "SKILL.md").write_text(
        "---\nname: available\ndescription: available skill\n---\n",
        encoding="utf-8",
    )
    catalog = FinanceBusinessSkillCatalog(root=root)

    assert catalog.qualified_skill_names(
        allowed_skill_ids=["available", "missing", "escape"]
    ) == ["finance-test:available"]
    assert catalog.qualified_skill_names(
        allowed_skill_ids=["missing"]
    ) == []


def test_catalog_rejects_an_unknown_execution_budget(tmp_path: Path) -> None:
    root, skill_path, _ = _build_snapshot_fixture(tmp_path)
    skill_path.write_text(
        skill_path.read_text(encoding="utf-8").replace(
            "allowed-tools:\n",
            "execution-budget: unlimited\nallowed-tools:\n",
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="execution-budget"):
        FinanceBusinessSkillCatalog(root=root, snapshot_root=tmp_path / "runtime")


def test_catalog_request_path_uses_only_the_immutable_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root, _, _ = _build_snapshot_fixture(tmp_path)
    catalog = FinanceBusinessSkillCatalog(
        root=root,
        snapshot_root=tmp_path / "runtime",
    )

    def _unexpected_read(*_args, **_kwargs):
        raise AssertionError("runtime catalog path must not read files")

    monkeypatch.setattr(Path, "read_text", _unexpected_read)
    monkeypatch.setattr(Path, "read_bytes", _unexpected_read)

    assert catalog.qualified_skill_names() == ["finance-test:test-research"]
    assert catalog.routing_summary() == "- test-research: test research method"
    assert catalog.load("test-research")["method"].endswith("# Original method")
    assert catalog.load_reference(
        "test-research",
        "references/method.md",
    ) == {
        "skill_id": "test-research",
        "reference": "references/method.md",
        "revision": catalog.revision,
        "content_hash": catalog.snapshot_metadata()["skills"][0][
            "reference_index"
        ][0]["content_hash"],
        "content": "original reference",
    }
    assert catalog.allowed_tools_by_skill() == {
        "test-research": ["mcp__finance__financial_news_search"]
    }
    assert catalog.runtime_binding() == {
        "revision": catalog.revision,
        "runtime_root": catalog.runtime_root,
        "skill_names": ["finance-test:test-research"],
    }
    catalog.validate_runtime_binding(catalog.runtime_binding())
    assert catalog.discovery_snapshot()["entries"][0]["allowed_tools"] == [
        "mcp__finance__financial_news_search"
    ]


def test_catalog_reference_lookup_is_exact_and_permission_scoped(
    tmp_path: Path,
) -> None:
    root, _, _ = _build_snapshot_fixture(tmp_path)
    catalog = FinanceBusinessSkillCatalog(
        root=root,
        snapshot_root=tmp_path / "runtime",
    )

    assert "error" not in catalog.load_reference(
        "test-research",
        "references/method.md",
        allowed_skill_ids=["test-research"],
    )
    assert "未授权" in catalog.load_reference(
        "test-research",
        "references/method.md",
        allowed_skill_ids=[],
    )["error"]
    assert "路径无效" in catalog.load_reference(
        "test-research",
        "../SKILL.md",
    )["error"]

    previous_revision = catalog.revision
    (root / "skills" / "test-research" / "references" / "method.md").write_text(
        "updated reference",
        encoding="utf-8",
    )
    catalog.reload()
    assert catalog.revision != previous_revision
    assert "快照已更新" in catalog.load_reference(
        "test-research",
        "references/method.md",
        expected_revision=previous_revision,
    )["error"]
    assert catalog.turn_snapshot(allowed_skill_ids=["test-research"]) == {
        "revision": catalog.revision,
        "runtime_root": catalog.runtime_root,
        "skill_names": ["finance-test:test-research"],
        "routing_summary": "- test-research: test research method",
        "allowed_tools_by_skill": {
            "test-research": ["mcp__finance__financial_news_search"]
        },
        "execution_budget_by_skill": {"test-research": "standard"},
    }


def test_catalog_changes_only_after_explicit_snapshot_reload(tmp_path: Path) -> None:
    root, skill_path, reference_path = _build_snapshot_fixture(tmp_path)
    companion_path = (
        root / "skills" / "test-research" / "agents" / "openai.yaml"
    )
    catalog = FinanceBusinessSkillCatalog(
        root=root,
        snapshot_root=tmp_path / "runtime",
    )
    initial_revision = catalog.revision
    initial_metadata = catalog.snapshot_metadata()
    initial_runtime_root = catalog.runtime_root

    assert initial_runtime_root != root
    assert (
        initial_runtime_root / "skills" / "test-research" / "SKILL.md"
    ).read_text(encoding="utf-8").endswith("# Original method\n")
    assert (
        initial_runtime_root
        / "skills"
        / "test-research"
        / "references"
        / "method.md"
    ).read_text(encoding="utf-8") == "original reference"
    assert (
        initial_runtime_root
        / "skills"
        / "test-research"
        / "agents"
        / "openai.yaml"
    ).read_text(encoding="utf-8").startswith("interface:")
    assert not (
        initial_runtime_root
        / "skills"
        / "test-research"
        / "scripts"
        / "run.py"
    ).exists()

    skill_path.write_text(
        "---\nname: test-research\ndescription: changed\n---\n\n# Changed method\n",
        encoding="utf-8",
    )
    reference_path.write_text("changed reference", encoding="utf-8")
    companion_path.write_text(
        "interface:\n  display_name: Changed research\n",
        encoding="utf-8",
    )

    assert catalog.revision == initial_revision
    assert catalog.load("test-research")["method"].endswith("# Original method")

    reloaded = catalog.reload()

    assert reloaded["revision"] != initial_revision
    assert catalog.runtime_root != initial_runtime_root
    assert catalog.load("test-research")["method"].endswith("# Changed method")
    assert (
        initial_runtime_root / "skills" / "test-research" / "SKILL.md"
    ).read_text(encoding="utf-8").endswith("# Original method\n")
    assert (
        catalog.runtime_root / "skills" / "test-research" / "SKILL.md"
    ).read_text(encoding="utf-8").endswith("# Changed method\n")
    assert reloaded["skills"][0]["content_hash"] != (
        initial_metadata["skills"][0]["content_hash"]
    )
    assert reloaded["skills"][0]["reference_index"][0]["content_hash"] != (
        initial_metadata["skills"][0]["reference_index"][0]["content_hash"]
    )
    assert reloaded["skills"][0]["companion_index"][0]["content_hash"] != (
        initial_metadata["skills"][0]["companion_index"][0]["content_hash"]
    )
    assert reloaded["skills"][0]["allowed_tools"] == []


def test_snapshot_does_not_materialize_reference_symlink_outside_skill(
    tmp_path: Path,
) -> None:
    root, _, _ = _build_snapshot_fixture(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("must not be copied", encoding="utf-8")
    escaped_reference = (
        root / "skills" / "test-research" / "references" / "escape.md"
    )
    escaped_reference.symlink_to(outside)

    catalog = FinanceBusinessSkillCatalog(
        root=root,
        snapshot_root=tmp_path / "runtime",
    )

    reference_paths = {
        item["path"]
        for item in catalog.snapshot_metadata()["skills"][0]["reference_index"]
    }
    assert "references/method.md" in reference_paths
    assert "references/escape.md" not in reference_paths
    assert not (
        catalog.runtime_root
        / "skills"
        / "test-research"
        / "references"
        / "escape.md"
    ).exists()


def test_existing_runtime_snapshot_with_content_drift_is_rejected(
    tmp_path: Path,
) -> None:
    root, _, _ = _build_snapshot_fixture(tmp_path)
    snapshot_root = tmp_path / "runtime"
    catalog = FinanceBusinessSkillCatalog(
        root=root,
        snapshot_root=snapshot_root,
    )
    runtime_skill = (
        catalog.runtime_root / "skills" / "test-research" / "SKILL.md"
    )
    assert runtime_skill.stat().st_mode & 0o222 == 0
    runtime_skill.chmod(0o644)
    runtime_skill.write_text("tampered", encoding="utf-8")

    with pytest.raises(RuntimeError, match="invalid Finance Skill runtime snapshot"):
        FinanceBusinessSkillCatalog(
            root=root,
            snapshot_root=snapshot_root,
        )


def test_invalid_reload_keeps_the_last_valid_runtime_binding(
    tmp_path: Path,
) -> None:
    root, _, _ = _build_snapshot_fixture(tmp_path)
    catalog = FinanceBusinessSkillCatalog(
        root=root,
        snapshot_root=tmp_path / "runtime",
    )
    initial_binding = catalog.runtime_binding()
    (root / "catalog.json").write_text("{invalid", encoding="utf-8")

    with pytest.raises(RuntimeError, match="invalid Finance Skill catalog"):
        catalog.reload()

    assert catalog.runtime_binding() == initial_binding


def test_catalog_byte_change_gets_a_new_content_addressed_snapshot(
    tmp_path: Path,
) -> None:
    root, _, _ = _build_snapshot_fixture(tmp_path)
    catalog = FinanceBusinessSkillCatalog(
        root=root,
        snapshot_root=tmp_path / "runtime",
    )
    initial_binding = catalog.runtime_binding()
    catalog_payload = json.loads(
        (root / "catalog.json").read_text(encoding="utf-8")
    )
    (root / "catalog.json").write_text(
        json.dumps(catalog_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    catalog.reload()

    assert catalog.revision != initial_binding["revision"]
    assert catalog.runtime_root != initial_binding["runtime_root"]


def test_frontmatter_identity_mismatch_does_not_replace_active_snapshot(
    tmp_path: Path,
) -> None:
    root, skill_path, _ = _build_snapshot_fixture(tmp_path)
    catalog = FinanceBusinessSkillCatalog(
        root=root,
        snapshot_root=tmp_path / "runtime",
    )
    initial_binding = catalog.runtime_binding()
    skill_path.write_text(
        "---\nname: another-skill\ndescription: wrong identity\n---\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="frontmatter name mismatch"):
        catalog.reload()

    assert catalog.runtime_binding() == initial_binding


def test_investment_agent_uses_catalog_ids_as_allowlist() -> None:
    context = AgentRuntimeService().get_agent_context("investment_analyst")
    runtime_profile = context["runtime_profile"]

    assert runtime_profile["skills"] == EXPECTED_SKILLS
    assert "stock_deep_dive" not in runtime_profile["skills"]


def test_business_skills_keep_method_soft_and_final_control_with_cc() -> None:
    root = Path("src/skills/finance-business/skills")

    for skill_id in EXPECTED_SKILLS:
        text = (root / skill_id / "SKILL.md").read_text(encoding="utf-8")
        assert f"name: {skill_id}" in text
        assert "## 数据需求" in text
        assert "## 回答要求" in text
        assert "success" not in text
        assert "active_skill" not in text
        assert "Output Schema" not in text
