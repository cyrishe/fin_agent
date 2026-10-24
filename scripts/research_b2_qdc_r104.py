"""Frozen B2_V2 Top3/rule portability replay on QDC daily and one-minute bars.

This is a source sensitivity experiment. Monthly RF scores and four-K rules are
kept frozen from the original chronological runs; the model is not retrained.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb
import pandas as pd

from scripts.automl_b2 import evaluate_morning
from scripts.replay_limit_rebound_cash_r23 import replay
from scripts.research_b2_daily_only_r100 import read_inputs

ROOT = Path(__file__).resolve().parents[1]
QDC = Path('/Volumes/ext/minite_k_data')
OUT = ROOT / 'outputs/stock_automl/b2_qdc_r104_20261024'
RULE_PATHS = [
    ROOT / 'docs/stock_automl_runs/20261023_b2_2018_2021_r90/evidence/rules_frozen.json',
    ROOT / 'outputs/stock_automl/b2_2021_minute_r60_20261020/rules_frozen.json',
    ROOT / 'outputs/stock_automl/b2_2022_minute_r41_20261019/rules_frozen.json',
    ROOT / 'outputs/stock_automl/b2_two_month_2024_r35_20261019/rules_frozen.json',
]


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def qdc_symbol(code: str) -> str:
    return code[:6] + ('.XSHG' if code.endswith('.SH') else '.XSHE' if code.endswith('.SZ') else '.XBSE')


def load_frozen() -> tuple[pd.DataFrame, list[str], dict]:
    candidates, calendar, manifest = read_inputs()
    candidates = candidates[candidates.d0_date.le('2026-09-30')].copy()
    candidates['model_month'] = candidates.d0_date.str[:7]
    candidates['qdc_symbol'] = candidates.stk_code.map(qdc_symbol)
    rules = {}
    for path in RULE_PATHS:
        for item in json.loads(path.read_text()):
            month = item['month']
            assert month not in rules, month
            rules[month] = item['selected']
    candidates = candidates[candidates.model_month.isin(rules)].copy()
    candidates = candidates[candidates.d1_date.notna()].copy()
    date_i = {d: i for i, d in enumerate(calendar)}
    candidates['d2_date'] = candidates.d1_date.map(
        lambda d: calendar[date_i[d] + 1] if d in date_i and date_i[d] + 1 < len(calendar) else None)
    candidates = candidates[candidates.d2_date.notna()].copy()
    assert not candidates.duplicated(['symbol', 'd0_date']).any()
    return candidates, calendar, {'scores': manifest, 'rules': {str(p.relative_to(ROOT)): digest(p) for p in RULE_PATHS}}


def extract_year(year: int, wanted: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    daily_path = QDC / '日线' / f'{year}.parquet'
    minute_path = QDC / '1分钟' / f'{year}.parquet'
    con = duckdb.connect()
    days = pd.concat([
        wanted[['qdc_symbol', 'd0_date']].rename(columns={'d0_date': 'day'}),
        wanted[['qdc_symbol', 'd1_date']].rename(columns={'d1_date': 'day'}),
        wanted[['qdc_symbol', 'd2_date']].rename(columns={'d2_date': 'day'}),
    ]).drop_duplicates()
    days = days[days.day.str.startswith(str(year))]
    con.register('wanted_days', days)
    if year < 2026:
        daily_glob = str(daily_path)
        minute_glob = str(minute_path)
    else:
        daily_glob = str(QDC / '日线/2026-*.parquet')
        minute_glob = str(QDC / '1分钟/2026-*.parquet')
    daily = con.execute("""
        SELECT d.symbol AS qdc_symbol, CAST(CAST(to_timestamp(d.ts/1000)
            AT TIME ZONE 'Asia/Shanghai' AS DATE) AS VARCHAR) AS day,
            d.open, d.high, d.low, d.close, d.volume, d.amount, d.source
        FROM read_parquet(?) d JOIN wanted_days w ON d.symbol=w.qdc_symbol
          AND CAST(CAST(to_timestamp(d.ts/1000) AT TIME ZONE 'Asia/Shanghai'
              AS DATE) AS VARCHAR)=w.day
    """, [daily_glob]).df()
    keys = wanted[['qdc_symbol', 'd1_date']].drop_duplicates()
    keys = keys[keys.d1_date.str.startswith(str(year))]
    con.register('wanted_minutes', keys)
    minutes = con.execute("""
        SELECT m.symbol AS qdc_symbol,
          CAST(CAST(to_timestamp(m.ts/1000) AT TIME ZONE 'Asia/Shanghai' AS DATE) AS VARCHAR) AS d1_date,
          CAST(to_timestamp(m.ts/1000) AT TIME ZONE 'Asia/Shanghai' AS TIMESTAMP) AS time,
          m.open, m.high, m.low, m.close, m.volume, m.amount, m.source
        FROM read_parquet(?) m JOIN wanted_minutes w ON m.symbol=w.qdc_symbol
          AND CAST(CAST(to_timestamp(m.ts/1000) AT TIME ZONE 'Asia/Shanghai'
              AS DATE) AS VARCHAR)=w.d1_date
        WHERE CAST(to_timestamp(m.ts/1000) AT TIME ZONE 'Asia/Shanghai' AS TIME)
          BETWEEN TIME '09:31:00' AND TIME '09:40:00'
    """, [minute_glob]).df()
    con.close()
    return daily, minutes


def prepare_data(candidates: pd.DataFrame, years: list[int]) -> tuple[pd.DataFrame, pd.DataFrame]:
    daily_parts, minute_parts = [], []
    for year in years:
        subset = candidates[(candidates.d0_date.str[:4].eq(str(year))) |
                            (candidates.d1_date.str[:4].eq(str(year))) |
                            (candidates.d2_date.str[:4].eq(str(year)))]
        if subset.empty:
            continue
        day, minute = extract_year(year, subset)
        daily_parts.append(day)
        minute_parts.append(minute)
        print(f'extracted {year}: daily={len(day)} minute={len(minute)}', flush=True)
    daily = pd.concat(daily_parts, ignore_index=True).drop_duplicates(['qdc_symbol', 'day'])
    minute = pd.concat(minute_parts, ignore_index=True)
    daily.to_pickle(OUT / 'qdc_candidate_daily.pkl')
    minute.to_pickle(OUT / 'qdc_candidate_first10.pkl')
    return daily, minute


def attach_daily(candidates: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    frame = candidates.copy()
    for tag, date in [('d0', 'd0_date'), ('d1', 'd1_date'), ('d2', 'd2_date')]:
        keep = daily[['qdc_symbol', 'day', 'open', 'high', 'low', 'close', 'volume', 'source']].rename(
            columns={'day': date, **{c: f'qdc_{tag}_{c}' for c in ['open', 'high', 'low', 'close', 'volume', 'source']}})
        frame = frame.merge(keep, on=['qdc_symbol', date], how='left', validate='many_to_one')
    for tag, old in [('d0', 'close'), ('d1', 'd1_close'), ('d2', 'd2_close')]:
        frame[f'{tag}_close_gap'] = (frame[f'qdc_{tag}_close'] - frame[old]).abs()
    return frame


def choose(candidates: pd.DataFrame, minute: pd.DataFrame, rules: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    minute = minute.copy()
    # Some historical sources round sub-lot volume to zero despite positive amount.
    minute['rounded_trades'] = minute.volume.eq(0) & minute.amount.gt(0)
    minute.loc[minute.rounded_trades, 'volume'] = 1.0
    bars = {(s, d): g[['time', 'open', 'high', 'low', 'close', 'volume']].sort_values('time')
            for (s, d), g in minute.groupby(['qdc_symbol', 'd1_date'])}
    decisions = []
    picked = []
    for d0, group in candidates.groupby('d0_date', sort=True):
        picks = []
        source = {}
        for row in group.to_dict('records'):
            cap = row['d1_cap']
            preclose = row['d1_preclose']
            # Frozen model's price limits are kept; record old/new price gaps separately.
            ordinary = row['d0_st'] == 0 and row['d1_st'] == 0
            picks.append(dict(symbol6=row['symbol'], selection_rank=int(row['d0_rank']),
                              preclose=preclose, cap=cap, ordinary=ordinary))
            source[row['symbol']] = row
        day = group.d1_date.iloc[0]
        b = {r['symbol']: bars.get((r['qdc_symbol'], day), pd.DataFrame()) for r in group.to_dict('records')}
        try:
            result = evaluate_morning(picks, b, day, pd.Timestamp(day + ' 09:40:00').to_pydatetime(),
                                      rules[group.model_month.iloc[0]])
        except ValueError as exc:
            result = [dict(symbol6=p['symbol6'], actionable=0, reason=f'minute_invalid:{exc}') for p in picks]
        for action in result:
            src = source[action['symbol6']]
            item = {k: src[k] for k in ['symbol', 'd0_date', 'd1_date', 'd2_date', 'model_month',
                'd0_rank', 'score', 'd0_close_gap', 'd1_close_gap', 'd2_close_gap']}
            item.update({k: action.get(k) for k in ['actionable', 'shape_ready', 'shape_pass',
                'chosen', 'reason', 'activation_bar_end', 'entry_at', 'entry_price']})
            decisions.append(item)
            if action.get('actionable'):
                p = dict(src)
                p['entry'] = action['entry_price']
                p['entry_at'] = action['entry_at']
                p['rule_id'] = rules[src['model_month']]['id']
                p['qdc_preclose_proxy'] = src['qdc_d0_close']
                p['close'] = src['qdc_d0_close']
                p['d1_preclose'] = src['qdc_d0_close']
                p['d1_close'] = src['qdc_d1_close']
                p['d2_preclose'] = src['qdc_d1_close']
                p['d2_close'] = src['qdc_d2_close']
                picked.append(p)
    result = pd.DataFrame(picked)
    if len(result):
        result = result[result[['close', 'd1_close', 'd2_close', 'entry']].notna().all(axis=1)].copy()
    return result, pd.DataFrame(decisions)


def summarize(signals: pd.DataFrame, calendar: list[str], candidates: pd.DataFrame,
              years: list[int]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    from scripts.experiment_b2_recent_loss_resume_r55 import prior_return, should_pause
    first = min(years)
    last = max(years)
    start = f'{first}-01-01'
    end = max(signals.d2_date) if len(signals) else f'{last}-12-31'
    shadow, _, nav, _, bad = replay(signals, calendar, start=start, end=end, fee_rate=.001)
    if bad:
        raise ValueError(f'account excluded {len(bad)} signals: {bad[:3]}')
    counts = signals.groupby('model_month').size().to_dict()
    nav = nav.set_index('date')
    gate = []
    months = sorted(candidates.model_month.unique())
    for month in months:
        prior = str(pd.Period(month) - 1)
        r5 = prior_return(nav, prior, 5)
        r10 = prior_return(nav, prior, 10)
        count = counts.get(prior, 0) if prior in months else None
        paused = bool(should_pause(count, r5, r10, 'sum_negative')) if count is not None else False
        gate.append(dict(model_month=month, prior_month=prior, prior_candidates=count,
                         prior_r5=r5, prior_r10=r10, paused=paused))
    gates = pd.DataFrame(gate)
    allowed = signals.merge(gates[['model_month', 'paused']], on='model_month', validate='many_to_one')
    allowed = allowed[~allowed.paused].copy()
    yearly = []
    ledgers = []
    for year in years:
        for name, frame in [('shadow', signals), ('V2', allowed)]:
            subset = frame[frame.model_month.str[:4].eq(str(year))]
            if subset.empty:
                continue
            result, _, _, ledger, bad = replay(subset, calendar, start=f'{year}-01-01',
                end=max(subset.d2_date), fee_rate=.001)
            if bad:
                raise ValueError((year, name, bad[:3]))
            yearly.append(dict(year=year, scenario=name, candidates=int(candidates.d0_date.str[:4].eq(str(year)).sum()),
                signals=len(subset), return_=result['total_return'], drawdown=result['max_drawdown'],
                win_rate=result['closed_win_rate'], excluded=len(bad), paused_months=int(gates[
                    gates.model_month.str[:4].eq(str(year))].paused.sum())))
            ledger['year'] = year; ledger['scenario'] = name
            ledgers.append(ledger)
    return pd.DataFrame(yearly), gates, pd.concat(ledgers, ignore_index=True), shadow


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--years', nargs='+', type=int, required=True)
    parser.add_argument('--cache', action='store_true')
    args = parser.parse_args()
    years = sorted(set(args.years))
    OUT.mkdir(parents=True, exist_ok=True)
    candidates, calendar, frozen = load_frozen()
    candidates = candidates[candidates.d0_date.str[:4].astype(int).isin(years)].copy()
    extra_years = sorted(set(years) | {int(y) for y in candidates.d1_date.str[:4]} |
                         {int(y) for y in candidates.d2_date.str[:4]})
    if args.cache:
        daily = pd.read_pickle(OUT / 'qdc_candidate_daily.pkl')
        minute = pd.read_pickle(OUT / 'qdc_candidate_first10.pkl')
    else:
        daily, minute = prepare_data(candidates, extra_years)
    rounded_trade_rows = int((minute.volume.eq(0) & minute.amount.gt(0)).sum())
    candidates = attach_daily(candidates, daily)
    rules = {}
    for path in RULE_PATHS:
        rules.update({item['month']: item['selected'] for item in json.loads(path.read_text())})
    signals, decisions = choose(candidates, minute, rules)
    yearly, gates, ledger, shadow = summarize(signals, calendar, candidates, years)
    candidates.to_pickle(OUT / 'candidates.pkl')
    signals.to_pickle(OUT / 'signals.pkl')
    decisions.to_csv(OUT / 'decisions.csv', index=False)
    yearly.to_csv(OUT / 'yearly.csv', index=False)
    gates.to_csv(OUT / 'gates.csv', index=False)
    ledger.to_csv(OUT / 'ledger.csv', index=False)
    manifest = dict(years=years, model='B2_V2', definition_sha256='54ebe0150dfefa6ce9837d403fd83b331f2720ad835271525e3106aae7529f00',
        method='frozen D0 Top3 RF scores and four-K rules; QDC D1 first10 and D0/D1/D2 daily closes',
        retrained=False, qdc_files={str(p): digest(p) for year in extra_years for p in
            (([QDC / '日线' / f'{year}.parquet', QDC / '1分钟' / f'{year}.parquet'] if year < 2026 else
              [*sorted((QDC / '日线').glob('2026-*.parquet')),
               *sorted((QDC / '1分钟').glob('2026-*.parquet'))]))},
        frozen=frozen, shadow=shadow,
        counts=dict(candidates=len(candidates), d0_gap_over_1c=int(candidates.d0_close_gap.gt(.011).sum()),
            d1_gap_over_1c=int(candidates.d1_close_gap.gt(.011).sum()),
            d2_gap_over_1c=int(candidates.d2_close_gap.gt(.011).sum()),
            missing_daily=int(candidates[['qdc_d0_close','qdc_d1_close','qdc_d2_close']].isna().any(axis=1).sum()),
            minute_rows=len(minute), rounded_trade_rows=rounded_trade_rows,
            signals=len(signals)))
    (OUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + '\n')
    print(yearly.to_string(index=False), flush=True)
    print('counts', manifest['counts'], flush=True)


if __name__ == '__main__':
    main()
