"""Audit every available stock-day minute file against saved daily prices.

Read-only research utility. The input archives and daily snapshots are never altered.
"""

from __future__ import annotations

import argparse
import csv
import subprocess
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = Path('/Volumes/ext/tushare_market_data')
COMPACT = {
    2025: ROOT / 'outputs/stock_automl/mk_1m_compact_2025',
    2026: ROOT / 'outputs/stock_automl/mk_1m_compact',
}
PRICE_ROOT = ROOT / 'outputs/stock_automl'
FIELDS = [
    'date', 'source', 'daily_rows', 'daily_traded', 'minute_symbols',
    'matched_traded', 'daily_traded_no_minute', 'minute_no_daily',
    'zero_volume_minute', 'bad_bar_count', 'bad_timestamp_count',
    'low_missing', 'high_missing', 'both_missing', 'outside_daily',
    'open_mismatch', 'close_mismatch', 'volume_mismatch',
]


def daily_paths() -> dict[str, Path]:
    groups: dict[str, list[Path]] = {}
    for p in PRICE_ROOT.glob('*/price_months/price_20??-??.csv.gz'):
        month = p.stem.removeprefix('price_').removesuffix('.csv')
        groups.setdefault(month, []).append(p)
    paths: dict[str, Path] = {}
    for month, candidates in groups.items():
        if len(candidates) == 1:
            paths[month] = candidates[0]
            continue
        # A preferred snapshot may start mid-month. Compare actual trade-day
        # coverage before source preference so the denominator is not truncated.
        def coverage(path: Path) -> tuple[int, int, int, str]:
            dates = pd.read_csv(path, usecols=['trade_date'], dtype={'trade_date': 'string'})['trade_date']
            return (dates.nunique(), len(dates),
                    int('limit_rebound_2y_r1' in str(path)), str(path))
        paths[month] = max(candidates, key=coverage)
    return paths


def minute_paths(year: int) -> list[Path]:
    if year <= 2024:
        return sorted(p for p in (RAW_ROOT / str(year)).glob('20??????.csv.gz') if not p.name.startswith('._'))
    return sorted(p for p in COMPACT[year].glob('20??-??-??.csv.zst') if not p.name.startswith('._'))


def read_minute(path: Path) -> tuple[pd.DataFrame, str]:
    if path.suffix == '.zst':
        proc = subprocess.Popen(['zstd', '-dc', str(path)], stdout=subprocess.PIPE)
        assert proc.stdout is not None
        try:
            frame = pd.read_csv(proc.stdout, usecols=['time', 'symbol', 'open', 'high', 'low', 'close', 'volume'], dtype={'symbol': 'string'})
        finally:
            proc.stdout.close()
        if proc.wait() != 0:
            raise RuntimeError(f'zstd failed: {path}')
        frame = frame.rename(columns={'symbol': 'code', 'time': 'stamp', 'volume': 'vol'})
        source = 'compact240'
    else:
        frame = pd.read_csv(path, compression='gzip', usecols=['ts_code', 'trade_time', 'open', 'high', 'low', 'close', 'vol'], dtype={'ts_code': 'string'})
        frame = frame.rename(columns={'ts_code': 'code', 'trade_time': 'stamp'})
        source = 'raw241'
    return frame, source


def summarize_minutes(df: pd.DataFrame) -> pd.DataFrame:
    all_counts = df.groupby('code', sort=False).agg(bar_count=('stamp', 'size'), stamp_count=('stamp', 'nunique'))
    traded = df.loc[df['vol'] > 0]
    if traded.empty:
        stats = pd.DataFrame(columns=['m_open', 'm_high', 'm_low', 'm_close', 'm_vol'])
    else:
        stats = traded.groupby('code', sort=False).agg(
            m_open=('open', 'first'), m_high=('high', 'max'), m_low=('low', 'min'),
            m_close=('close', 'last'), m_vol=('vol', 'sum'),
        )
    return all_counts.join(stats, how='left')


