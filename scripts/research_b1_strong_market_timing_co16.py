"""CO16: test strong-market timing on the registered frozen B1 trades."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.research_b1_frozen_rsi_timing_co15 import (
    EOD, EARLY, LATE, MARKET, REPLAY, ROOT, add_market, frozen_frame,
    metrics, replay, sha,
)
from scripts.research_b1_market_gate_co14 import buy_notional, sale_proceeds

REPORT = ROOT / 'docs/stock_automl_runs/20261017_b1_strong_market_timing_co16'
OUT = ROOT / 'outputs/stock_automl/b1_strong_market_timing_co16_20261017'
GATES = ('all', 'up10', 'up20', 'rsi_high', 'today_up', 'breadth_high',
         'up10_and_rsi_high', 'up10_weighted_50', 'rsi_high_weighted_50',
         'constant_75pct', 'down10', 'rsi_low')
PURE = ('up10', 'up20', 'rsi_high', 'today_up', 'breadth_high', 'up10_and_rsi_high')
PERIODS = {'2026_02_05': ('2026-02-01', '2026-05-31'),
           '2026_06_07': ('2026-06-01', '2026-07-31'),
           '2026_09': ('2026-09-01', '2026-09-30'),
           'all': ('2026-02-01', '2026-09-30')}


def build_frame() -> pd.DataFrame:
    f = add_market(frozen_frame())
    f['up10_and_rsi_high'] = f.up10 & f.rsi_high
    f['up10_weighted_50'] = np.where(f.up10, 1., .5)
    f['rsi_high_weighted_50'] = np.where(f.rsi_high, 1., .5)
    # CO15's replay handles this named constant in exactly the same way.
    assert f[['up10', 'up20', 'rsi_high', 'today_up', 'breadth_high']].notna().all().all()
    assert (f.up10 != f.down10).all() and (f.rsi_high != f.rsi_low).all()
    return f


def randomized_same_month(frame: pd.DataFrame, gate: str, reps=3000) -> dict:
    selected = frame[frame[gate]]
    counts = selected.groupby('month').size().to_dict()
    rng = np.random.default_rng(20261018 + PURE.index(gate))
    factors = np.array([float(sale_proceeds(float(buy_notional(1_000_000.)), r)) / 1_000_000.
                        for r in frame.gross_return])
    samples = np.empty(reps)
    for i in range(reps):
        mask = np.zeros(len(frame), dtype=bool)
        for month, group in frame.groupby('month'):
            k = counts.get(month, 0)
            if k:
                mask[rng.choice(group.index.to_numpy(), k, replace=False)] = True
        samples[i] = 100 * (np.prod(factors[mask]) - 1)
    actual = metrics(replay(frame, gate))['nav_return_pct']
    assert abs(actual - 100 * (np.prod(factors[selected.index]) - 1)) < 1e-8
    return {'gate': gate, 'actual_pct': actual, 'random_median_pct': float(np.median(samples)),
            'random_p05_pct': float(np.quantile(samples, .05)),
            'random_p95_pct': float(np.quantile(samples, .95)),
            'fraction_random_at_least_actual': float(np.mean(samples >= actual)),
            'reps': reps}


def risk_diagnostics(frame: pd.DataFrame, reps=3000) -> pd.DataFrame:
    """Post-result diagnostics; never use these draws to select a threshold."""
    rows = []
    rng = np.random.default_rng(20261018)
    factor = np.array([float(sale_proceeds(float(buy_notional(1_000_000.)), r)) / 1_000_000.
                       for r in frame.gross_return])
    for gate in ('up10', 'up20', 'today_up'):
        counts = frame.groupby('month')[gate].sum().astype(int).to_dict()
        samples = []
        for _ in range(reps):
            mask = np.zeros(len(frame), bool)
            for month, group in frame.groupby('month'):
                k = counts[month]
                if k:
                    mask[rng.choice(group.index.to_numpy(), k, replace=False)] = True
            nav = np.r_[1., np.cumprod(np.where(mask, factor, 1.))]
            samples.append((100 * (nav[-1] - 1),
                            100 * np.max(1 - nav / np.maximum.accumulate(nav))))
        arr = np.array(samples)
        actual = metrics(replay(frame, gate))
        rows.append({'gate': gate, 'matched_month_random_median_return_pct': float(np.median(arr[:, 0])),
                     'matched_month_random_median_drawdown_pct': float(np.median(arr[:, 1])),
                     'fraction_random_return_at_least_actual': float(np.mean(arr[:, 0] >= actual['nav_return_pct'])),
                     'fraction_random_drawdown_at_most_actual': float(np.mean(arr[:, 1] <= actual['settled_max_drawdown_pct'])),
                     'reps': reps})
    equal = frame.copy()
    equal['equal_exposure'] = 75 / 104
    rows.append({'gate': 'constant_equal_up10_weighted_exposure',
                 'nav_return_pct': metrics(replay(equal, 'equal_exposure'))['nav_return_pct'],
                 'settled_max_drawdown_pct': metrics(replay(equal, 'equal_exposure'))['settled_max_drawdown_pct']})
    counts = frame.groupby('month').up10.sum().astype(int).to_dict()
    weighted_samples = []
    for _ in range(reps):
        random_frame = frame.copy()
        random_frame['random_weighted'] = .5
        for month, group in frame.groupby('month'):
            k = counts[month]
            if k:
                random_frame.loc[rng.choice(group.index.to_numpy(), k, replace=False), 'random_weighted'] = 1.
        result = metrics(replay(random_frame, 'random_weighted'))
        weighted_samples.append((result['nav_return_pct'], result['settled_max_drawdown_pct']))
    arr = np.array(weighted_samples)
    actual = metrics(replay(frame, 'up10_weighted_50'))
    rows.append({'gate': 'up10_weighted_50',
                 'matched_month_random_median_return_pct': float(np.median(arr[:, 0])),
                 'matched_month_random_median_drawdown_pct': float(np.median(arr[:, 1])),
                 'fraction_random_return_at_least_actual': float(np.mean(arr[:, 0] >= actual['nav_return_pct'])),
                 'fraction_random_drawdown_at_most_actual': float(np.mean(arr[:, 1] <= actual['settled_max_drawdown_pct'])),
                 'reps': reps})
    return pd.DataFrame(rows)


def main() -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    frame = build_frame()
    summary, monthly, ledgers, stress = [], [], [], []
    for gate in GATES:
        full = replay(frame, gate)
        ledgers.append(full)
        for period, (start, end) in PERIODS.items():
            part = frame[frame.signal_date.between(start, end)]
            summary.append({'gate': gate, 'period': period, **metrics(replay(part, gate))})
        for month, part in frame.groupby('month'):
            monthly.append({'gate': gate, 'month': month, **metrics(replay(part, gate))})
        if gate in ('all', *PURE, 'down10', 'rsi_low'):
            month_rows = [r for r in monthly if r['gate'] == gate]
            best_month = max(month_rows, key=lambda r: r['nav_return_pct'])['month']
            for name, blocked in (
                ('remove_best_month', frame.month.eq(best_month)),
                ('remove_monthly_best_trade', frame.index.isin(frame.groupby('month').gross_return.idxmax())),
            ):
                test = frame.copy()
                test[gate] = full.exposure.to_numpy().astype(bool) & ~np.asarray(blocked)
                stress.append({'gate': gate, 'stress': name,
                               'return_pct': metrics(replay(test, gate))['nav_return_pct']})
    frame[['signal_date', 'exit_date', 'symbol6', 'month', 'gross_return', 'second_high_return',
           'strong', 'market_return_now', 'market_up_fraction_now', 'trend10_1440',
           'trend20_1440', 'rsi14_1440', 'up10', 'up20', 'rsi_high', 'today_up',
           'breadth_high', 'up10_and_rsi_high']].to_csv(OUT/'frozen_decisions.csv', index=False,
                                                       float_format='%.17g')
    pd.concat(ledgers, ignore_index=True).to_csv(OUT/'daily_replays.csv.gz', index=False,
                                                  compression='gzip', float_format='%.17g')
    pd.DataFrame(summary).to_csv(REPORT/'summary.csv', index=False, float_format='%.12g')
    pd.DataFrame(monthly).to_csv(REPORT/'monthly.csv', index=False, float_format='%.12g')
    pd.DataFrame(randomized_same_month(frame, gate) for gate in PURE).to_csv(
        REPORT/'matched_random.csv', index=False, float_format='%.12g')
    pd.DataFrame(stress).to_csv(REPORT/'stress.csv', index=False, float_format='%.12g')
    risk_diagnostics(frame).to_csv(REPORT/'risk_diagnostics.csv', index=False, float_format='%.12g')
    manifest = {'experiment': 'CO16_20261017_B1_strong_market_timing',
                'frozen_trades': len(frame), 'gates': list(GATES), 'periods': PERIODS,
                'input_sha256': {str(p.relative_to(ROOT)): sha(p) for p in
                                 (REPLAY, EARLY, LATE, MARKET, EOD)},
                'code_sha256': sha(Path(__file__)), 'fee_rule': 'same as CO15',
                'market_scope': 'asof D0 14:40', 'random_seed': 20261018}
    (REPORT/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    print(pd.DataFrame(summary).query("period == 'all'").to_string(index=False))


if __name__ == '__main__':
    main()
