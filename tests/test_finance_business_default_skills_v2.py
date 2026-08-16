import json
import re
from pathlib import Path

import yaml


ROOT = Path("src/skills/finance-business")
CATALOG_PATH = ROOT / "catalog.json"
EVAL_PATH = Path("tests/evals/finance_business_default_skills_v2.json")
DEFAULT_SKILL_IDS = {
    "market-overview",
    "sector-theme-analysis",
    "earnings-analysis",
    "stock-screening",
    "factor-analysis",
    "valuation-analysis",
    "financial-quality-analysis",
    "stock-comparison",
    "technical-structure-analysis",
    "dividend-analysis",
}


def _catalog_items() -> list[dict]:
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return [
        item for item in payload["skills"] if item["id"] in DEFAULT_SKILL_IDS
    ]


def _skill_text(skill_id: str) -> str:
    return (ROOT / "skills" / skill_id / "SKILL.md").read_text(encoding="utf-8")


def _frontmatter(text: str) -> dict:
    match = re.match(r"^---\n(.*?)\n---\n", text, flags=re.DOTALL)
    assert match
    payload = yaml.safe_load(match.group(1))
    assert isinstance(payload, dict)
    return payload


def test_default_skill_catalog_and_frontmatter_share_one_trigger_description() -> None:
    items = _catalog_items()

    assert {item["id"] for item in items} == DEFAULT_SKILL_IDS
    for item in items:
        metadata = _frontmatter(_skill_text(item["id"]))
        assert metadata["name"] == item["id"]
        assert metadata["description"] == item["description"]
        assert "时使用" in metadata["description"]
        assert "不使用" in metadata["description"]
        assert set(metadata) <= {"name", "description", "allowed-tools"}


def test_default_skills_keep_tool_work_outside_the_natural_language_method() -> None:
    factor = _skill_text("factor-analysis")
    screening = _skill_text("stock-screening")

    assert "确定性 Tool" in factor
    assert "不在模型中临时拼算" in factor
    assert "单次分析不能替代" in factor
    assert "只有全部必要条件被验证" in screening
    assert "部分条件观察名单" in screening
    assert "Tool/Strategy" in screening


def test_default_skill_references_are_direct_optional_and_complete() -> None:
    for skill_id in DEFAULT_SKILL_IDS:
        skill_dir = ROOT / "skills" / skill_id
        skill_text = _skill_text(skill_id)
        shipped = {
            path.name
            for path in (skill_dir / "references").glob("*.md")
            if not path.name.startswith("._")
        }
        linked = set(
            re.findall(r"\(references/([A-Za-z0-9_.-]+\.md)\)", skill_text)
        )

        assert linked == shipped
        assert "## 按需参考" in skill_text
        assert all(
            "references/" not in path.read_text(encoding="utf-8")
            for path in (skill_dir / "references").glob("*.md")
            if not path.name.startswith("._")
        )


def test_default_skill_ui_metadata_matches_runtime_identity() -> None:
    for skill_id in DEFAULT_SKILL_IDS:
        metadata_path = ROOT / "skills" / skill_id / "agents" / "openai.yaml"
        metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
        interface = metadata["interface"]

        assert 25 <= len(interface["short_description"]) <= 64
        assert f"${skill_id}" in interface["default_prompt"]


def test_default_skill_trigger_matrix_covers_positive_and_boundary_cases() -> None:
    dataset = json.loads(EVAL_PATH.read_text(encoding="utf-8"))
    cases = dataset["cases"]
    positive_ids = {
        case["expected_skill_id"]
        for case in cases
        if case["expected_skill_id"] in DEFAULT_SKILL_IDS
    }

    assert dataset["version"] == "finance_business_default_skills_v2"
    assert positive_ids == DEFAULT_SKILL_IDS
    assert all(case["question"].strip() for case in cases)
    assert all(case["expected_cc_entry"] for case in cases)
    assert any(case["expected_skill_id"] == "stock-research" for case in cases)
    assert sum(case["expected_skill_id"] == "" for case in cases) >= 8
    assert any(
        case["expected_cc_entry"] == "outside_financial_qa" for case in cases
    )
