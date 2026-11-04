from __future__ import annotations

from src.experiments.staged_data_protocol.phase2.financial_provider import (
    STOCK_FINANCIAL_SOURCE,
    _build_where,
    _explicit_filters,
)


def test_financial_query_without_period_keeps_latest_period_default() -> None:
    where_sql, params = _build_where(
        source=STOCK_FINANCIAL_SOURCE,
        args={"filter": "code = 600519.SH"},
    )

    assert "i.report_period = (SELECT MAX(report_period)" in where_sql
    assert "i.stk_code = %s" in where_sql
    assert params == ["HB", "600519.SH"]


def test_explicit_financial_period_range_replaces_latest_period_default() -> None:
    where_sql, params = _build_where(
        source=STOCK_FINANCIAL_SOURCE,
        args={
            "filter": (
                "code = 600519.SH and report_period >= 2021-12-31 "
                "and report_period <= 2025-12-31"
            )
        },
    )

    assert "SELECT MAX(report_period)" not in where_sql
    assert "i.stk_code = %s AND i.report_period >= %s AND i.report_period <= %s" in where_sql
    assert params == ["HB", "600519.SH", "2021-12-31", "2025-12-31"]


def test_grouped_same_period_or_preserves_stock_scope_as_in_filter() -> None:
    args = {
        "filter": (
            "code = 600519.SH and (report_period = 2023-12-31 "
            "or report_period = 2024-12-31 or report_period = 2025-12-31)"
        )
    }

    filters = _explicit_filters(source=STOCK_FINANCIAL_SOURCE, args=args)
    where_sql, params = _build_where(source=STOCK_FINANCIAL_SOURCE, args=args)

    assert filters == [
        ("AND", "code", "=", "600519.SH"),
        (
            "AND",
            "report_period",
            "in",
            "[2023-12-31, 2024-12-31, 2025-12-31]",
        ),
    ]
    assert "SELECT MAX(report_period)" not in where_sql
    assert "i.stk_code = %s AND i.report_period IN (%s, %s, %s)" in where_sql
    assert params == [
        "HB",
        "600519.SH",
        "2023-12-31",
        "2024-12-31",
        "2025-12-31",
    ]
