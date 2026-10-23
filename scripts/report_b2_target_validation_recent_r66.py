"""Read-only comparison of each target model's validation trades and prior 5 days.

Uses the frozen monthly models and rules. The target rule is applied backward
to its own validation months, so the validation returns are diagnostic and
must never be presented as an independently tradable historical account.
"""
from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn

from scripts import experiment_b2_full_2024_r34 as r34
from scripts import experiment_limit_rebound_four_minutes_r24 as four
from scripts import experiment_limit_rebound_shape_r19 as shape
from scripts.extend_b2_2021_minutes_r60 import MINUTES, source
from scripts.experiment_limit_rebound_segments_r18 import FEATURES
from scripts.experiment_limit_rebound_short_mixed_r12 import xmatrix
from scripts.experiment_limit_rebound_two_stage_r15 import rank
from scripts.export_limit_rebound_short_mixed_r12 import filtered_local
from scripts.mk_minute_archive import COMPACT_ROOT_2025, day_file
from scripts.replay_limit_rebound_cash_r23 import dump, replay, sha

BASE = Path('outputs/stock_automl')
ROOTS = [BASE / x for x in (
    'b2_2021_minute_r60_20261020',
    'b2_2022_minute_r41_20261019',
    'b2_two_month_2024_r35_20261019',
)]
R60 = Path('docs/stock_automl_runs/20261020_b2_2021_minutes_r60')
R61 = Path('docs/stock_automl_runs/20261020_b2_model_month_r61')
R52 = Path('docs/stock_automl_runs/20261020_b2_validation_vs_month_r52')
OUT = Path('docs/stock_automl_runs/20261021_b2_target_validation_r66')
KEY = ['symbol', 'd0_date']
MONTHS = [str(m) for m in pd.period_range('2022-05', '2026-09', freq='M')]


def fmt_return(x):
    return '—' if x is None or pd.isna(x) else f'{x:+.2%}'


def fmt_win(x, n):
    return f'{x:.0%}（{n}）' if x is not None and not pd.isna(x) else f'—（{n}）'


def daily_high_low(symbol: str, day: str, manifests: dict) -> tuple[float, float]:
    item = manifests[day[:7]]
    frame = pd.read_csv(item['path'], usecols=['stk_code', 'trade_date', 'high', 'low'],
                        dtype={'stk_code': str, 'trade_date': str})
    row = frame[frame.stk_code.str[:6].eq(symbol) & frame.trade_date.eq(day)]
    assert len(row) == 1, (symbol, day)
    return float(row.iloc[0].high), float(row.iloc[0].low)


def raw_bars(symbol: str, day: str) -> pd.DataFrame:
    if day < '2025-01-01':
        original = r34.raw_minute_path
        try:
            if day < '2023-01-01':
                r34.raw_minute_path = lambda d: MINUTES / d[:4] / f'{d.replace("-", "")}.csv.gz'
            return r34.tushare_bars(day, {symbol}).get(symbol, pd.DataFrame())
        finally:
            r34.raw_minute_path = original
    path = day_file(COMPACT_ROOT_2025, day)
    raw = filtered_local(path, {symbol})
    return raw[raw.symbol.str[:6].eq(symbol)].assign(symbol=symbol).sort_values('time').reset_index(drop=True)


def build_missing(row: pd.Series, manifests: dict) -> tuple[pd.Series, pd.DataFrame | None]:
    assert row.d0_st == 0 and row.d1_st == 0, (row.symbol, row.d0_date)
    b = raw_bars(row.symbol, row.d1_date)
    high, low = daily_high_low(row.symbol, row.d1_date, manifests)
    data = row.to_dict()
    data.update(state0='N', state1='N', ordinary=True, d1_high=high, d1_low=low)
    input_row = pd.Series(data)
    facts = shape.facts(b, input_row)
    data.update(facts)
    data['eligible'] = bool(facts['audit_ok'])
    if facts['audit_ok']:
        data.update(four.first_features(b, row.d1_preclose, row.d1_cap, 4))
        data.update(four.entry_facts(b, row.d1_cap, 4))
        bars = b.iloc[:10].assign(d0_date=row.d0_date)
    else:
        bars = None
    return pd.Series(data), bars


