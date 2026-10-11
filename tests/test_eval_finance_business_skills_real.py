from scripts.eval_finance_business_skills_real import (
    _case_research_mode,
    _tool_metrics,
)


def test_tool_metrics_separate_discovery_queries_and_recovery_signals() -> None:
    metrics = _tool_metrics(
        [
            {"tool": "read_finance_skill_reference"},
            {"tool": "read_finance_catalog"},
            {"tool": "read_finance_catalog"},
            {"tool": "finance_query", "row_count": 3},
            {"tool": "finance_query", "row_count": 0},
            {"tool": "finance_query", "validation_errors": ["bad field"]},
            {"tool": "load_finance_result"},
            {"tool": "financial_news_search"},
        ]
    )

    assert metrics == {
        "catalog_read_count": 2,
        "reference_read_count": 1,
        "finance_query_count": 3,
        "zero_row_query_count": 1,
        "validation_error_query_count": 1,
        "result_load_count": 1,
        "supplemental_tool_count": 1,
    }


def test_case_research_mode_preserves_explicit_choice_and_defaults_to_auto() -> None:
    assert _case_research_mode({"research_mode": "fast"}) == "fast"
    assert _case_research_mode({"research_mode": "deep"}) == "deep"
    assert _case_research_mode({}) == "auto"
