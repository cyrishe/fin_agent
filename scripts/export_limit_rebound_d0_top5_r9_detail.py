"""Prepare a compact, auditable D0 Top5 / D1-open RF detail workbook source."""

from __future__ import annotations

import json
from bisect import bisect_right
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from scripts.analyze_limit_rebound_effect_r9 import R8
from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.experiment_automl_limit_rebound_d0_open_r5 import load_daily
from scripts.research_limit_rebound_d1_minute_r6 import minute_facts
from scripts.mk_minute_archive import day_file


OUT = Path('outputs/stock_automl/limit_rebound_effect_r9_20261014')
R7_MINUTE = Path('outputs/stock_automl/limit_rebound_train_valid_r7_20261014/minute_facts.csv.gz')
R6_MINUTE = Path('outputs/stock_automl/limit_rebound_d1_minute_r6_20261014/minute_facts_v2.csv.gz')
R4_SHARES = Path('outputs/stock_automl/limit_rebound_st_size_r4_20261014/share_months')


def chosen_top5() -> pd.DataFrame:
    parts = []
    for part in ('valid', 'test'):
        panel = pd.read_pickle(R8 / f'{part}_all.pkl')
        scores = pd.read_csv(R8 / f'{part}_scores_forest_sk171.csv.gz',
                             dtype={'symbol': str, 'd0_date': str})
        panel = panel.merge(scores[['symbol', 'd0_date', 'score']],
                            on=['symbol', 'd0_date'], validate='one_to_one')
        panel = panel.sort_values(['d0_date', 'score', 'symbol'],
                                  ascending=[True, False, True])
        panel['d0_rank'] = panel.groupby('d0_date').cumcount() + 1
        panel = panel[panel.d0_rank.le(5)].copy()
        panel['part'] = part
        parts.append(panel)
    result = pd.concat(parts, ignore_index=True)
    assert len(result) == 615 and result.groupby(['part', 'd0_date']).size().eq(5).all()
    result['day_seq'] = result.groupby('part').d0_date.transform(
        lambda s: pd.factorize(s, sort=True)[0] + 1)
    result['selected'] = False
    active = result[result.activated].sort_values(['part', 'd1_date', 'd0_rank'])
    winner = active.groupby(['part', 'd1_date']).head(1).index
    result.loc[winner, 'selected'] = True
    assert result.groupby(['part', 'd1_date']).selected.sum().le(1).all()
    return result


def load_quotes(f: pd.DataFrame) -> pd.DataFrame:
    daily = load_daily()
    quotes = daily.set_index(['symbol', 'date_i'])[['close', 'preclose', 'up']]
    original_d1_close = f.d1_close.to_numpy(copy=True)
    for gap in range(1, 5):
        keys = pd.MultiIndex.from_arrays([f.symbol.to_numpy(), f.date_i.to_numpy() + gap])
        q = quotes.reindex(keys)
        f[f'd{gap}_close'] = q.close.to_numpy()
        f[f'd{gap}_preclose'] = q.preclose.to_numpy()
        f[f'd{gap}_up'] = q.up.to_numpy()
    assert np.isclose(original_d1_close, f.d1_close, equal_nan=True).all()
    return f


def market_cap(f: pd.DataFrame) -> pd.DataFrame:
    # Pricevaluate is sparse; use last observed share count times D0 close where needed.
    cache = OUT / 'd0_top5_market_cap_source.csv.gz'
    if cache.exists():
        direct = pd.read_csv(cache, dtype={'symbol': str, 'd0_date': str})
    else:
        load_dotenv('.env')
        with db_connection() as conn:
            direct = query(conn, 'SELECT trade_date,stk_code,total_mv '
                           'FROM kcrp_stock_pricevaluate WHERE trade_date BETWEEN %s AND %s',
                           (f.d0_date.min(), f.d0_date.max()))
            conn.rollback()
        direct['d0_date'] = direct.trade_date.astype(str)
        direct['symbol'] = direct.stk_code.astype(str).str[:6]
        keys = pd.MultiIndex.from_frame(f[['symbol', 'd0_date']])
        direct = direct[pd.MultiIndex.from_frame(direct[['symbol', 'd0_date']]).isin(keys)]
        direct = direct[['symbol', 'd0_date', 'total_mv']]
        assert not direct.duplicated(['symbol', 'd0_date']).any()
        direct.to_csv(cache, index=False, compression='gzip')
    f = f.merge(direct, on=['symbol', 'd0_date'], how='left', validate='one_to_one')
    history = pd.concat((pd.read_csv(path, dtype={'symbol': str, 'trade_date': str})
                         for path in sorted(R4_SHARES.glob('total_share_*.csv.gz'))),
                        ignore_index=True)
    history = history.sort_values(['symbol', 'trade_date'])
    lookup = {symbol: (group.trade_date.tolist(), group.total_share.to_numpy(float))
              for symbol, group in history.groupby('symbol')}
    estimates, age = [], []
    for symbol, day, close in zip(f.symbol, f.d0_date, f.close):
        dates, shares = lookup[symbol]
        pos = bisect_right(dates, day) - 1
        estimates.append(float(shares[pos] * close) if pos >= 0 else np.nan)
        age.append(int((pd.Timestamp(day) - pd.Timestamp(dates[pos])).days) if pos >= 0 else None)
    direct_mv = pd.to_numeric(f.total_mv, errors='coerce')
    f['mv_estimated'] = ~direct_mv.gt(0)
    f['market_cap_100m'] = direct_mv.where(direct_mv.gt(0), estimates) / 1e8
    f['share_age_days'] = age
    return f


