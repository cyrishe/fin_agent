"""Convert the completed 2024 Tushare minute archives to the existing MK layout.

Tushare supplies a separate 09:30 auction row.  The existing 240-bar archive
includes that auction in its 09:31 bar, so combine the two before writing.
The gzip source is read directly; no uncompressed market-wide copy is kept.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path

import numpy as np
import pandas as pd
import zstandard as zstd


SOURCE = Path('/Volumes/ext/tushare_market_data/2024')
OUT = Path('outputs/stock_automl/mk_1m_compact_2024')
DAILY = Path('outputs/stock_automl/limit_rebound_2y_r1/price_months')
SEPTEMBER_DAILY = Path('outputs/stock_automl/limit_rebound_2024_may_sep_r11_20261014/price_months')
COLUMNS = ['time', 'symbol', 'open', 'high', 'low', 'close', 'volume', 'turnover']
CLOCK = [
    f'{hour:02d}:{minute:02d}'
    for hour, minutes in ((9, range(30, 60)), (10, range(60)),
                          (11, range(31)), (13, range(1, 60)),
                          (14, range(60)), (15, range(1)))
    for minute in minutes
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def convert(frame: pd.DataFrame, day: str) -> pd.DataFrame:
    required = {'ts_code', 'trade_time', 'open', 'high', 'low', 'close', 'vol', 'amount'}
    if set(frame.columns) != required:
        raise ValueError(f'{day}: unexpected columns')
    if frame.empty or not frame.trade_time.str.startswith(day).all():
        raise ValueError(f'{day}: empty or wrong trading date')
    if frame.duplicated(['ts_code', 'trade_time']).any():
        raise ValueError(f'{day}: duplicate symbol-minute')
    counts = frame.groupby('ts_code').size()
    if not counts.eq(241).all():
        raise ValueError(f'{day}: incomplete 241-row stock day')
    clock_counts = frame.trade_time.str[11:16].value_counts()
    if len(clock_counts) != 241 or not clock_counts.reindex(CLOCK).eq(len(counts)).all():
        raise ValueError(f'{day}: minute clock differs from exchange sessions')
    auction = frame[frame.trade_time.eq(day + ' 09:30:00')].set_index('ts_code')
    regular = frame[frame.trade_time.ne(day + ' 09:30:00')].copy()
    if len(auction) != len(counts) or len(regular) != len(counts) * 240:
        raise ValueError(f'{day}: auction/regular row count mismatch')
    first = regular[regular.trade_time.eq(day + ' 09:31:00')].set_index('ts_code')
    if len(first) != len(counts) or not first.index.equals(auction.index):
        raise ValueError(f'{day}: first minute and auction keys differ')
    indices = regular.trade_time.eq(day + ' 09:31:00')
    a = auction.reindex(regular.loc[indices, 'ts_code'])
    b = regular.loc[indices]
    regular.loc[indices, 'open'] = a.open.to_numpy()
    regular.loc[indices, 'high'] = np.maximum(a.high.to_numpy(), b.high.to_numpy())
    regular.loc[indices, 'low'] = np.minimum(a.low.to_numpy(), b.low.to_numpy())
    regular.loc[indices, 'vol'] = a.vol.to_numpy() + b.vol.to_numpy()
    regular.loc[indices, 'amount'] = a.amount.to_numpy() + b.amount.to_numpy()
    result = regular.rename(columns={'trade_time': 'time', 'ts_code': 'symbol',
                                     'vol': 'volume', 'amount': 'turnover'})[COLUMNS]
    if result.groupby('symbol').size().ne(240).any():
        raise ValueError(f'{day}: converted stock day is not 240 bars')
    return result


def daily_match(frame: pd.DataFrame, day: str, daily: pd.DataFrame) -> dict:
    grouped = frame.groupby('symbol', sort=False).agg(
        open=('open', 'first'), high=('high', 'max'), low=('low', 'min'),
        close=('close', 'last'), volume=('volume', 'sum'), turnover=('turnover', 'sum'))
    target = daily[daily.trade_date.eq(day)].set_index('stk_code')
    joined = grouped.join(target[['open', 'high', 'low', 'close', 'volume', 'amount']],
                          how='inner', lsuffix='_minute', rsuffix='_daily')
    price_ok = np.logical_and.reduce([
        np.isclose(joined[f'{field}_minute'], joined[f'{field}_daily'], atol=.011, rtol=0)
        for field in ('open', 'high', 'low', 'close')])
    volume_ok = np.isclose(joined.volume_minute, joined.volume_daily, atol=1, rtol=.001)
    return {'minute_symbols': int(len(grouped)), 'daily_symbols': int(len(target)),
            'joined_symbols': int(len(joined)), 'price_match': int(price_ok.sum()),
            'volume_match': int(volume_ok.sum()),
            'price_volume_match': int((price_ok & volume_ok).sum())}


def run(source: Path = SOURCE, out: Path = OUT, daily_root: Path = DAILY,
        start: str = '2024-09-01', end: str = '2024-12-31') -> dict:
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    monthly = {}
    for path in sorted(source.glob('2024????.csv.gz')):
        day = f'{path.name[:4]}-{path.name[4:6]}-{path.name[6:8]}'
        if not start <= day <= end:
            continue
        metadata_path = path.with_suffix('').with_suffix('.json')
        metadata = json.loads(metadata_path.read_text())
        source_hash = sha256(path)
        if metadata['trade_date'] != day.replace('-', '') or metadata['sha256'] != source_hash:
            raise ValueError(f'{day}: source manifest/hash mismatch')
        if metadata['bars'] != metadata['codes_with_bars'] * 241:
            raise ValueError(f'{day}: source bars are not complete')
        dest = out / f'{day}.csv.zst'
        if dest.exists():
            raise FileExistsError(f'{dest}: use a new output directory for a new source snapshot')
        raw = pd.read_csv(path, dtype={'ts_code': str, 'trade_time': str})
        converted = convert(raw, day)
        month = day[:7]
        if month not in monthly:
            daily_path = (SEPTEMBER_DAILY if month == '2024-09' else daily_root) / f'price_{month}.csv.gz'
            monthly[month] = pd.read_csv(daily_path, dtype={'trade_date': str, 'stk_code': str})
        quality = daily_match(converted, day, monthly[month])
        if quality['joined_symbols'] == 0:
            raise ValueError(f'{day}: no matching daily snapshot')
        with dest.open('wb') as raw_out:
            with zstd.ZstdCompressor(level=3).stream_writer(raw_out) as compressed:
                with io.TextIOWrapper(compressed, encoding='utf-8') as text_out:
                    converted.to_csv(text_out, index=False)
        rows.append({'day': day, 'source': str(path), 'source_sha256': source_hash,
                     'source_bytes': path.stat().st_size, 'compact_sha256': sha256(dest),
                     'compact_bytes': dest.stat().st_size, 'bars': len(converted), **quality})
        print(day, quality['price_volume_match'], '/', quality['joined_symbols'], flush=True)
    manifest = {'source': str(source), 'output': str(out), 'days': len(rows),
                'date_range': [rows[0]['day'], rows[-1]['day']] if rows else [],
                'rows': rows}
    (out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--out', type=Path, default=OUT)
    parser.add_argument('--daily-root', type=Path, default=DAILY)
    parser.add_argument('--start', default='2024-09-01')
    parser.add_argument('--end', default='2024-12-31')
    args = parser.parse_args()
    result = run(args.source, args.out, args.daily_root, args.start, args.end)
    print(json.dumps({'days': result['days'], 'date_range': result['date_range']},
                     ensure_ascii=False), flush=True)
