from __future__ import annotations

import pandas as pd

from scripts.prepare_automl_mk_1m_tradable_st import prepare


def test_keep_tradable_st_and_plain_stock_but_drop_signal_limit():
    rows = pd.DataFrame({
        "signal_date": ["2026-02-03"] * 3,
        "symbol6": ["600001", "600002", "600003"],
        "t_reference_preclose": [10., 10., 10.],
        "signal_price": [10.5, 10.4, 10.4],
        "second_high": [10.9, 10.5, 10.5],
        "close_40": [10.9, 10.5, 10.5],
    })
    intervals = pd.DataFrame({
        "symbol6": ["600001", "600002", "600003"],
        "st_type": ["Y", "Y", "N"],
        "begin_date": pd.to_datetime(["2026-01-01"] * 3),
        "end_date": pd.to_datetime(["2026-03-01"] * 3),
        "ann_date": pd.to_datetime(["2026-01-01", "2026-01-01", None]),
    })
    reference = rows.iloc[[1, 2]].drop(columns="t_reference_preclose")
    kept, report = prepare(rows, intervals, reference)
    assert kept.symbol6.tolist() == ["600002", "600003"]
    assert report["retained_st_rows"] == 1
    assert report["removed_at_limit_st_rows"] == 1
