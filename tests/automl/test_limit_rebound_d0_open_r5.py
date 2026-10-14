import pandas as pd

from scripts import experiment_automl_limit_rebound_d0_open_r5 as r5


def test_d1_open_activation_and_same_day_reboard(monkeypatch, tmp_path):
    days = pd.bdate_range('2024-10-01', periods=12).strftime('%Y-%m-%d').tolist()
    rows = []
    for i, day in enumerate(days):
        preclose = 10.0 if i <= 5 else 11.0 if i == 6 else 12.1
        close = 11.0 if i == 5 else 12.1 if i == 6 else preclose
        rows.append({'trade_date': day, 'symbol': '000001', 'date_i': i,
                     'preclose': preclose, 'open': 10.2 if i == 5 else 11.3 if i == 6 else preclose,
                     'high': close, 'low': min(preclose, close), 'close': close,
                     'volume': 1000, 'amount': 10000, 'turn_ratio': 2.0,
                     'up': i in (5, 6)})
    def status(frame, _):
        result = frame.copy()
        result['is_st'] = 0
        return result
    monkeypatch.setattr(r5, 'historical_status', status)
    panel, _ = r5.build_panel(pd.DataFrame(rows), tmp_path)
    first = panel.loc[panel.d0_date.eq(days[5])].iloc[0]
    assert first.board_streak_days == 1
    assert first.activated
    assert first.d1_up
    assert first.label_3 == first.label_4 == first.label_5 == 1
    second = panel.loc[panel.d0_date.eq(days[6])].iloc[0]
    assert second.board_streak_days == 2
    assert second.label_3 == 0


def test_open_at_cap_does_not_activate(monkeypatch, tmp_path):
    days = pd.bdate_range('2024-10-01', periods=9).strftime('%Y-%m-%d').tolist()
    rows = [{'trade_date': day, 'symbol': '000001', 'date_i': i, 'preclose': 10.0,
             'open': 11.0 if i == 6 else 10.0, 'high': 11.0 if i == 5 else 10.0,
             'low': 10.0, 'close': 11.0 if i == 5 else 10.0, 'volume': 1000,
             'amount': 10000, 'turn_ratio': 2.0, 'up': i == 5}
            for i, day in enumerate(days)]
    monkeypatch.setattr(r5, 'historical_status', lambda frame, _: frame.assign(is_st=0))
    panel, _ = r5.build_panel(pd.DataFrame(rows), tmp_path)
    assert not panel.iloc[0].activated