def get_minutes(f: pd.DataFrame) -> pd.DataFrame:
    local = pd.concat((pd.read_csv(path, dtype={'symbol': str, 'd1_date': str})
                       for path in (R7_MINUTE, R6_MINUTE)), ignore_index=True)
    local = local.drop_duplicates(['symbol', 'd1_date'])
    local = local[local.d1_date.isin(f.d1_date)]
    have = set(zip(local.d1_date, local.symbol))
    demand = f[[(d, s) not in have for d, s in zip(f.d1_date, f.symbol)]]
    cache = OUT / 'd0_top5_extra_minute_facts.csv.gz'
    if cache.exists():
        extra = pd.read_csv(cache, dtype={'symbol': str, 'd1_date': str})
    else:
        rows = []
        load_dotenv('.env')
        with db_connection() as conn:
            for n, (day, group) in enumerate(demand.groupby('d1_date'), 1):
                symbols = sorted(group.symbol.unique())
                placeholders = ','.join(['%s'] * len(symbols))
                bars = query(conn,
                    'SELECT stk_code AS symbol,bar_end_time AS time,open_price AS open,'
                    'high_price AS high,low_price AS low,latest_price AS close,volume '
                    'FROM aiia_stock_realtime_minute_snapshot_full '
                    "WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1 "
                    'AND is_finalized=1 AND is_fallback=0 AND source_snapshot_time=bar_end_time '
                    f'AND stk_code IN ({placeholders})', (day, *symbols))
                if bars.empty:
                    continue
                for symbol, sbars in bars.groupby('symbol'):
                    row = group[group.symbol.eq(str(symbol)[:6])].iloc[0]
                    sbars = sbars.copy()
                    for col in ('open', 'high', 'low', 'close', 'volume'):
                        sbars[col] = pd.to_numeric(sbars[col], errors='coerce')
                    sbars['volume'] *= 100
                    facts = minute_facts(sbars, float(row.d1_cap), float(row.d1_close))
                    if facts is None or not np.isclose(facts['minute_open'], row.d1_open, atol=.005):
                        continue
                    rows.append({'d1_date': day, 'symbol': str(symbol)[:6], **facts})
                if n % 10 == 0:
                    print(f'minute days {n}/{demand.d1_date.nunique()}, valid {len(rows)}', flush=True)
            conn.rollback()
        extra = pd.DataFrame(rows, columns=local.columns)
        extra.to_csv(cache, index=False, compression='gzip')
    all_minutes = pd.concat([local, extra], ignore_index=True)
    local_cache = OUT / 'd0_top5_local_minute_facts.csv.gz'
    if local_cache.exists():
        local_extra = pd.read_csv(local_cache, dtype={'symbol': str, 'd1_date': str})
    else:
        have = set(zip(all_minutes.d1_date, all_minutes.symbol))
        missing = f[[(d, s) not in have for d, s in zip(f.d1_date, f.symbol)]]
        rows = []
        for day, group in missing.groupby('d1_date'):
            try:
                path = day_file(Path('outputs/stock_automl/mk_1m_compact'), day)
            except FileNotFoundError:
                continue
            symbols = set(group.symbol)
            chunks = []
            for chunk in pd.read_csv(path, dtype={'symbol': str}, chunksize=150_000):
                take = chunk[chunk.symbol.str[:6].isin(symbols)]
                if not take.empty:
                    chunks.append(take)
            if not chunks:
                continue
            for symbol, sbars in pd.concat(chunks, ignore_index=True).groupby('symbol'):
                short = symbol[:6]
                row = group[group.symbol.eq(short)].iloc[0]
                facts = minute_facts(sbars, float(row.d1_cap), float(row.d1_close))
                if facts is None or not np.isclose(facts['minute_open'], row.d1_open, atol=.005):
                    continue
                rows.append({'d1_date': day, 'symbol': short, **facts})
        local_extra = pd.DataFrame(rows, columns=local.columns)
        local_extra.to_csv(local_cache, index=False, compression='gzip')
    all_minutes = pd.concat([all_minutes, local_extra], ignore_index=True)
    duplicate = all_minutes[all_minutes.duplicated(['d1_date', 'symbol'], keep=False)]
    for _, group in duplicate.groupby(['d1_date', 'symbol']):
        for col in ('minute_open', 'first_close_cap_min', 'final_close_cap_min'):
            if group[col].fillna(-1).nunique() != 1:
                raise ValueError(f'Conflicting minute facts: {group.iloc[0][["d1_date", "symbol"]].to_dict()}')
    all_minutes = all_minutes.drop_duplicates(['d1_date', 'symbol'])
    return f.merge(all_minutes[['d1_date', 'symbol', 'first_close_cap_min',
                                'final_close_cap_min', 'minute_open']],
                   on=['d1_date', 'symbol'], how='left', validate='one_to_one')


