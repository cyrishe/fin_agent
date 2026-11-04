from __future__ import annotations

import pytest

from src.experiments.staged_data_protocol.phase2 import (
    base_info_provider,
    constitution_provider,
    dynamic_cal_provider,
    financial_provider,
    hot_event_provider,
    intraday_quote_provider,
    margin_provider,
    moneyflow_provider,
    pricevalue_provider,
    quote_provider,
    stock_corporate_provider,
)


@pytest.mark.parametrize(
    "filter_re",
    [
        base_info_provider.FILTER_RE,
        constitution_provider.FILTER_RE,
        financial_provider.FILTER_RE,
        hot_event_provider.FILTER_RE,
        intraday_quote_provider.FILTER_RE,
        margin_provider.FILTER_RE,
        moneyflow_provider.FILTER_RE,
        pricevalue_provider.FILTER_RE,
        quote_provider.FILTER_RE,
        stock_corporate_provider.FILTER_RE,
    ],
)
def test_double_equals_is_not_split_into_equals_plus_value(filter_re) -> None:
    match = filter_re.search("code == 600519.SH")

    assert match is not None
    assert match.group("op") == "=="
    assert match.group("value").strip() == "600519.SH"


def test_dynamic_filter_parser_preserves_double_equals() -> None:
    assert dynamic_cal_provider._simple_filter_items("code == 600519.SH") == [
        ("code", "==", "600519.SH")
    ]
