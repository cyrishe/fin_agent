from scripts.append_automl_stock_amount_state import required_bars, stock_columns


def test_stock_percentile_and_increasing_days_use_only_prior_trading_days():
    values = [float(i) for i in range(1, 21)]
    row = stock_columns(0.5, values)
    assert row["个股此前20日有效天数"] == "20"
    assert row["个股此前5日递增次数（共4次比较）"] == "4"
    assert row["个股当日在前20日中的百分位"] == "0.000000"
    assert row["个股成交额低于此前20日全部（标签）"] == "是"
    assert row["个股成交额低于此前20日中位数（标签）"] == "是"
    assert row["个股此前5日成交额（亿元，远到近）"] == (
        "16.0000;17.0000;18.0000;19.0000;20.0000")
    rows = [{"选股日": "2026/2/2", "Top1代码": "002131"}]
    days = [f"2026-01-{i:02d}" for i in range(1, 21)] + ["2026-02-02"]
    needed = required_bars(rows, days)
    assert len(needed) == 21
    assert needed["2026-01-01"] == {"002131"}
    assert needed["2026-02-02"] == {"002131"}


def test_missing_stock_history_is_explicit_and_has_no_percentile():
    prior = [float(i) for i in range(1, 21)]
    prior[7] = None
    row = stock_columns(25.0, prior)
    assert row["个股此前20日有效天数"] == "19"
    assert row["个股当日截至14:40成交额（亿元）"] == "25.0000"
    assert row["个股当日在前20日中的百分位"] == ""
    assert row["个股成交额低于此前20日全部（标签）"] == ""
