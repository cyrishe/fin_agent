from __future__ import annotations

import pandas as pd

from scripts.build_automl_mk_1m_candidates import summarize_day


def test_minute_summary_preserves_all_ten_morning_closes(tmp_path):
    morning = pd.date_range("2026-01-05 09:31", periods=120, freq="min")
    afternoon = pd.date_range("2026-01-05 13:01", periods=120, freq="min")
    times = morning.append(afternoon)
    rows = pd.DataFrame({
        "time": times.strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": "600000.SH", "open": 10., "high": 10.,
        "low": 10., "close": 10., "volume": 100.,
    })
    rows.loc[rows.time.str.endswith("09:35:00"),
             ["open", "high", "low", "close"]] = 10.35
    path = tmp_path / "2026-01-05.csv"
    rows.to_csv(path, index=False)
    result, audit = summarize_day(path)
    assert audit["invalid_required_stock_days"] == 0
    assert len(result) == 1
    assert result.iloc[0].morning_close_35 == 10.35
    assert result.iloc[0].morning_close_40 == 10.
    assert result.iloc[0].morning_second_high == 10.