def account(trades: pd.DataFrame, calendar: list[str], start: str, end: str) -> tuple[float, pd.DataFrame, pd.DataFrame]:
    summary, _, nav, ledger, excluded = replay(trades, calendar, start=start, end=end,
                                                initial=1_000_000., fee_rate=.001)
    assert not excluded and summary['entries'] == len(trades[trades.d1_date.between(start, end)]), excluded
    return float(summary['total_return']), nav, ledger


def closed_stats(trades: pd.DataFrame) -> tuple[int, float | None]:
    sold = trades[trades.exit_date.notna()] if len(trades) else trades
    count = len(sold)
    return count, float(sold.return_.mean()) if count else None


def window(nav: pd.DataFrame, ledger: pd.DataFrame, days: list[str]) -> tuple[float, int, float | None]:
    end = nav.index[nav.date.eq(days[-1])][0]
    first = nav.index[nav.date.eq(days[0])][0]
    assert first > 0
    gain = float(nav.iloc[end].nav / nav.iloc[first - 1].nav - 1)
    sold = ledger[ledger.exit_date.isin(days)] if len(ledger) else ledger
    n = len(sold)
    win = float(sold.return_.gt(0).mean()) if n else None
    return gain, n, win


def main():
    assert sklearn.__version__ == '1.7.1', sklearn.__version__
    OUT.mkdir(parents=True, exist_ok=True)
    panel = pd.read_pickle(ROOTS[0] / 'panel.pkl')
    calendar = json.loads((ROOTS[0] / 'calendar.json').read_text())
    end_by_month = {month: group[-1] for month, group in
                    pd.Series(calendar).groupby(pd.Series(calendar).str[:7]).agg(list).items()}
    raw_enriched = pd.concat([pd.read_pickle(root / 'enriched.pkl') for root in ROOTS], ignore_index=True)
    templates = raw_enriched.drop_duplicates(KEY).set_index(KEY, drop=False)
    first10 = pd.concat([pd.read_pickle(root / 'first10.pkl') for root in ROOTS], ignore_index=True)
    first10['symbol'] = first10.symbol.str[:6]
    first10 = first10.drop_duplicates(KEY + ['time'])
    bars = {(symbol, day): g.sort_values('time') for (symbol, day), g in first10.groupby(KEY)}
    rules = {x['month']: x for root in ROOTS
             for x in json.loads((root / 'rules_frozen.json').read_text())}
    assert all(month in rules for month in MONTHS)
    manifests = {x['month']: x for x in json.loads((ROOTS[0] / 'daily_sources.json').read_text())}
    manifests.update({x['month']: x for x in json.loads((r34.OUT / 'daily_sources.json').read_text())['months']})
    for item in json.loads((BASE / 'limit_rebound_segments_r18_20261015' / 'source_manifest.json').read_text())['raw_sources']:
        month = Path(item['path']).name.removeprefix('price_').removesuffix('.csv.gz')
        manifests[month] = item
    model_month_rows = {x['模型月']: x for x in json.loads((R61 / '模型归属.json').read_text())['月模型']}
    shadow_signals = pd.read_pickle(ROOTS[0] / 'signals.pkl')
    shadow_signals = shadow_signals[shadow_signals.d0_date.le('2026-09-30')]
    _, shadow_nav, shadow_ledger = account(shadow_signals, calendar, '2022-05-01', '2026-09-30')
    saved_nav = pd.read_csv(R60 / '逐日净值.csv')
    np.testing.assert_allclose(shadow_nav.nav, saved_nav['原B2净值'], atol=1e-8, rtol=0)

    rows = []
    detail = []
    added = []
    mature_controls = []
    for month in MONTHS:
        prior = str(pd.Period(month) - 1)
        prior2 = str(pd.Period(month) - 2)
        valid_panel = panel[panel.d0_date.str[:7].isin([prior2, prior])]
        model_path = source(2, month, 'forecast').parent / 'segments.joblib'
        model = joblib.load(model_path)['model']
        scored = rank(valid_panel, model.predict_proba(xmatrix(valid_panel, FEATURES['segments']))[:, 1])
        selected = scored[scored.d0_rank.eq(1)].copy()
        assert selected.d0_date.is_unique
        records = []
        for candidate in selected.itertuples(index=False):
            key = (candidate.symbol, candidate.d0_date)
            if key in templates.index:
                row = templates.loc[key].copy()
                row['score'] = candidate.score
                row['d0_rank'] = 1
                row['model_month'] = month
                row['part'] = 'valid_full'
            else:
                row, b = build_missing(pd.Series(candidate._asdict()), manifests)
                row['model_month'] = month
                row['part'] = 'valid_full'
                row['observation_bars'] = 4
                row = four.reprice(pd.DataFrame([row])).iloc[0]
                if b is not None:
                    assert len(b) == 10
                    bars[key] = b
                added.append(dict(target=month, d0=candidate.d0_date, symbol=candidate.symbol,
                                  audit_ok=bool(row.audit_ok), reason=row.reason))
            records.append(row)
        full = pd.DataFrame(records)
        assert full.d0_date.is_unique
        trades = four.delay_same_stock(four.chosen(full, rules[month]['selected']), bars)
        trades['return_net'] = trades.d2_close / trades.entry * .999 / 1.001 - 1
        assert trades.d0_date.is_unique and trades.d1_date.is_unique
        for item in trades.itertuples():
            detail.append(dict(目标月=month, D0=item.d0_date, D1=item.d1_date,
                               D2=calendar[calendar.index(item.d1_date) + 1], 股票=item.symbol,
                               费后收益=float(item.return_net)))
        total2, nav2, ledger2 = account(trades, calendar, prior2 + '-01', end_by_month[prior])
        one = trades[trades.d0_date.str.startswith(prior)].copy()
        total1, _, ledger1 = account(one, calendar, prior + '-01', end_by_month[prior])
        recent_nav, recent_ledger = nav2, ledger2
        prior_days = [d for d in calendar if d.startswith(prior)]
        last5_days, last10_days = prior_days[-5:], prior_days[-10:]
        assert len(last5_days) == 5 and len(last10_days) == 10
        new5, new5_n, new5_win = window(recent_nav, recent_ledger, last5_days)
        new10, new10_n, new10_win = window(recent_nav, recent_ledger, last10_days)
        if prior >= '2022-05':
            old5, old5_n, old5_win = window(shadow_nav, shadow_ledger, last5_days)
            old10, old10_n, old10_win = window(shadow_nav, shadow_ledger, last10_days)
        else:
            old5, old5_n, old5_win = None, 0, None
            old10, old10_n, old10_win = None, 0, None
        two_n, two_mean = closed_stats(ledger2)
        one_n, one_mean = closed_stats(ledger1)
        assert two_n <= len(trades) and one_n <= len(one)
        month_return = float(model_month_rows[month]['原模型独立收益'])
        rows.append(dict(月份=month, 月收益率=month_return,
                         验证2月收益=total2, 验证2月笔数=two_n, 验证2月笔均=two_mean,
                         验证后1月收益=total1, 验证后1月笔数=one_n, 验证后1月笔均=one_mean,
                         影子末5收益=old5, 影子末5胜率=old5_win, 影子末5已卖=old5_n,
                         影子末10收益=old10, 影子末10胜率=old10_win, 影子末10已卖=old10_n,
                         旧模末5加10收益和=None if old5 is None else old5 + old10,
                         新模末5收益=new5, 新模末5胜率=new5_win, 新模末5已卖=new5_n,
                         新模末10收益=new10, 新模末10胜率=new10_win, 新模末10已卖=new10_n,
                         新模末5加10收益和=new5 + new10,
                         新模验证Top1候选=len(full), 四K规则=rules[month]['selected']['id']))
        # Existing R52 covers a purged validation set. Its completed trades
        # must be a subset of the full-month simulation at identical prices.
        if month >= '2024-03':
            mature_controls.append(month)
        print(month, two_n, one_n, fmt_return(new5), flush=True)

    result = pd.DataFrame(rows)
    assert len(result) == 53 and result.月份.is_unique
    assert len(added) <= 20
    saved_r52 = pd.read_csv(R52 / '验证期逐笔_延迟买入.csv')
    ours = pd.DataFrame(detail)
    matching = saved_r52.merge(ours, left_on=['目标月', '验证D0'], right_on=['目标月', 'D0'],
                               validate='one_to_one')
    assert len(matching) == len(saved_r52)
    assert matching.验证D1.eq(matching.D1).all()
    np.testing.assert_allclose(matching.D2费后收益, matching.费后收益, atol=1e-12, rtol=0)
    assert result.loc[result.月份.eq('2026-09'), '影子末5收益'].iloc[0] == result.loc[
        result.月份.eq('2026-09'), '影子末5收益'].iloc[0]

    columns = ['月份', '月收益率', '验证2月收益', '验证2月笔数', '验证2月笔均',
               '验证后1月收益', '验证后1月笔数', '验证后1月笔均',
               '影子末5收益', '影子末5胜率', '影子末5已卖',
               '影子末10收益', '影子末10胜率', '影子末10已卖',
               '旧模末5加10收益和',
               '新模末5收益', '新模末5胜率', '新模末5已卖',
               '新模末10收益', '新模末10胜率', '新模末10已卖',
               '新模末5加10收益和',
               '新模验证Top1候选', '四K规则']
    dump(OUT / 'data.json', json.loads(result[columns].to_json(
        orient='records', force_ascii=False, double_precision=15)))
    dump(OUT / '新增分钟审计.json', added)
    dump(OUT / '逐笔.json', detail)
    display = ['| 月份 | 月收益 | 验证2月：收益/笔/笔均 | 验证后1月：收益/笔/笔均 | 影子末5：收益/胜率 | 影子末10：收益/胜率 | 旧模5+10收益和 | 新模末5：收益/胜率 | 新模末10：收益/胜率 | 新模5+10收益和 |',
               '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for row in result.itertuples(index=False):
        two = f'{fmt_return(row.验证2月收益)} / {row.验证2月笔数} / {fmt_return(row.验证2月笔均)}'
        one = f'{fmt_return(row.验证后1月收益)} / {row.验证后1月笔数} / {fmt_return(row.验证后1月笔均)}'
        old5_display = '—' if pd.isna(row.影子末5收益) else f'{fmt_return(row.影子末5收益)} / {fmt_win(row.影子末5胜率, row.影子末5已卖)}'
        old10_display = '—' if pd.isna(row.影子末10收益) else f'{fmt_return(row.影子末10收益)} / {fmt_win(row.影子末10胜率, row.影子末10已卖)}'
        new5_display = f'{fmt_return(row.新模末5收益)} / {fmt_win(row.新模末5胜率, row.新模末5已卖)}'
        new10_display = f'{fmt_return(row.新模末10收益)} / {fmt_win(row.新模末10胜率, row.新模末10已卖)}'
        display.append(f'| {row.月份} | {fmt_return(row.月收益率)} | {two} | {one} | {old5_display} | {old10_display} | {fmt_return(row.旧模末5加10收益和)} | {new5_display} | {new10_display} | {fmt_return(row.新模末5加10收益和)} |')
    (OUT / '月度对照.md').write_text('\n'.join(display) + '\n')
    dump(OUT / 'audit.json', dict(months=len(result), target_signals=len(shadow_signals),
         new_validation_trades=len(detail), newly_enriched=len(added),
         r52_existing_trades_matched=len(matching), old_shadow_nav_replayed=True,
         model_scoring_sklearn=sklearn.__version__, model_rule_post_selection_diagnostic=True,
         source_sha256={str(p): sha(p) for p in [ROOTS[0] / 'panel.pkl',
             ROOTS[0] / 'signals.pkl', R61 / '模型归属.json', R52 / '验证期逐笔_延迟买入.csv']}))


if __name__ == '__main__':
    main()
