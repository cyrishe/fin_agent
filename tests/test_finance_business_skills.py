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
        "mcp__finance__general_search"
    ]
    assert access["sector-theme-analysis"] == [
        "mcp__finance__general_search"
    ]
    assert access["stock-research"] == [
        "mcp__finance__general_search"
    ]
    assert FinanceBusinessSkillCatalog(root=ROOT).execution_budget_by_skill()[
        "stock-research"
    ] == "long"
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
        path.name
        for path in (skill_dir / "references").glob("*.md")
        if not path.name.startswith("._")
    }

    assert {
        "research-method.md",
        "report-template.md",
        "company-archetypes.md",
        "evidence-and-source-policy.md",
        "scoring-and-confidence.md",
        "catalyst-expectation-redteam.md",
        "personalization.md",
    } == reference_names
    assert "核心命题" in skill_text
    assert "固定子 Skill 清单" in skill_text
    assert "篇幅要求同时约束取证" in skill_text
    assert "不得因为界面处于智能模式" in skill_text
    assert "最终综合前必须读取" in skill_text
    assert "新闻或 Web 检索失败只形成事件证据缺口" in skill_text
    assert "不改用另一种新闻或搜索工具重复碰运气" in skill_text
    assert "不得用它们证明报告“够深”" in skill_text
    assert "深度报告以研究链完整性而不是机械页数为目标" in skill_text
    assert "最终回答只输出可供用户阅读和导出的报告正文" in skill_text
    assert "不读取复杂研究方法或完整报告模板" in skill_text
    assert "结构化数据失败或缺失不是转向新闻的理由" in skill_text
    assert "不从模型记忆猜字段后试错" in skill_text
    assert "只按已加载目录修正一次" in skill_text
    assert "read_finance_skill_reference" in skill_text
    assert "research-method.md" in skill_text
    assert "company-archetypes.md" in skill_text
    assert "scoring-and-confidence.md" in skill_text
    assert "catalyst-expectation-redteam.md" in skill_text
    assert "价格可能隐含" in skill_text
    assert "不让模型临时拼出综合分" in skill_text
    assert "只修正该证据目标" in skill_text
    assert "三至六张真正支持决策" in skill_text
    assert "数据充分却仍只有四页左右摘要时" in skill_text
    assert "Sibling Skill" not in skill_text
    assert "depends_on_skill" not in skill_text


def test_stock_research_references_are_direct_focused_and_all_linked() -> None:
    skill_dir = ROOT / "skills" / "stock-research"
    skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    linked_references = set(
        re.findall(r"\(references/([A-Za-z0-9_.-]+\.md)\)", skill_text)
    )
    shipped_references = {
        path.name
        for path in (skill_dir / "references").glob("*.md")
        if not path.name.startswith("._")
    }

    assert linked_references == shipped_references
    assert "一般只读取真正影响判断的一至两份参考" in skill_text
    assert "一次加载全部参考" in skill_text
    assert all("references/" not in path.read_text(encoding="utf-8") for path in (skill_dir / "references").glob("*.md") if not path.name.startswith("._"))


def test_stock_research_uses_named_frameworks_as_progressive_methods_not_skills() -> None:
    skill_dir = ROOT / "skills" / "stock-research"
    skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    research_method = (skill_dir / "references" / "research-method.md").read_text(
        encoding="utf-8"
    )
    report_template = (skill_dir / "references" / "report-template.md").read_text(
        encoding="utf-8"
    )
    catalog_ids = {item["id"] for item in _catalog()["skills"]}

    assert "专业分析框架" in skill_text
    assert "研究深度改变证据预算" in skill_text
    assert "最终回答前做一次证据审计" in skill_text
    assert "Porter 五力" in research_method
    assert "Morningstar Economic Moat" in research_method
    assert "DuPont" in research_method
    assert "Damodaran Fundamental Growth" in research_method
    assert "不按框架分别生成小报告" in research_method
    assert "深度报告的完整性主要来自对既有证据的经营解释" in research_method
    assert "用三条桥把方法落实到报告" in research_method
    assert "才考虑注册为新 Skill" in research_method
    assert "方法、证据与局限附录" in report_template
    assert "框架名词堆砌" in report_template
    assert "稳定导航，不是死板顺序" in report_template
    assert "最终输出从报告标题或核心命题直接开始" in report_template
    assert "大体章节目录" in report_template
    assert "事实陈列规范" in report_template
    assert "理解业务、预测、方法选择" in report_template
    assert "三至六张决策相关表格或图形" in report_template
    evidence_policy = (
        skill_dir / "references" / "evidence-and-source-policy.md"
    ).read_text(encoding="utf-8")
    assert "结论强度与证据匹配" in evidence_policy
    assert "不能单独证明护城河" in evidence_policy
    assert catalog_ids.isdisjoint(
        {"porter-five-forces", "economic-moat", "dupont-analysis", "reverse-dcf"}
    )


def test_stock_research_flagship_eval_is_natural_layered_and_has_boundaries() -> None:
    dataset = json.loads(
        Path("tests/evals/stock_research_flagship_v1.json").read_text(
            encoding="utf-8"
        )
    )
    cases = dataset["cases"]
    depths = {case["research_depth"] for case in cases}
    modes = {case["research_mode"] for case in cases}
    questions = [case["question"] for case in cases]

    assert depths == {"quick", "standard", "deep", "none"}
    assert modes == {"fast", "auto", "deep"}
    assert any(
        case["research_mode"] == "auto" and case["research_depth"] == "standard"
        for case in cases
    )
    assert any(
        case["research_mode"] == "auto" and case["research_depth"] == "none"
        for case in cases
    )
    assert any(
        case["research_mode"] == "auto" and case["research_depth"] == "deep"
        for case in cases
    )
    assert len(cases) == 8
    assert all(case["review_criteria"] for case in cases)
    assert all(case["anti_patterns"] for case in cases)
    assert any(case["expected_skill_id"] == "" for case in cases)
    assert any("证据不够" in question for question in questions)
    assert all("不要套通用综合评分" not in question for question in questions)


def test_stock_research_ui_metadata_matches_the_runtime_skill() -> None:
    skill_dir = ROOT / "skills" / "stock-research"
    metadata = (skill_dir / "agents" / "openai.yaml").read_text(
        encoding="utf-8"
    )
    catalog_item = next(
        item for item in _catalog()["skills"] if item["id"] == "stock-research"
    )
    skill_metadata = _frontmatter(
        (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    )

    assert "$stock-research" in metadata
    assert "反证" in metadata
    assert catalog_item["description"] == skill_metadata["description"]


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
