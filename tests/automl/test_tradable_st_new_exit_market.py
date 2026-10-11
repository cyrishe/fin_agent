from __future__ import annotations

from decimal import Decimal

import pandas as pd

from scripts.report_automl_tradable_st_new_exit_market import attach_market, sell


def test_sell_uses_first_observable_trigger_and_strict_entry_thresholds():
    minute, reason, ret = sell(100, [103, 104] + [90] * 8)
    assert (minute, reason) == ("09:32", "涨幅超过3%")
    assert abs(ret - .04) < 1e-12

    minute, reason, ret = sell(100, [98, 99] + [99] * 8)
    assert (minute, reason) == ("09:32", "亏损中较前一分钟上涨至少1%")
    assert abs(ret + .01) < 1e-12

    minute, reason, ret = sell(100, [99, 100] + [100] * 8)
    assert (minute, reason) == ("09:40", "到时卖出")
    assert ret == 0


def test_market_context_uses_five_prior_days_nearest_first():
    dates = [f"2026-01-{day:02d}" for day in range(5, 11)]
    market = pd.DataFrame({"trade_date": dates,
                           "amount_1440_100m_cny": [50, 60, 70, 80, 90, 100],
                           "symbols_at_1440": [5000] * 6})
    breadth = pd.DataFrame({"trade_date": dates,
                            "amount_100m_cny": list(map(Decimal, [100, 120, 140, 160, 180, 200])),
                            "up_count": [1, 2, 3, 4, 5, 6],
                            "down_count": [6, 5, 4, 3, 2, 1]})
    result = attach_market(pd.DataFrame({"选股日": [dates[-1]]}), market, breadth)
    assert result["前一交易日上涨家数"].iloc[0] == 5
    assert result["前一交易日下跌家数"].iloc[0] == 2
    assert [result[f"前{lag}交易日截至14:40成交额（亿元）"].iloc[0]
            for lag in range(1, 6)] == [90, 80, 70, 60, 50]
