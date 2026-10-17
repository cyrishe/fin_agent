import pandas as pd
import pytest

from scripts.import_tushare_minutes_2024 import CLOCK, convert


def test_auction_is_included_in_first_completed_minute():
    day = '2024-09-02'
    rows = []
    for clock in CLOCK:
        rows.append({'ts_code': '000001.SZ', 'trade_time': f'{day} {clock}:00',
                     'open': 10.0, 'high': 10.1, 'low': 9.9, 'close': 10.0,
                     'vol': 100, 'amount': 1000})
    rows[0].update(open=9.8, high=10.2, low=9.7, vol=200, amount=2000)
    rows[1].update(open=10.0, high=10.1, low=9.9, close=10.05)
    result = convert(pd.DataFrame(rows), day)
    assert len(result) == 240
    assert result.iloc[0].time == f'{day} 09:31:00'
    assert result.iloc[0][['open', 'high', 'low', 'close', 'volume', 'turnover']].tolist() == [
        9.8, 10.2, 9.7, 10.05, 300, 3000]
    assert result.volume.sum() == 24200


def test_missing_exchange_minute_rejected():
    day = '2024-09-02'
    frame = pd.DataFrame([
        {'ts_code': '000001.SZ', 'trade_time': f'{day} {clock}:00',
         'open': 10, 'high': 10, 'low': 10, 'close': 10, 'vol': 1, 'amount': 10}
        for clock in CLOCK
    ])
    frame.loc[5, 'trade_time'] = f'{day} 12:00:00'
    with pytest.raises(ValueError, match='minute clock'):
        convert(frame, day)
