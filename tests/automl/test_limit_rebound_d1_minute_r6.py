import pandas as pd

from scripts.research_limit_rebound_d1_minute_r6 import minute_facts


def test_minute_times_keep_1000_decision_before_1001_fill():
    morning = pd.date_range('2026-07-01 09:31', periods=120, freq='min')
    afternoon = pd.date_range('2026-07-01 13:01', periods=120, freq='min')
    times = morning.append(afternoon).strftime('%Y-%m-%d %H:%M:%S')
    bars = pd.DataFrame({'time': times, 'open': 10.5, 'high': 10.5,
                         'low': 10.5, 'close': 10.5, 'volume': 100})
    bars.loc[30:, ['high', 'close']] = 11.0
    bars.loc[30, 'open'] = 10.5
    bars.loc[30:, 'low'] = 11.0
    bars.loc[30, 'low'] = 10.5
    facts = minute_facts(bars, 11.0, 11.0)
    assert facts['below_cap_at_1000']
    assert facts['can_buy_1001_proxy']
    assert facts['first_close_cap_min'] == 31
    assert facts['final_close_cap_min'] == 31
