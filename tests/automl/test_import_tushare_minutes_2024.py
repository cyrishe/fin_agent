import json

import pandas as pd
import pytest

import scripts.import_tushare_minutes_2024 as importer
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


def test_append_preserves_verified_days_and_merges_manifest(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    output = tmp_path / 'compact'
    daily_root = tmp_path / 'daily'
    source.mkdir()
    daily_root.mkdir()
    monkeypatch.setattr(importer, 'EARLY_DAILY', daily_root)
    days = ['2024-01-02', '2024-01-03']
    pd.DataFrame([{'trade_date': day, 'stk_code': '000001.SZ',
                   'open': 10, 'high': 10, 'low': 10, 'close': 10,
                   'volume': 241, 'amount': 2410} for day in days]).to_csv(
        daily_root / 'price_2024-01.csv.gz', index=False)
    for day in days:
        raw = pd.DataFrame([{'ts_code': '000001.SZ',
                             'trade_time': f'{day} {clock}:00',
                             'open': 10, 'high': 10, 'low': 10, 'close': 10,
                             'vol': 1, 'amount': 10} for clock in CLOCK])
        path = source / f'{day.replace("-", "")}.csv.gz'
        raw.to_csv(path, index=False)
        path.with_suffix('').with_suffix('.json').write_text(json.dumps({
            'trade_date': day.replace('-', ''), 'sha256': importer.sha256(path),
            'bars': 241, 'codes_with_bars': 1}))
    importer.run(source, output, start=days[0], end=days[0])
    original_hash = importer.sha256(output / f'{days[0]}.csv.zst')
    result = importer.run(source, output, start=days[0], end=days[1],
                          append_existing=True)
    assert result['days'] == 2
    assert [row['day'] for row in result['rows']] == days
    assert importer.sha256(output / f'{days[0]}.csv.zst') == original_hash
    assert importer.run(source, output, start=days[0], end=days[1],
                        append_existing=True)['days'] == 2
