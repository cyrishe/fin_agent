from __future__ import annotations

import pandas as pd

from scripts.prepare_automl_mk_1m_nonst import annotate, jan_jun_entry_split


def test_historical_st_filter_accepts_plain_n_without_announcement():
    rows = pd.DataFrame({
        "signal_date": ["2026-02-03"] * 3,
        "symbol6": ["000001", "000002", "000003"],
    })
    intervals = pd.DataFrame({
        "symbol6": ["000001", "000002", "000003"],
        "st_type": ["N", "Y", "Y"],
        "begin_date": pd.to_datetime(["2026-01-01"] * 3),
        "end_date": pd.to_datetime(["2026-03-01"] * 3),
        "ann_date": pd.to_datetime([None, "2026-01-01", "2026-02-04"]),
    })
    result = annotate(rows, intervals)
    assert result.historical_status_known.tolist() == [True, True, False]
    assert result.historical_st.tolist() == [False, True, True]


def test_entry_split_distinguishes_st_price_cap_from_st_identity():
    rows = pd.DataFrame({
        "signal_date": ["2026-02-03"] * 3,
        "symbol6": ["600001", "600002", "600003"],
        "st_type": ["Y", "Y", "N"],
        "ann_date": pd.to_datetime(["2026-01-01", "2026-01-01", None]),
        "historical_st": [True, True, False],
        "t_reference_preclose": [10., 10., 10.],
        "signal_price": [10.5, 10.4, 10.4],
        "second_high": [10.9, 10.5, 10.5],
        "close_40": [10.9, 10.5, 10.5],
    })
    result = jan_jun_entry_split(rows)
    assert result["ST_at_limit"] == {
        "labeled": 1, "label_ge3": 1, "sale_0940_ge3": 1}
    assert result["ST_not_at_limit"]["label_ge3"] == 0
    assert result["non_ST"]["labeled"] == 1
