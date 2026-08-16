import json
from pathlib import Path

from src.scenarios.financial_qa.business_skills import FinanceBusinessSkillCatalog
from src.services.skill_hub_catalog_service import SkillHubCatalogService
from src.services.skill_studio_service import SkillStudioService


def _write_business_skill(root: Path, *, name: str) -> None:
    skill_dir = root / "skills" / name
    (root / ".claude-plugin").mkdir(parents=True)
    skill_dir.mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "finance-test", "version": "1"}),
        encoding="utf-8",
    )
    (root / "catalog.json").write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "id": name,
                        "category": "equity-research",
                        "path": f"skills/{name}",
                        "description": "Finance CC research method",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                f"name: {name}",
                "description: Finance CC research method",
                "allowed-tools:",
                "  - mcp__finance__financial_news_search",
                "---",
                "",
                "# Research method",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (skill_dir / "agents").mkdir()
    (skill_dir / "agents" / "openai.yaml").write_text(
        "\n".join(
            [
                "interface:",
                '  display_name: "Test Research"',
                '  short_description: "Readable research method"',
                '  default_prompt: "Use this research method."',
            ]
        ),
        encoding="utf-8",
    )
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "method.md").write_text(
        "# Test Method\n\nReference details.\n",
        encoding="utf-8",
    )


def _write_legacy_skill(
    root: Path,
    *,
    name: str,
    auth: str = "public",
    status: str = "active",
    lifecycle: str = "active",
    visibility: str = "visible",
) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Legacy method\n", encoding="utf-8")
    (skill_dir / "skill.json").write_text(
        json.dumps(
            {
                "status": status,
                "purpose": "Legacy compiled method",
                "owner": "system",
                "auth": auth,
                "availability": {
                    "lifecycle": lifecycle,
                    "retrieval_mode": "direct_only",
                    "visibility": visibility,
                },
                "tool_policy": {"mode": "strict"},
                "tools": [],
            }
        ),
        encoding="utf-8",
    )
    (skill_dir / "schema.json").write_text(
        json.dumps({"type": "object", "properties": {}}),
        encoding="utf-8",
    )


def test_hub_keeps_discovery_unified_and_execution_semantics_separate(
    tmp_path: Path,
) -> None:
    shared_name = "stock-research"
    business_root = tmp_path / "finance-business"
    legacy_root = tmp_path / "legacy"
    _write_business_skill(business_root, name=shared_name)
    _write_legacy_skill(legacy_root, name=shared_name)
    _write_legacy_skill(legacy_root, name="internal-research", auth="internal")
    _write_legacy_skill(legacy_root, name="draft-research", status="draft")
    _write_legacy_skill(
        legacy_root,
        name="hidden-research",
        visibility="hidden",
    )
    business_catalog = FinanceBusinessSkillCatalog(
        root=business_root,
        snapshot_root=tmp_path / "runtime",
    )
    legacy_studio = SkillStudioService(skills_root=str(legacy_root))
    service = SkillHubCatalogService(
        business_catalog=business_catalog,
        legacy_skill_studio=legacy_studio,
    )

    catalog = service.catalog()

    assert len(catalog["revision"]) == 64
    assert catalog["business_revision"] == business_catalog.revision
    assert [item["skill_name"] for item in catalog["items"]] == [
        shared_name,
        "draft-research",
        "hidden-research",
        shared_name,
    ]
    assert [item["skill_type"] for item in catalog["items"]] == [
        "business_method",
        "legacy_compiled",
        "legacy_compiled",
        "legacy_compiled",
    ]
    business, draft, hidden, legacy = catalog["items"]
    assert business["catalog_id"] == "skill:business_method:stock-research"
    assert business["invocation_mode"] == "finance_cc_preference"
    assert business["invocation_enabled"] is False
    assert business["editable"] is False
    assert business["display_name"] == "Test Research"
    assert business["workspace_url"].startswith("/skills/studio/stock-research")
    assert business["tools"] == ["mcp__finance__financial_news_search"]
    assert legacy["invocation_mode"] == "legacy_runner"
    assert legacy["invocation_enabled"] is True
    assert legacy["catalog_id"] == "skill:legacy_compiled:stock-research"
    assert legacy["editable"] is False
    assert legacy["workspace_url"].startswith("/skills/studio/stock-research")
    assert draft["invocation_enabled"] is False
    assert hidden["invocation_enabled"] is False
    assert [
        item["skill_name"] for item in legacy_studio.list_skills()
    ] == [
        "draft-research",
        "hidden-research",
        "internal-research",
        shared_name,
    ]

    detail = service.detail(
        shared_name,
        catalog_id="skill:business_method:stock-research",
    )
    assert detail is not None
    assert detail["display_name"] == "Test Research"
    assert detail["detail_available"] is True
    assert detail["controls"] == {
        "execution_budget": "standard",
        "supplemental_tools": ["mcp__finance__financial_news_search"],
        "web_search_enabled": False,
    }
    assert detail["references"][0]["title"] == "Test Method"
    reference = service.load_business_reference(
        shared_name,
        "references/method.md",
        expected_revision=detail["revision"],
    )
    assert reference["content"] == "# Test Method\n\nReference details.\n"


