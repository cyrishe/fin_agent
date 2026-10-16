from datetime import timedelta

import pandas as pd

from scripts.experiment_automl_next_open_weekly import metrics, prepare_dataset, weekly_policy


def test_weekly_policy_limits_stocks_days_and_allows_abstention():
    dates = pd.bdate_range("2026-01-05", periods=10)
    frame = pd.DataFrame([
        {"signal_date": day, "symbol": f"{stock:06d}.SZ", "gap": 0.01}
        for day in dates for stock in range(10)
    ])
    scores = [0.9 if day < dates[5] else 0.1 for day in frame.signal_date]
    selected = weekly_policy(frame, scores, 0.8)
    assert len(selected) == 5
    assert selected.signal_date.nunique() == 2
    assert selected.signal_date.dt.strftime("%G-W%V").nunique() == 1
    assert metrics(selected)["precision"] == 1.0
    assert weekly_policy(frame, scores, 0.95).empty


def test_current_close_is_label_only_in_long_history_features():
    dates = pd.bdate_range("2026-01-05", periods=25)
    prices = [10 + i * .01 for i in range(len(dates))]
    price = pd.DataFrame({
        "symbol": "000001.SZ", "date": dates, "open": prices, "high": [p + .1 for p in prices],
        "low": [p - .1 for p in prices], "close": prices, "adjopen": prices,
        "adjclose": prices, "volume": 1000000, "amount": 20000000,
        "turn_ratio": .01, "update_time": [day + timedelta(hours=16) for day in dates],
    })
    flow = pd.DataFrame({"symbol": "000001.SZ", "date": dates, "main_ratio": .02,
                         "huge_ratio": .01,
                         "flow_updated": [day + timedelta(hours=16) for day in dates]})
    value = pd.DataFrame({"symbol": "000001.SZ", "date": dates, "total_mv": 1e10,
                          "pe_ttm": 12, "value_updated": [day + timedelta(hours=16) for day in dates]})
    industry = pd.DataFrame({"symbol": ["000001.SZ"], "industry_name": ["银行"],
                             "begin_date": [pd.Timestamp("2020-01-01")], "end_date": [pd.NaT]})
    index_rows = pd.DataFrame([
        {"idx_code": code, "index_short_name": name, "date": day,
         "close": 1000 + i, "amount": 1e9,
         "update_time": day + timedelta(hours=16)}
        for code, name in (("000300.SH", "沪深300"), ("000852.SH", "中证1000"),
                           ("801780.SL", "银行"))
        for i, day in enumerate(dates)
    ])
    original = prepare_dataset(dates, price, flow, value, industry, index_rows)
    row = original[original.signal_date.eq(dates[22])].iloc[0]
    changed = price.copy()
    changed.loc[changed.date.eq(dates[22]), ["close", "adjclose"]] = 20
    revised = prepare_dataset(dates, changed, flow, value, industry, index_rows)
    changed_row = revised[revised.signal_date.eq(dates[22])].iloc[0]
    assert row.gap != changed_row.gap
    assert row.price_return_20 == changed_row.price_return_20
    assert row.flow_main_ratio_1 == changed_row.flow_main_ratio_1
    assert row.sector_return_5 == changed_row.sector_return_5

    late_index = index_rows.copy()
    late_index.loc[(late_index.idx_code.eq("000300.SH")) &
                   (late_index.date.eq(dates[21])), "update_time"] = dates[24] + timedelta(hours=16)
    late = prepare_dataset(dates, price, flow, value, industry, late_index)
    late_row = late[late.signal_date.eq(dates[22])].iloc[0]
    assert pd.isna(late_row.csi300_return_1)
    assert pd.notna(late_row.sector_return_5)


def test_late_revised_price_history_is_not_used_for_selection_or_features():
    dates = pd.bdate_range("2026-01-05", periods=25)
    prices = [10 + i * .01 for i in range(len(dates))]
    price = pd.DataFrame({
        "symbol": "000001.SZ", "date": dates, "open": prices,
        "high": [p + .1 for p in prices], "low": [p - .1 for p in prices],
        "close": prices, "adjopen": prices, "adjclose": prices,
        "volume": 1000000, "amount": 20000000, "turn_ratio": .01,
        "update_time": [day + timedelta(hours=16) for day in dates],
    })
    flow = pd.DataFrame({"symbol": "000001.SZ", "date": dates,
                         "main_ratio": .02, "huge_ratio": .01,
                         "flow_updated": [day + timedelta(hours=16) for day in dates]})
    value = pd.DataFrame({"symbol": "000001.SZ", "date": dates,
                          "total_mv": 1e10, "pe_ttm": 12,
                          "value_updated": [day + timedelta(hours=16) for day in dates]})
    industry = pd.DataFrame({"symbol": ["000001.SZ"], "industry_name": ["银行"],
                             "begin_date": [pd.Timestamp("2020-01-01")], "end_date": [pd.NaT]})
    index_rows = pd.DataFrame([
        {"idx_code": code, "index_short_name": name, "date": day,
         "close": 1000 + i, "amount": 1e9,
         "update_time": day + timedelta(hours=16)}
        for code, name in (("000300.SH", "沪深300"), ("000852.SH", "中证1000"),
                           ("801780.SL", "银行"))
        for i, day in enumerate(dates)
    ])
    price.loc[price.date.eq(dates[21]), "update_time"] = dates[24] + timedelta(hours=16)
    dataset = prepare_dataset(dates, price, flow, value, industry, index_rows)
    assert dates[22] not in set(dataset.signal_date)
    assert dates[23] not in set(dataset.signal_date)
