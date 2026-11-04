from datetime import timedelta

import pandas as pd

from scripts.experiment_automl_next_open_1445 import prepare_dataset


def example_rows():
    dates = pd.bdate_range("2026-07-01", periods=24)
    daily = pd.DataFrame({
        "symbol": "000001.SZ", "date": dates,
        "open": [10 + n * .01 for n in range(len(dates))],
        "close": [10 + n * .01 for n in range(len(dates))],
        "adjopen": [10 + n * .01 for n in range(len(dates))],
        "adjclose": [10 + n * .01 for n in range(len(dates))],
        "volume": 1000000, "amount": 20000000, "turn_ratio": .01,
        "flow_ratio": .02, "total_mv": 1e10, "pe_ttm": 12,
        "flow_updated": [day + timedelta(hours=16) for day in dates],
        "value_updated": [day + timedelta(hours=16) for day in dates],
        "update_time": [day + timedelta(hours=16) for day in dates],
    })
    signal_day = dates[21]
    minute = pd.DataFrame([{
        "symbol6": "000001", "date": signal_day, "industry": "银行",
        "day_open": 10.1, "p1431": 10.2, "p1440": 10.25, "p1445": 10.3,
        "tail_amount": 1000000, "tail_bars": 15, "fallback": 0,
        "latest_source_snapshot": signal_day + timedelta(hours=14, minutes=45),
    }])
    return list(dates), daily, minute


def test_current_close_changes_label_only_and_late_minute_is_excluded():
    dates, daily, minute = example_rows()
    original = prepare_dataset(dates, daily, minute)
    assert len(original) == 1
    changed = daily.copy()
    changed.loc[changed.date.eq(dates[21]), ["close", "adjclose"]] = 20
    revised = prepare_dataset(dates, changed, minute)
    assert revised.loc[0, "gap"] != original.loc[0, "gap"]
    assert revised.loc[0, "prior_return_20"] == original.loc[0, "prior_return_20"]
    assert revised.loc[0, "day_return_1445"] == original.loc[0, "day_return_1445"]
    late = minute.copy()
    late["latest_source_snapshot"] = dates[21] + timedelta(hours=14, minutes=46)
    assert prepare_dataset(dates, daily, late).empty
