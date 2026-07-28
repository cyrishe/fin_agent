import json
from pathlib import Path

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
        set(item) == {"id", "category", "path", "description"}
        for item in entries
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
        "---\nname: available\n---\n",
        encoding="utf-8",
    )
    catalog = FinanceBusinessSkillCatalog(root=root)

    assert catalog.qualified_skill_names(
        allowed_skill_ids=["available", "missing", "escape"]
    ) == ["finance-test:available"]
    assert catalog.qualified_skill_names(
        allowed_skill_ids=["missing"]
    ) == []


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
