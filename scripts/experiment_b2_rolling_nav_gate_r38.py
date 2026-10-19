"""Self-consistent 20-trading-day NAV gate on frozen B2 D1 signals."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.replay_limit_rebound_cash_r23 import replay, dump, sha


SOURCE = Path('outputs/stock_automl/b2_two_month_2024_r35_20261019')
CALENDAR = Path('outputs/stock_automl/b2_full_2024_r34_20261019/calendar.json')
OUT = Path('docs/stock_automl_runs/20261019_b2_rolling20_gate_r38')
END = '2026-09-30'
FEE = .001
INITIAL = 1_000_000.
CASES = [('B2原策略', None, None), ('上月≤−10%', 'prior_month', -.10),
         ('20日≤−5%', 20, -.05),
         ('20日≤−10%', 20, -.10), ('20日≤−15%', 20, -.15),
         ('20日影子≤−10%', 'shadow20', -.10),
         ('10日≤−10%', 10, -.10), ('30日≤−10%', 30, -.10)]
STARTS = [('全期连续', '2024-03-01'), ('2025五月重置', '2025-05-01'),
          ('2026一月重置', '2026-01-01')]


def simulate(frame: pd.DataFrame, calendar: list[str], start: str, end: str,
             lookback: int | str | None, threshold: float | None):
    """Decide each morning from this gated account's own previous closes."""
    dates = [d for d in calendar if start <= d <= end]
    date_index = {d: i for i, d in enumerate(calendar)}
    signal = frame[frame.d1_date.between(start, end)].sort_values('d1_date')
    assert not signal.d1_date.duplicated().any()
    by_day = {r['d1_date']: r for r in signal.to_dict('records')}
    # R35's canonical replay has no ex-post price exclusions over this slice.
    baseline, _, paper_nav, _, excluded = replay(signal, calendar, start=start, end=end,
                                                 initial=INITIAL, fee_rate=FEE)
    assert not excluded and baseline['entries'] == len(signal)
    shadow_history = [INITIAL, *paper_nav.nav.to_list()]
    cash = INITIAL
    history = [INITIAL]  # NAV before the first market day is the first 20-day anchor.
    positions: list[dict] = []
    decisions: list[dict] = []
    chosen_days: set[str] = set()
    blocked_days = 0
    current_month = None
    month_start_nav = INITIAL
    previous_month_return = None
    for i, day in enumerate(dates):
        if day[:7] != current_month:
            if current_month is not None:
                previous_month_return = history[-1]/month_start_nav-1
            current_month = day[:7]
            month_start_nav = history[-1]
        observed = (previous_month_return if lookback == 'prior_month' else
                    None if lookback == 'shadow20' and i < 20 else
                    shadow_history[i]/shadow_history[i-20]-1 if lookback == 'shadow20' else
                    None if lookback is None or i < lookback else
                    history[-1]/history[-lookback-1]-1)
        blocked = bool(observed is not None and observed <= threshold)
        blocked_days += int(blocked)
        row = by_day.get(day)
        if row is not None:
            counterfactual = row['d2_close']/row['entry']*(1-FEE)/(1+FEE)-1
            decision = dict(date=day, d0_date=row['d0_date'], symbol=row['symbol'],
                            d0_rank=int(row['d0_rank']), score=float(row['score']),
                            rule=str(row['rule_id']), entry_time=str(row['entry_time']),
                            buy_price=float(row['entry']), d2_close=float(row['d2_close']),
                            d2_net_return=float(counterfactual),
                            rolling_return=None if observed is None else float(observed),
                            gate_blocked=int(blocked), action='跳过' if blocked else '买入',
                            d0_amount_100m=float(row['d0_amount_100m']) if pd.notna(row['d0_amount_100m']) else None,
                            d0_turnover_pct=float(row['d0_turnover_pct']) if pd.notna(row['d0_turnover_pct']) else None)
            decisions.append(decision)
            if not blocked:
                assert len(positions) <= 1
                budget = max(0., cash)*(1. if positions else .5)
                assert budget > 1e-8
                buy_amount = budget/(1+FEE)
                positions.append(dict(start_i=date_index[day], shares=buy_amount/row['entry'],
                                      row=row, cost=budget))
                cash -= budget
                chosen_days.add(day)
        keep = []
        market_value = 0.
        for lot in positions:
            k = date_index[day] - lot['start_i'] + 1
            assert k in (1, 2)
            value = lot['shares']*lot['row'][f'd{k}_close']
            if k == 2:
                cash += value*(1-FEE)
            else:
                market_value += value
                keep.append(lot)
        positions = keep
        nav = cash + market_value
        assert nav > 0 and cash >= -1e-7
        history.append(nav)
    chosen = signal[signal.d1_date.isin(chosen_days)]
    canonical, monthly, nav, ledger, missing = replay(chosen, calendar, start=start, end=end,
                                                      initial=INITIAL, fee_rate=FEE)
    assert not missing
    assert len(chosen) + sum(d['gate_blocked'] for d in decisions) == len(signal)
    np.testing.assert_allclose(nav.nav.to_numpy(), np.asarray(history[1:]), rtol=0, atol=1e-7)
    assert np.isclose(canonical['final_nav'], history[-1], rtol=0, atol=1e-7)
    return canonical, monthly, nav, ledger, decisions, blocked_days


