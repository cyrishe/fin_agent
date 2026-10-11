from __future__ import annotations

import pandas as pd
import pytest

from scripts.report_automl_daily_market_context import (
    build_daily, export_daily, render_markdown, top1_flags,
)


def test_daily_report_uses_only_prior_market_days_and_labeled_positive_signals():
    day = "2026-02-02"
    scores = pd.DataFrame({
        "signal_date": [day] * 4,
        "symbol6": ["000001", "000002", "000003", "000004"],
        "signal_price": [10.0] * 4,
        "predicted_class": ["ge3", "1to3", "1to3", "lt0"],
    })
    candidates = pd.DataFrame({
        "signal_date": [day] * 4,
        "symbol6": scores.symbol6,
        "signal_price": [10.0] * 4,
        "second_high": [10.4, 9.9, None, 11.0],
        "label_complete": [True, True, False, True],
    })
    top1 = pd.DataFrame({"signal_date": [day], "symbol6": ["000001"],
                         "name": ["甲"], "actual_class": ["ge3"],
                         "actual_0940_return": [.02]})
    market = pd.DataFrame({"trade_date": pd.date_range("2026-01-26", periods=6),
                           "up_count": [1, 2, 3, 4, 5, 6],
                           "down_count": [6, 5, 4, 3, 2, 1],
                           "amount_100m_cny": [10, 20, 30, 40, 50, 60],
                           "listed_count": [7] * 6, "traded_count": [7] * 6})
    market.loc[5, "trade_date"] = pd.Timestamp(day)
    result = build_daily(scores, candidates, top1, market).iloc[0]
    assert result.predicted_up_n == 3
    assert result.predicted_up_labeled_n == 2
    assert result.predicted_up_unlabeled_n == 1
    assert (result.target_ge3_n, result.acceptable_1to3_n, result.error_lt0_n) == (1, 0, 1)
    assert result.T_minus_1_date == "2026-01-30"
    assert result.T_minus_1_up == 5
    assert result.T_minus_5_to_minus_1_amount_100m_cny == (
        "10.00; 20.00; 30.00; 40.00; 50.00")
    assert result.top1_0940_return_pct == pytest.approx(2.0)

    csv = export_daily(pd.DataFrame([result]))
    assert "达标数" in csv.columns and "严重错误数" in csv.columns
    assert "前一交易日" not in csv.columns
    assert csv.iloc[0]["第一名实际分类"] == "达标"
    assert csv.iloc[0][["第一名达标", "第一名可接受", "第一名严重错误"]].tolist() == [1, 0, 0]
    markdown = render_markdown(pd.DataFrame([result]), "source")
    assert "当天第一名股票" in markdown and "第一名严重错误" in markdown
    assert "| 5/2 |" in markdown
    assert "10.00<br>20.00<br>30.00<br>40.00<br>50.00" in markdown
    assert "Top1" not in markdown


def test_top1_flags_distinguish_neutral_and_no_trade():
    assert top1_flags("000001", "0to1") == (0, 0, 0)
    assert top1_flags(None, None) == (None, None, None)


def test_daily_report_rejects_price_disagreement():
    scores = pd.DataFrame({"signal_date": ["2026-02-02"], "symbol6": ["000001"],
                           "signal_price": [11.0], "predicted_class": ["ge3"]})
    candidates = pd.DataFrame({"signal_date": ["2026-02-02"], "symbol6": ["000001"],
                               "signal_price": [10.0], "second_high": [10.4],
                               "label_complete": [True]})
    top1 = pd.DataFrame(columns=["signal_date", "symbol6"])
    with pytest.raises(ValueError, match="entry prices disagree"):
        build_daily(scores, candidates, top1, pd.DataFrame())