def test_hub_business_rows_follow_only_explicit_snapshot_reload(
    tmp_path: Path,
) -> None:
    business_root = tmp_path / "finance-business"
    legacy_root = tmp_path / "legacy"
    _write_business_skill(business_root, name="stock-research")
    legacy_root.mkdir()
    business_catalog = FinanceBusinessSkillCatalog(
        root=business_root,
        snapshot_root=tmp_path / "runtime",
    )
    service = SkillHubCatalogService(
        business_catalog=business_catalog,
        legacy_skill_studio=SkillStudioService(skills_root=str(legacy_root)),
    )
    first = service.catalog()
    skill_file = business_root / "skills" / "stock-research" / "SKILL.md"
    skill_file.write_text(
        "---\nname: stock-research\ndescription: changed\n---\n\n# Changed\n",
        encoding="utf-8",
    )

    unchanged = service.catalog()
    business_catalog.reload()
    changed = service.catalog()

    assert unchanged["revision"] == first["revision"]
    assert changed["revision"] != first["revision"]
    assert changed["business_revision"] == business_catalog.revision


def test_skill_hub_api_uses_read_only_unified_catalog(monkeypatch) -> None:
    from src.web import flask_app as web

    monkeypatch.setattr(
        web.skill_hub_catalog_service,
        "catalog",
        lambda: {
            "revision": "r1",
            "business_revision": "b1",
            "items": [
                {
                    "skill_name": "stock-research",
                    "skill_type": "business_method",
                    "invocation_enabled": False,
                }
            ],
        },
    )
    monkeypatch.setattr(
        web.skill_studio_service,
        "list_skills",
        lambda: [
            {
                "skill_name": "stock_deep_dive",
                "description": "legacy only",
            }
        ],
    )

    response = web.app.test_client().get("/api/skill-hub")
    legacy_response = web.app.test_client().get("/api/skills/catalog")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["revision"] == "r1"
    assert payload["items"][0]["skill_type"] == "business_method"
    assert legacy_response.get_json()["items"] == [
        {
            "skill_name": "stock_deep_dive",
            "description": "legacy only",
        }
    ]


def test_skill_hub_detail_api_is_read_only_and_reference_is_revision_bound(
    monkeypatch,
) -> None:
    from src.web import flask_app as web

    monkeypatch.setattr(
        web.skill_hub_catalog_service,
        "detail",
        lambda skill_name, catalog_id="": {
            "skill_id": skill_name,
            "catalog_id": catalog_id,
            "skill_type": "business_method",
            "skill_markdown": "# Method",
        },
    )
    monkeypatch.setattr(
        web.skill_hub_catalog_service,
        "load_business_reference",
        lambda skill_name, reference_path, expected_revision="": {
            "skill_id": skill_name,
            "reference": reference_path,
            "revision": expected_revision,
            "content": "# Reference",
        },
    )

    client = web.app.test_client()
    detail = client.get(
        "/api/skill-hub/stock-research"
        "?catalog_id=skill%3Abusiness_method%3Astock-research"
    )
    reference = client.get(
        "/api/skill-hub/stock-research/references/references/method.md"
        "?revision=r1"
    )

    assert detail.status_code == 200
    assert detail.get_json()["skill"]["skill_markdown"] == "# Method"
    assert reference.status_code == 200
    assert reference.get_json()["content"] == "# Reference"


def test_chat_skill_catalog_paths_use_the_hub_without_changing_execution(
    monkeypatch,
) -> None:
    from src.web import flask_app as web

    hub_items = [
        {
            "skill_name": "stock-research",
            "skill_type": "business_method",
            "invocation_mode": "finance_cc_preference",
            "invocation_enabled": False,
            "workspace_url": "/skills",
        }
    ]
    monkeypatch.setattr(
        web.skill_hub_catalog_service,
        "list_skills",
        lambda: list(hub_items),
    )

    command_result = web._build_chat_dispatch_payload("/skills")
    semantic_result = web._build_chat_dispatch_payload(
        "查看 Skill 目录",
        precomputed_plan={
            "entry": "catalog_browse",
            "browse_mode": "skills_catalog",
        },
    )

    for result in (command_result, semantic_result):
        assert result["items"] == hub_items
        assert result["workspace"] == {
            "type": "skills_catalog",
            "title": "Skill Hub",
            "url": "/skills",
        }


def test_public_hub_renders_catalog_values_as_text_nodes() -> None:
    template = Path("src/web/templates/skills_catalog.html").read_text(
        encoding="utf-8"
    )

    assert "innerHTML" not in template
    assert "textContent" in template
    assert "replaceChildren" in template

    studio = Path("src/web/templates/skill_studio_v2.html").read_text(
        encoding="utf-8"
    )
    assert "innerHTML" not in studio
    assert "textContent" in studio
    assert "fin_agent.pending_prompt" in studio
    assert "candidate" in studio