def metrics(frame: pd.DataFrame) -> dict:
    known = frame[frame.exit_date.notna()]
    return {'已卖笔': int(len(known)), 'D2笔均': float(known.return_.mean()) if len(known) else None,
            'D2胜率': float(known.return_.gt(0).mean()) if len(known) else None,
            '亏超5%': float(known.return_.lt(-.05).mean()) if len(known) else None}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    calendar = json.loads(CALENDAR.read_text())
    signals = pd.read_pickle(SOURCE/'signals.pkl')
    assert len(signals) == len(signals.drop_duplicates('d1_date'))
    summary_rows, month_rows, signal_rows, daily_rows = [], [], [], []
    for scope, start in STARTS:
        for label, days, threshold in CASES:
            result, months, nav, ledger, decisions, blocked_days = simulate(
                signals, calendar, start, END, days, threshold)
            skipped = [x['d2_net_return'] for x in decisions if x['gate_blocked']]
            summary_rows.append({'账户': scope, '方案': label, '起点': start,
                                 '窗口日': days, '暂停阈值': threshold,
                                 '停手交易日': blocked_days, '跳过信号': sum(x['gate_blocked'] for x in decisions),
                                 '跳过D2笔均': float(np.mean(skipped)) if skipped else None,
                                 '跳过D2胜率': float(np.mean(np.asarray(skipped) > 0)) if skipped else None,
                                 **result, **metrics(ledger)})
            month_rows.extend({'账户': scope, '方案': label, **row} for row in months)
            signal_rows.extend({'账户': scope, '方案': label, **row} for row in decisions)
            daily_rows.extend({'账户': scope, '方案': label, **row} for row in nav.to_dict('records'))
    base = next(x for x in summary_rows if x['账户'] == '全期连续' and x['方案'] == 'B2原策略')
    prior = pd.read_pickle(SOURCE/'nav.pkl')
    prior = prior[prior.scope.eq('完整两月2024起') & prior.date.le(END)]
    observed = pd.DataFrame([x for x in daily_rows if x['账户'] == '全期连续' and x['方案'] == 'B2原策略'])
    assert len(prior) == len(observed)
    np.testing.assert_allclose(prior.nav.to_numpy(), observed.nav.to_numpy(), rtol=0, atol=0)
    assert base['entries'] == 404 and base['total_return'] < 0
    dump(OUT/'specification.json', {
        'name': 'R38_B2_rolling_NAV_gate', 'strategy': 'frozen D2S2 B2, no refit or rule change',
        'strategy_registry': 'kingdomai.automl_strategy_definition / imac / B2 / v1.0',
        'strategy_definition_sha256': '157b9d77bb9951214b0978365cec407320b1d5663636b80fe60939ae94172c0c',
        'experiment_code_sha256': sha(Path(__file__)),
        'source_signals': str(SOURCE/'signals.pkl'), 'source_sha256': sha(SOURCE/'signals.pkl'),
        'calendar': str(CALENDAR), 'calendar_sha256': sha(CALENDAR),
        'lookback': 'At D1 before buy: prior close NAV divided by NAV 20 completed market days earlier minus 1; initial NAV anchors first 20 days; current day never used.',
        'gate': 'If 20-day return <= threshold, skip all new purchases that day. Existing positions are sold on original D2 close. Gate uses its own iteratively recomputed NAV; no signal replacement.',
        'primary': '20 trading days, -10% cutoff copied from exploratory prior calendar-month loss idea, not optimized on target outcomes',
        'calendar_month_comparator': 'At first market day of each month, prior full calendar-month return of this own gated account; <=-10% blocks all buys that month.',
        'shadow_comparator': 'Same 20-day -10% gate, but rolling return is from a non-trading full-B2 paper NAV that continues tracking every frozen signal; only closes before current D1 are read.',
        'sensitivity': '20d -5/-15%, 10/30d -10%, inspected only; no best-of-test selection',
        'account': '1m initial, flat half cash or carried all remaining cash; 0.1% each side; no integer share/queue/slippage',
        'period': [STARTS[0][1], END], 'reset_robustness': STARTS[1:],
        'scope': 'historical research, not unseen blind test or production strategy change',
        'db_status': 'research output not inserted into production registry',
        'cloud_status': 'local only; no independent cloud readback'})
    dump(OUT/'summary.json', summary_rows)
    dump(OUT/'monthly.json', month_rows)
    dump(OUT/'signals.json', signal_rows)
    dump(OUT/'daily.json', daily_rows)
    for row in summary_rows:
        print(row['账户'], row['方案'],row['entries'],f"{row['total_return']:+.2%}",
              f"DD {row['max_drawdown']:.2%}",f"Sharpe {row['sharpe']:.2f}",
              'skipped',row['跳过信号'])


if __name__ == '__main__':
    main()