def minute_clock(index: float) -> str | None:
    if pd.isna(index):
        return None
    i = int(index)
    if i < 1 or i > 240:
        raise ValueError(index)
    minutes = 9 * 60 + 30 + i if i <= 120 else 13 * 60 + (i - 120)
    return f'{minutes // 60:02d}:{minutes % 60:02d}'


def exit_gross(up: list, observed: list[bool], price_returns: list) -> float | None:
    """First D2-D4 sealed-close return, otherwise observed D4-close return."""
    first_after_d1 = next((i for i in range(2, 5) if observed[i-1] and bool(up[i-1])), None)
    return (price_returns[first_after_d1-2] if first_after_d1 is not None
            else price_returns[2] if observed[3] else None)


def records(f: pd.DataFrame) -> list[dict]:
    rows = []
    for r in f.itertuples():
        up = [getattr(r, f'd{i}_up') for i in range(1, 5)]
        observed = [pd.notna(value) for value in up]
        first = next((i for i, value in enumerate(up, 1) if value is True or value == True), None)
        if first is not None:
            cooling = first - 1
            end_price = r.d1_open if first == 1 else getattr(r, f'd{first-1}_close')
            cooling_return = end_price / r.d1_open - 1
        else:
            cooling = '未结束' if all(observed) else '待观察'
            cooling_return = None
        price_returns = [getattr(r, f'd{i}_close') / r.d1_open - 1
                         if pd.notna(getattr(r, f'd{i}_close')) else None
                         for i in range(2, 5)]
        # D1 cannot be sold in A shares. Exit at the first D2-D4 sealed close,
        # or at the D4 close if none occurs and D4 is observed.
        exit_return = exit_gross(up, observed, price_returns)
        rows.append({
            '集合': '验证' if r.part == 'valid' else '测试', '日序': int(r.day_seq),
            'D0名次': int(r.d0_rank), 'D0评分': float(r.score),
            'D1激活': int(bool(r.activated)), 'D1入选': int(bool(r.selected)),
            'D1开板': int(bool(r.d1_open < r.d1_cap - .005)),
            'D1开涨': r.d1_open / r.d1_preclose - 1,
            'D1首封时': minute_clock(r.first_close_cap_min),
            'D1终封时': minute_clock(r.final_close_cap_min) if bool(r.d1_up) else None,
            'D1封板': int(bool(r.d1_up)),
            'D2封板': int(bool(r.d2_up)) if observed[1] else None,
            'D3封板': int(bool(r.d3_up)) if observed[2] else None,
            'D4封板': int(bool(r.d4_up)) if observed[3] else None,
            '冷静天数': cooling, '冷静末涨': cooling_return,
            'D2收涨': price_returns[0], 'D3收涨': price_returns[1],
            'D4收涨': price_returns[2], '策略退出涨': exit_return,
            '总市值亿': float(r.market_cap_100m) if pd.notna(r.market_cap_100m) else None,
            '市值估算': int(bool(r.mv_estimated)), 'D0成交额亿': float(r.d0_amount_100m),
            'D0换手率': float(r.d0_turnover_pct) / 100,
            '原连板数': int(r.board_streak_days), '当时ST': int(bool(r.d1_st)),
        })
    return rows


def run() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    f = chosen_top5()
    f = load_quotes(f)
    f = market_cap(f)
    f = get_minutes(f)
    rows = records(f)
    assert len(rows) == len(f)
    assert sum(r['D1入选'] for r in rows if r['集合'] == '验证') == 57
    assert sum(r['D1入选'] for r in rows if r['集合'] == '测试') == 64
    published = json.loads((OUT / 'summary.json').read_text())
    for part in ('valid', 'test'):
        picked = f[f.part.eq(part) & f.selected]
        expected = published['parts'][part]['d0_top5']['forest_sk171']['1']
        assert len(picked) == expected['days']
        assert int(picked.label_3.sum()) == expected['h3_hits']
        assert np.isclose((picked.d3_close / picked.d1_open - 1).mean(),
                          expected['d3_mean_gross'], atol=1e-12)
    (OUT / 'd0_top5_rf171_workbook_rows.json').write_text(json.dumps(
        rows, ensure_ascii=False, allow_nan=False, indent=2) + '\n')
    f[['part','d0_date','d1_date','symbol','d0_rank','selected',
       'share_age_days','minute_open']].to_csv(OUT / 'd0_top5_rf171_internal_audit.csv.gz',
                                             index=False, compression='gzip')
    print(json.dumps({'rows': len(rows), 'minutes': int(f.minute_open.notna().sum()),
                      'market_cap': int(f.market_cap_100m.notna().sum()),
                      'market_cap_estimated': int(f.mv_estimated.sum()),
                      'd4_observed': int(f.d4_up.notna().sum()),
                      'selected': int(f.selected.sum())}, ensure_ascii=False))


if __name__ == '__main__':
    run()
