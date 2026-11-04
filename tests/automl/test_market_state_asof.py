import pandas as pd

from scripts.report_automl_market_state import learned_rise_gate, market_features


def test_market_state_uses_prior_twenty_days_and_current_1440_only():
    days = pd.date_range("2026-01-01", periods=21).strftime("%Y-%m-%d")
    market = pd.DataFrame({"trade_date": days,
                           "amount_1440_100m_cny": range(1, 22)})
    result = market_features(market)
    last = result.iloc[-1]
    assert pd.isna(result.iloc[19]["此前20日均额（亿元）"])
    assert last["此前20日均额（亿元）"] == 10.5
    assert last["此前5日成交额（亿元，远到近）"] == "16.00;17.00;18.00;19.00;20.00"
    assert last["此前5日递增次数（共4次比较）"] == 4
    assert last["当日在前20日中的百分位"] == 1


def test_learned_cutoff_ignores_labels_maturing_after_signal():
    history = pd.DataFrame({
        "选股日": pd.date_range("2025-01-01", periods=40).strftime("%Y-%m-%d"),
        "次交易日": pd.date_range("2025-01-02", periods=40).strftime("%Y-%m-%d"),
        "此前5日递增次数（共4次比较）": [1] * 20 + [3] * 20,
        "Top1收益率": [.02] * 20 + [-.02] * 20,
    })
    current = pd.DataFrame([{
        "选股日": "2025-03-01", "次交易日": "2025-03-02",
        "此前5日递增次数（共4次比较）": 3, "Top1收益率": float("nan"),
    }])
    expected = learned_rise_gate(history, current)
    future = pd.DataFrame([{"选股日": "2025-02-28", "次交易日": "2025-03-03",
                            "此前5日递增次数（共4次比较）": 3, "Top1收益率": 10.0}])
    actual = learned_rise_gate(pd.concat([history, future]), current)
    pd.testing.assert_frame_equal(expected, actual)
    assert actual.iloc[0]["自动阈值试验标记"] == "未通过"
