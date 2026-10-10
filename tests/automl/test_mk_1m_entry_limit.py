from __future__ import annotations

import pandas as pd

from scripts.audit_automl_mk_1m_entry_limit import mark_entry_limit


def test_entry_limit_uses_historical_board_rule_and_cent_rounding():
    rows = pd.DataFrame({
        "signal_date": ["2026-03-24", "2026-03-24", "2026-07-06", "2026-03-24"],
        "symbol6": ["600180", "301288", "600180", "000988"],
        "st_type": ["Y", "Y", "Y", "N"],
        "ann_date": pd.to_datetime(["2026-01-01"] * 4),
        "preclose": [1.31, 16.38, 1.31, 10.0],
        "signal_price": [1.38, 17.20, 1.38, 10.50],
    })
    result = mark_entry_limit(rows)
    assert result.limit_price.tolist() == [1.38, 19.66, 1.44, 11.0]
    assert result.entry_at_limit.tolist() == [True, False, False, False]
    assert result.entry_eligible.tolist() == [False, True, True, True]
