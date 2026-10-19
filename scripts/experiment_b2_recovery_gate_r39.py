"""Compare B2 30-day loss gates with a causal 10-day recovery condition."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.experiment_b2_rolling_nav_gate_r38 import (
    CALENDAR, END, FEE, INITIAL, SOURCE, STARTS, metrics,
)
from scripts.replay_limit_rebound_cash_r23 import dump, replay, sha


OUT = Path('docs/stock_automl_runs/20261019_b2_recovery_gate_r39')
CASES = [
    ('B2原策略', None, None),
    ('30日单门槛', 'own', None),
    ('30日+真实10日', 'own', 'own'),
    ('30日+影子10日', 'own', 'shadow'),
    ('影子30日+影子10日', 'shadow', 'shadow'),
]


def simulate(frame: pd.DataFrame, calendar: list[str], start: str,
             thirty_source: str | None, ten_source: str | None):
    dates = [d for d in calendar if start <= d <= END]
    date_index = {d: i for i, d in enumerate(calendar)}
    signal = frame[frame.d1_date.between(start, END)].sort_values('d1_date')
    assert not signal.d1_date.duplicated().any()
    by_day = {r['d1_date']: r for r in signal.to_dict('records')}
    baseline, _, paper_nav, _, excluded = replay(
        signal, calendar, start=start, end=END, initial=INITIAL, fee_rate=FEE)
    assert not excluded and baseline['entries'] == len(signal)
    shadow_history = [INITIAL, *paper_nav.nav.to_list()]
    cash = INITIAL
    own_history = [INITIAL]
    positions: list[dict] = []
    decisions: list[dict] = []
    chosen_days: set[str] = set()
    blocked_days = 0
    recovered_days = 0
    for i, day in enumerate(dates):
        own30 = own_history[i]/own_history[i-30]-1 if i >= 30 else None
        own10 = own_history[i]/own_history[i-10]-1 if i >= 10 else None
        shadow30 = shadow_history[i]/shadow_history[i-30]-1 if i >= 30 else None
        shadow10 = shadow_history[i]/shadow_history[i-10]-1 if i >= 10 else None
        loss = {'own': own30, 'shadow': shadow30}.get(thirty_source)
        recovery = {'own': own10, 'shadow': shadow10}.get(ten_source)
        under_loss = loss is not None and loss <= -.10
        recovered = bool(under_loss and recovery is not None and recovery >= .01)
        blocked = bool(under_loss and (ten_source is None or not recovered))
        blocked_days += int(blocked)
        recovered_days += int(recovered)
        row = by_day.get(day)
        if row is not None:
            counterfactual = row['d2_close']/row['entry']*(1-FEE)/(1+FEE)-1
            decisions.append(dict(
                date=day, d0_date=row['d0_date'], symbol=row['symbol'],
                d0_rank=int(row['d0_rank']), score=float(row['score']),
                entry_time=str(row['entry_time']), buy_price=float(row['entry']),
                d2_close=float(row['d2_close']), d2_net_return=float(counterfactual),
                own30=own30, own10=own10, shadow30=shadow30, shadow10=shadow10,
                loss_trigger=int(under_loss), recovery_override=int(recovered),
                blocked=int(blocked), action='跳过' if blocked else '买入'))
            if not blocked:
                assert len(positions) <= 1
                budget = max(0., cash)*(1. if positions else .5)
                assert budget > 1e-8
                amount = budget/(1+FEE)
                positions.append(dict(start_i=date_index[day],
                                      shares=amount/row['entry'], row=row))
                cash -= budget
                chosen_days.add(day)
        keep = []
        market_value = 0.
        for lot in positions:
            k = date_index[day]-lot['start_i']+1
            assert k in (1, 2)
            value = lot['shares']*lot['row'][f'd{k}_close']
            if k == 2:
                cash += value*(1-FEE)
            else:
                keep.append(lot)
                market_value += value
        positions = keep
        nav = cash+market_value
        assert cash >= -1e-7 and nav > 0
        own_history.append(nav)
    chosen = signal[signal.d1_date.isin(chosen_days)]
    result, monthly, daily, ledger, missing = replay(
        chosen, calendar, start=start, end=END, initial=INITIAL, fee_rate=FEE)
    assert not missing
    np.testing.assert_allclose(daily.nav.to_numpy(), own_history[1:], rtol=0, atol=1e-7)
    assert result['entries']+sum(x['blocked'] for x in decisions) == len(signal)
    return result, monthly, daily, ledger, decisions, blocked_days, recovered_days


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    calendar = json.loads(CALENDAR.read_text())
    signals = pd.read_pickle(SOURCE/'signals.pkl')
    assert not signals.d1_date.duplicated().any()
    summary_rows, month_rows, signal_rows, daily_rows = [], [], [], []
    for scope, start in STARTS:
        for label, thirty_source, ten_source in CASES:
            result, months, daily, ledger, decisions, blocked_days, recovered_days = simulate(
                signals, calendar, start, thirty_source, ten_source)
            missed = [d['d2_net_return'] for d in decisions if d['blocked']]
            recovered = [d['d2_net_return'] for d in decisions if d['recovery_override']]
            summary_rows.append({
                '账户': scope, '方案': label, '起点': start,
                '30日来源': thirty_source, '10日来源': ten_source,
                '停手交易日': blocked_days, '恢复交易日': recovered_days,
                '跳过信号': len(missed), '恢复信号': len(recovered),
                '跳过D2笔均': float(np.mean(missed)) if missed else None,
                '恢复D2笔均': float(np.mean(recovered)) if recovered else None,
                **result, **metrics(ledger),
            })
            month_rows.extend({'账户': scope, '方案': label, **x} for x in months)
            signal_rows.extend({'账户': scope, '方案': label, **x} for x in decisions)
            daily_rows.extend({'账户': scope, '方案': label, **x} for x in daily.to_dict('records'))
    # Exact continuity with R38's already-audited base and pure 30-day gate.
    prior = pd.DataFrame(json.loads(Path(
        'docs/stock_automl_runs/20261019_b2_rolling20_gate_r38/summary.json').read_text()))
    for scope, pairs in ((s, [('B2原策略', 'B2原策略'),
                             ('30日单门槛', '30日≤−10%')]) for s, _ in STARTS):
        for new, old in pairs:
            a = next(x for x in summary_rows if x['账户'] == scope and x['方案'] == new)
            b = prior[(prior.账户 == scope) & (prior.方案 == old)].iloc[0]
            assert a['entries'] == b.entries
            np.testing.assert_allclose([a['final_nav'], a['max_drawdown']],
                                       [b.final_nav, b.max_drawdown], rtol=0, atol=1e-8)
    dump(OUT/'specification.json', {
        'name': 'R39_B2_30d_loss_10d_recovery',
        'strategy_registry': 'kingdomai.automl_strategy_definition / imac / B2 / v1.0',
        'strategy_definition_sha256': '157b9d77bb9951214b0978365cec407320b1d5663636b80fe60939ae94172c0c',
        'source_signals': str(SOURCE/'signals.pkl'),
        'source_sha256': sha(SOURCE/'signals.pkl'),
        'calendar_sha256': sha(CALENDAR), 'experiment_code_sha256': sha(Path(__file__)),
        'gate': 'At D1 before buy, 30 completed market-day return <=-10%; if a 10-day condition is present, pause only when 10 completed market-day return <+1%. Both use only previous close.',
        'shadow': 'Frozen B2 runs on paper every day, including paused days; all shadow returns read only prior market closes. Real cash is not changed by paper trades.',
        'start_and_end': [STARTS, END],
        'account': '1m initial; half available cash if flat, all remaining cash if carrying; D2 close sell; 0.1% each side',
        'scope': 'research on already seen historical data; B2 production unchanged',
        'db_status': 'research output not inserted into production registry',
        'cloud_status': 'local only; no independent cloud readback',
    })
    dump(OUT/'summary.json', summary_rows)
    dump(OUT/'monthly.json', month_rows)
    dump(OUT/'signals.json', signal_rows)
    dump(OUT/'daily.json', daily_rows)
    for r in summary_rows:
        print(r['账户'], r['方案'], r['entries'],
              f"{r['total_return']:+.2%}", f"DD {r['max_drawdown']:.2%}",
              f"Sharpe {r['sharpe']:.2f}", 'skip', r['跳过信号'],
              'recover', r['恢复信号'])


if __name__ == '__main__':
    main()
