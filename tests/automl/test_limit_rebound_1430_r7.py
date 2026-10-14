import pandas as pd

from scripts.experiment_automl_limit_rebound_1430_r7 import add_labels, extract_prefix


def test_1430_features_ignore_all_later_minutes():
    morning = pd.date_range('2026-06-01 09:31', '2026-06-01 11:30', freq='min')
    afternoon = pd.date_range('2026-06-01 13:01', '2026-06-01 14:30', freq='min')
    times = morning.append(afternoon)
    bars = pd.DataFrame({'time': times.astype(str), 'open': 10.0, 'high': 10.2,
                         'low': 9.9, 'close': 10.1, 'volume': 100,
                         'turnover': 1010.0})
    at_cutoff = extract_prefix(bars, 10.0, 100000.0)
    later = pd.DataFrame({'time': ['2026-06-01 15:00:00'], 'open': [30.0],
                          'high': [30.0], 'low': [1.0], 'close': [30.0],
                          'volume': [999999], 'turnover': [999999.0]})
    assert at_cutoff == extract_prefix(pd.concat([bars, later]), 10.0, 100000.0)
    assert extract_prefix(bars.iloc[:-1], 10.0, 100000.0) is None


def test_labels_start_after_decision_day():
    daily = pd.DataFrame({'symbol': ['000001'] * 5, 'date_i': range(5),
                          'up': [True, True, False, True, False]})
    decision = pd.DataFrame({'symbol': ['000001'], 'date_i': [0], 'd1_up': [True]})
    result = add_labels(decision, daily).iloc[0]
    # D1 reboards, but the 14:30 decision predicts D2 and later.
    assert result.label_next1 == 0
    assert result.label_next3 == 1
    assert result.label_next3_after_cold_close == 0
    assert result.observed_next3