def audit_day(path: Path, daily: pd.DataFrame, outliers: csv.DictWriter) -> dict[str, object]:
    name = path.name.split('.')[0]
    date = name if '-' in name else f'{name[:4]}-{name[4:6]}-{name[6:8]}'
    minute, source = read_minute(path)
    minute_stats = summarize_minutes(minute)
    minute = None
    current = daily.loc[daily['trade_date'] == date, ['stk_code', 'open', 'high', 'low', 'close', 'volume']].copy()
    current = current.rename(columns={'stk_code': 'code', 'open': 'd_open', 'high': 'd_high', 'low': 'd_low', 'close': 'd_close', 'volume': 'd_vol'})
    current = current.drop_duplicates('code', keep='last').set_index('code')
    traded_daily = current['d_vol'] > 0
    joined = current.join(minute_stats, how='inner')
    active = joined.loc[(joined['d_vol'] > 0) & (joined['m_vol'] > 0)].copy()
    low_missing = active['m_low'] - active['d_low'] > 0.011
    high_missing = active['d_high'] - active['m_high'] > 0.011
    outside = (active['d_low'] - active['m_low'] > 0.011) | (active['m_high'] - active['d_high'] > 0.011)
    open_bad = (active['m_open'] - active['d_open']).abs() > 0.011
    close_bad = (active['m_close'] - active['d_close']).abs() > 0.011
    vol_bad = (active['m_vol'] - active['d_vol']).abs() > active['d_vol'].abs().mul(0.0001).clip(lower=100)
    suspect = active.loc[low_missing | high_missing | outside | open_bad | close_bad]
    if not suspect.empty:
        for code, row in suspect.iterrows():
            outliers.writerow({
                'date': date, 'code': code, 'daily_open': row['d_open'], 'minute_open': row['m_open'],
                'daily_high': row['d_high'], 'minute_high': row['m_high'],
                'daily_low': row['d_low'], 'minute_low': row['m_low'],
                'daily_close': row['d_close'], 'minute_close': row['m_close'],
                'daily_volume': row['d_vol'], 'minute_volume': row['m_vol'],
                'low_missing': bool(low_missing.loc[code]), 'high_missing': bool(high_missing.loc[code]),
                'outside_daily': bool(outside.loc[code]),
            })
    expected = 240 if source == 'compact240' else 241
    return {
        'date': date, 'source': source, 'daily_rows': len(current), 'daily_traded': int(traded_daily.sum()),
        'minute_symbols': len(minute_stats), 'matched_traded': len(active),
        'daily_traded_no_minute': int((traded_daily & ~current.index.isin(minute_stats.index)).sum()),
        'minute_no_daily': int((~minute_stats.index.isin(current.index)).sum()),
        'zero_volume_minute': int(minute_stats['m_vol'].isna().sum()),
        'bad_bar_count': int((minute_stats['bar_count'] != expected).sum()),
        'bad_timestamp_count': int((minute_stats['stamp_count'] != expected).sum()),
        'low_missing': int(low_missing.sum()), 'high_missing': int(high_missing.sum()),
        'both_missing': int((low_missing & high_missing).sum()), 'outside_daily': int(outside.sum()),
        'open_mismatch': int(open_bad.sum()), 'close_mismatch': int(close_bad.sum()),
        'volume_mismatch': int(vol_bad.sum()),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--years', nargs='+', type=int, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--limit-days', type=int)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    prices = daily_paths()
    for year in args.years:
        paths = minute_paths(year)
        if args.limit_days is not None:
            paths = paths[:args.limit_days]
        report = args.out / f'days_{year}.csv'
        outliers_file = args.out / f'price_outliers_{year}.csv'
        with report.open('w', newline='') as day_f, outliers_file.open('w', newline='') as out_f:
            day_writer = csv.DictWriter(day_f, FIELDS)
            out_writer = csv.DictWriter(out_f, ['date', 'code', 'daily_open', 'minute_open', 'daily_high', 'minute_high', 'daily_low', 'minute_low', 'daily_close', 'minute_close', 'daily_volume', 'minute_volume', 'low_missing', 'high_missing', 'outside_daily'])
            day_writer.writeheader(); out_writer.writeheader()
            current_month = None
            daily = None
            for i, path in enumerate(paths, 1):
                month = path.name[:7] if year >= 2025 else f'{path.name[:4]}-{path.name[4:6]}'
                if month != current_month:
                    if month not in prices:
                        raise FileNotFoundError(f'No daily snapshot for {month}')
                    daily = pd.read_csv(prices[month], compression='gzip', dtype={'stk_code': 'string', 'trade_date': 'string'})
                    current_month = month
                assert daily is not None
                result = audit_day(path, daily, out_writer)
                day_writer.writerow(result)
                if i % 20 == 0 or i == len(paths):
                    day_f.flush();out_f.flush()
                    print(year, i, len(paths), result['date'], flush=True)
        print('DONE', year, report, outliers_file, flush=True)


if __name__ == '__main__':
    main()
