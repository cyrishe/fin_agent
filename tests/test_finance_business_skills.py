import json
import re
from pathlib import Path

import pytest
from selenium.common.exceptions import WebDriverException

from src.crawler.site_news_crawler import SiteNewsCrawler
from src.scenarios.financial_qa.business_skills import FinanceBusinessSkillCatalog
from src.scenarios.financial_qa.service import FinancialQaCcService
from src.tools.company_news_tool import (
    CompanyNewsTool,
    DEFAULT_CONFIG_PATH,
    DEFAULT_NEWS_SITES,
)


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


def test_native_skill_frontmatter_declares_only_needed_supplementary_tools() -> None:
    access = FinanceBusinessSkillCatalog(root=ROOT).allowed_tools_by_skill()

    assert access["market-overview"] == [
        "mcp__finance__financial_news_search"
    ]
    assert access["sector-theme-analysis"] == [
        "mcp__finance__financial_news_search"
    ]
    assert access["stock-research"] == [
        "mcp__finance__financial_news_search"
    ]
    assert access["earnings-analysis"] == []
    assert access["stock-screening"] == []


def test_financial_news_tool_ships_its_required_site_configuration() -> None:
    config_path = Path(DEFAULT_CONFIG_PATH)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    configured_names = {
        str(item.get("name") or "").strip()
        for item in payload.get("sites") or []
        if isinstance(item, dict)
    }

    assert set(DEFAULT_NEWS_SITES) <= configured_names


def test_financial_news_tool_does_not_turn_total_provider_failure_into_zero_rows(
    monkeypatch,
) -> None:
    class _Crawler:
        sites = [object(), object()]
        last_search_errors = {
            "site-a": "browser unavailable",
            "site-b": "browser unavailable",
        }

        @staticmethod
        def crawl(**kwargs):
            return []

    tool = CompanyNewsTool()
    monkeypatch.setattr(
        tool,
        "_resolve_query",
        lambda query, entity_type: {"query": query},
    )
    monkeypatch.setattr(tool, "_load_cached_items", lambda **kwargs: [])
    monkeypatch.setattr(tool, "_build_crawler", lambda **kwargs: _Crawler())

    with pytest.raises(RuntimeError, match="金融新闻检索站点全部执行失败"):
        tool.search(query="贵州茅台", db=object())


def test_crawler_falls_back_to_managed_driver_when_path_driver_is_broken(
    monkeypatch,
) -> None:
    class _Driver:
        timeout = None

        def set_page_load_timeout(self, timeout):
            self.timeout = timeout

    class _Service:
        def __init__(self, path):
            self.path = path

    calls = []

    def _chrome(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise WebDriverException("broken PATH driver")
        return _Driver()

    monkeypatch.setattr(
        "src.crawler.site_news_crawler.webdriver.Chrome",
        _chrome,
    )
    monkeypatch.setattr(
        "src.crawler.site_news_crawler.SeleniumManager.binary_paths",
        lambda self, args: {
            "driver_path": "/tmp/managed-chromedriver",
            "browser_path": "/Applications/Google Chrome.app",
        },
    )
    monkeypatch.setattr(
        "src.crawler.site_news_crawler.ChromeService",
        _Service,
    )

    driver = SiteNewsCrawler(sites=[])._create_driver()

    assert driver.timeout == 30
    assert len(calls) == 2
    assert calls[1]["service"].path == "/tmp/managed-chromedriver"


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


def test_shipped_legacy_compiled_skills_are_offline() -> None:
    for skill_name in ("stock_deep_dive", "quant_factor_screening"):
        config = json.loads(
            (Path("src/skills") / skill_name / "skill.json").read_text(
                encoding="utf-8"
            )
        )
        assert config["availability"] == {
            "lifecycle": "retired",
            "retrieval_mode": "direct_only",
        }


def test_stock_research_is_an_adaptive_lead_skill_with_progressive_references() -> None:
    skill_dir = ROOT / "skills" / "stock-research"
    skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    reference_names = {
        path.name for path in (skill_dir / "references").glob("*.md")
    }

    assert {
        "method.md",
        "report-template.md",
        "industry-lenses.md",
        "attention-and-catalysts.md",
        "expectation-gap-and-redteam.md",
        "personalization.md",
    } <= reference_names
    assert "研究地图" in skill_text
    assert "不形成另一套固定工作流" in skill_text
    assert "read_finance_skill_reference" in skill_text
    assert "valuation-analysis" in skill_text
    assert "sector-theme-analysis" in skill_text
    assert "价格可能隐含的预期" in skill_text
    assert "expectation-gap-and-redteam.md" in skill_text
    assert "无法解释权重的综合评分" in skill_text


def test_financial_qa_cc_loads_only_the_new_business_skill_plugin() -> None:
    service = FinancialQaCcService(enabled=True)

    assert service.session_service.skill_root == service.business_skill_catalog.runtime_root
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
        (
            service.business_skill_catalog.runtime_root
            / ".claude-plugin"
            / "plugin.json"
        ).read_text(encoding="utf-8")
    )
    assert plugin["name"] == "fin-agent-finance-business"
