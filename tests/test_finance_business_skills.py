import json
import re
from pathlib import Path

from src.scenarios.financial_qa.service import FinancialQaCcService


ROOT = Path("src/skills/finance-business")
CATALOG_PATH = ROOT / "catalog.json"


def _catalog() -> dict:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def _frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"^---\n(.*?)\n---\n", text, flags=re.DOTALL)
    assert match, "SKILL.md must start with YAML frontmatter"
    values: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, separator, value = line.partition(":")
        if separator:
            values[key.strip()] = value.strip()
    return values


def test_finance_business_catalog_has_the_grounded_business_skills() -> None:
    catalog = _catalog()
    items = catalog["skills"]

    assert catalog["version"] == "v1"
    assert [item["id"] for item in items] == [
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
    assert all(
        set(item) == {"id", "category", "path", "description"}
        for item in items
    )


def test_catalog_entries_point_to_standard_progressive_skills() -> None:
    for item in _catalog()["skills"]:
        skill_dir = ROOT / item["path"]
        skill_path = skill_dir / "SKILL.md"

        assert skill_path.exists()
        assert not (skill_dir / "schema.json").exists()
        assert not (skill_dir / "skill.json").exists()

        skill_text = skill_path.read_text(encoding="utf-8")
        metadata = _frontmatter(skill_text)
        assert metadata["name"] == item["id"]
        assert len(metadata["description"]) >= 30
        assert "## 工作方法" in skill_text
        assert "## 数据需求" in skill_text
        assert "## 工具与证据" in skill_text
        assert "## 回答要求" in skill_text


def test_business_skill_methods_are_decoupled_from_concrete_finance_api_names() -> None:
    api_call_pattern = re.compile(
        r"\b(?:stock|index|industry|plate|fund|bond|hot_event)\.[a-z_]"
    )
    for item in _catalog()["skills"]:
        skill_text = (ROOT / item["path"] / "SKILL.md").read_text(encoding="utf-8")
        assert not api_call_pattern.search(skill_text)


def test_method_references_record_public_implementation_basis() -> None:
    reference_count = 0
    for item in _catalog()["skills"]:
        method_path = ROOT / item["path"] / "references" / "method.md"
        if not method_path.exists():
            continue
        reference_count += 1
        method_text = method_path.read_text(encoding="utf-8")
        assert "## 方法来源" in method_text
        assert "https://github.com/" in method_text
    assert reference_count > 0


def test_financial_qa_cc_loads_only_the_new_business_skill_plugin() -> None:
    service = FinancialQaCcService(enabled=True)

    assert service.session_service.skill_root == ROOT
    assert service.session_service.skill_names == (
        "fin-agent-finance-business:market-overview",
        "fin-agent-finance-business:sector-theme-analysis",
        "fin-agent-finance-business:stock-research",
        "fin-agent-finance-business:earnings-analysis",
        "fin-agent-finance-business:stock-screening",
        "fin-agent-finance-business:factor-analysis",
        "fin-agent-finance-business:valuation-analysis",
        "fin-agent-finance-business:financial-quality-analysis",
        "fin-agent-finance-business:stock-comparison",
        "fin-agent-finance-business:technical-structure-analysis",
        "fin-agent-finance-business:dividend-analysis",
    )
    plugin = json.loads(
        (ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    assert plugin["name"] == "fin-agent-finance-business"
