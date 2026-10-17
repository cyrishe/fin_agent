"""CO15: apply predeclared, causal market timing to the registered B1 replay."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.research_b1_market_gate_co14 import buy_notional, market_features, sale_proceeds

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/stock_automl/b1_frozen_rsi_timing_co15_20261017'
REPORT = ROOT / 'docs/stock_automl_runs/20261017_b1_frozen_rsi_timing_co15'
REPLAY = ROOT / 'outputs/stock_automl/capital_replay_20261012/replay.json'
EARLY = ROOT / 'docs/stock_automl_runs/20261011_mk_1m_tradable_st_rolling20/top1_verified.csv'
LATE = ROOT / 'docs/stock_automl_runs/20261011_aug_oct_independent20/top2_verified.csv'
MARKET = ROOT / 'outputs/stock_automl/b1_market_gate_co14_20261017/market_input.csv'
EOD = ROOT / 'outputs/stock_automl/b1_market_gate_co14_20261017/market_eod.csv'
GATES = ('all', 'down10', 'rsi_low', 'down10_and_rsi_low', 'mild_down10',
         'rsi_low_turnup5', 'down10_turnup5', 'down10_weighted', 'rsi_low_weighted',
         'constant_75pct')
PERIODS = {'2026_02_05': ('2026-02-01', '2026-05-31'),
           '2026_06_07': ('2026-06-01', '2026-07-31'),
           '2026_09': ('2026-09-01', '2026-09-30'),
           'all': ('2026-02-01', '2026-09-30')}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def frozen_frame() -> pd.DataFrame:
    raw = json.loads(REPLAY.read_text())
    records = [r for r in raw['trades'] if '2026-02-01' <= r['signal_date'] <= '2026-09-30']
    assert len(records) == 104 and len({r['signal_date'] for r in records}) == 104
    picks = pd.concat([pd.read_csv(EARLY, dtype={'symbol6': str}),
                       pd.read_csv(LATE, dtype={'symbol6': str})], ignore_index=True)
    picks = picks[picks.selection_rank.eq(1)].copy()
    picks['symbol6'] = picks.symbol6.str.zfill(6)
    assert not picks.signal_date.duplicated().any()
    picked = picks.set_index('signal_date')
    frame = pd.DataFrame(records).sort_values('signal_date').reset_index(drop=True)
    frame = frame.rename(columns={'return': 'gross_return'})
    for row in frame.itertuples():
        p = picked.loc[row.signal_date]
        assert p.symbol6 == row.symbol6
        assert abs(float(p.signal_price) - row.buy_price) < 1e-8
        assert abs(float(p.close_40) - row.sell_price) < 1e-8
        assert abs(row.sell_price / row.buy_price - 1 - row.gross_return) < 1e-10
    frame['second_high_return'] = [float(picked.loc[d].actual_second_high_return)
                                   for d in frame.signal_date]
    frame['strong'] = frame.second_high_return.ge(.03)
    frame['month'] = frame.signal_date.str[:7]
    return frame


def add_market(frame: pd.DataFrame) -> pd.DataFrame:
    market = pd.read_csv(MARKET).set_index('date')
    eod = pd.read_csv(EOD).set_index('date').eod_return
    features = market_features(market, eod)
    # Historical RSI five trading days ago was known before today's 14:40 signal.
    features['rsi_prev5'] = features.rsi14_1440.shift(5)
    assert set(frame.signal_date).issubset(features.index)
    f = frame.join(features, on='signal_date', validate='one_to_one')
    assert f[['trend10_1440', 'rsi14_1440', 'rsi_prev5']].notna().all().all()
    f['down10_and_rsi_low'] = f.down10 & f.rsi_low
    f['mild_down10'] = f.trend10_1440.gt(-.02) & f.trend10_1440.lt(0)
    f['rsi_low_turnup5'] = f.rsi_low & f.rsi14_1440.gt(f.rsi_prev5)
    f['down10_turnup5'] = f.down10 & f.rsi14_1440.gt(f.rsi_prev5)
    return f


def exposure(frame: pd.DataFrame, gate: str) -> np.ndarray:
    if gate == 'all':
        return frame['all'].astype(float).to_numpy()
    if gate == 'down10_weighted':
        return np.where(frame.down10, 1., .5)
    if gate == 'rsi_low_weighted':
        return np.where(frame.rsi_low, 1., .5)
    if gate == 'constant_75pct':
        return np.full(len(frame), .75)
    return frame[gate].astype(float).to_numpy()


def replay(frame: pd.DataFrame, gate: str, initial=1_000_000.) -> pd.DataFrame:
    x = frame.copy()
    x['gate'] = gate
    x['exposure'] = exposure(frame, gate)
    x['trade'] = x.exposure.gt(0)
    before, after = [], []
    cap = float(initial)
    for r in x.itertuples():
        before.append(cap)
        if r.exposure:
            budget = cap * r.exposure
            buy = float(buy_notional(budget))
            cap = cap - budget + float(sale_proceeds(buy, r.gross_return))
        after.append(cap)
    x['nav_before'], x['nav_after'] = before, after
    x['account_return'] = x.nav_after / x.nav_before - 1
    return x


def metrics(x: pd.DataFrame) -> dict:
    baseline = x.copy()
    baseline['fixed_net_return'] = [float(sale_proceeds(float(buy_notional(1_000_000.)), r)) / 1_000_000. - 1
                                    for r in baseline.gross_return]
    trades = baseline[baseline.trade]
    blocked = baseline[~baseline.trade & baseline.exposure.eq(0)]
    ledger = np.r_[x.nav_before.iloc[0], x.nav_after.to_numpy()]
    return {'trades': int(x.trade.sum()), 'full_equivalent_exposure': float(x.exposure.sum()),
            'nav_return_pct': float(100 * (ledger[-1] / ledger[0] - 1)),
            'settled_max_drawdown_pct': float(100 * np.max(1 - ledger / np.maximum.accumulate(ledger))),
            'strong_hit_pct': float(100 * trades.strong.mean()) if len(trades) else None,
            'critical_net_loss_count': int((trades.fixed_net_return < -.02).sum()),
            'critical_net_loss_rate_pct': float(100 * (trades.fixed_net_return < -.02).mean()) if len(trades) else None,
            'blocked_critical': int((blocked.fixed_net_return < -.02).sum()),
            'blocked_profitable': int((blocked.fixed_net_return > 0).sum()),
            'net_mean_pct': float(100 * trades.fixed_net_return.mean()) if len(trades) else None}


def matched_random(frame: pd.DataFrame, gate: str, reps=3000) -> dict:
    """Same number of trades per signal month; no future returns enter gate decisions."""
    chosen = frame[frame[gate]].copy()
    k = chosen.groupby('month').size().to_dict()
    rng = np.random.default_rng(20261017 + GATES.index(gate))
    # Capital is far above the 20,000-yuan threshold where minimum commission
    # binds, so each full-investment trade has a capital-independent net factor.
    factors = np.asarray([float(sale_proceeds(float(buy_notional(1_000_000.)), r)) / 1_000_000.
                          for r in frame.gross_return])
    log_factors = np.log(factors)
    rets = []
    for _ in range(reps):
        selected = set()
        for month, group in frame.groupby('month'):
            count = k.get(month, 0)
            if count:
                selected.update(rng.choice(group.index.to_numpy(), count, replace=False).tolist())
        rets.append(float(100 * np.expm1(log_factors[list(selected)].sum())))
    actual = metrics(replay(frame, gate))['nav_return_pct']
    assert abs(actual - 100 * np.expm1(log_factors[chosen.index].sum())) < 1e-8
    return {'gate': gate, 'actual_pct': actual, 'random_median_pct': float(np.median(rets)),
            'random_p05_pct': float(np.quantile(rets, .05)), 'random_p95_pct': float(np.quantile(rets, .95)),
            'fraction_random_at_least_actual': float(np.mean(np.asarray(rets) >= actual)),
            'reps': reps}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    REPORT.mkdir(parents=True, exist_ok=True)
    frame = add_market(frozen_frame())
    raw = json.loads(REPLAY.read_text())
    gross = np.prod(1 + frame.gross_return.to_numpy()) - 1
    assert abs(gross * 100 - 97.98174059753015) < 1e-8
    assert abs(raw['monthly'][7]['ending_equity'] - 2_001_595.3974410289) < 1e-6
    assert not frame.signal_date.str.startswith('2026-08').any()
    summary, monthly, daily = [], [], []
    for gate in GATES:
        x = replay(frame, gate)
        daily.append(x)
        for name, (lo, hi) in PERIODS.items():
            sub = x[x.signal_date.between(lo, hi)]
            if sub.empty:
                continue
            base = replay(frame[frame.signal_date.between(lo, hi)], gate)
            summary.append({'gate': gate, 'period': name, **metrics(base)})
        for month, group in x.groupby('month'):
            m = metrics(replay(frame[frame.month.eq(month)], gate))
            monthly.append({'gate': gate, 'month': month, **m})
        assert abs(np.prod(1 + x.account_return.to_numpy()) - x.nav_after.iloc[-1] / 1_000_000.) < 1e-10
    days = pd.concat(daily, ignore_index=True)
    overview = pd.DataFrame(summary)
    months = pd.DataFrame(monthly)
    control = pd.DataFrame([matched_random(frame, g) for g in
                            ('down10', 'rsi_low', 'down10_and_rsi_low', 'mild_down10',
                             'rsi_low_turnup5', 'down10_turnup5')])
    # Conservative diagnostics only: these deletions use future outcomes.
    stress = []
    for gate in GATES:
        if 'weighted' in gate or gate == 'constant_75pct':
            continue
        x = days[days.gate.eq(gate)].copy()
        best_month = months[months.gate.eq(gate)].sort_values('nav_return_pct').iloc[-1]['month']
        for label, ix in [('remove_best_month', x.month.eq(best_month)),
                          ('remove_monthly_best_trade', x.index.isin(x[x.trade].groupby('month').gross_return.idxmax()))]:
            y = frame.copy()
            y[gate] = x.exposure.to_numpy().astype(bool) & ~np.asarray(ix)
            stress.append({'gate': gate, 'stress': label, 'return_pct': metrics(replay(y, gate))['nav_return_pct']})
    decisions = frame[['signal_date', 'exit_date', 'symbol6', 'buy_price', 'sell_price',
                       'gross_return', 'second_high_return', 'strong', 'month', 'market_return_now',
                       'market_up_fraction_now', 'trend10_1440', 'trend20_1440',
                       'rsi14_1440', 'rsi_prev5', 'down10', 'rsi_low',
                       'down10_and_rsi_low', 'mild_down10', 'rsi_low_turnup5', 'down10_turnup5']]
    decisions.to_csv(OUT / 'frozen_decisions.csv', index=False, float_format='%.17g')
    days.to_csv(OUT / 'daily_replays.csv.gz', index=False, compression='gzip', float_format='%.17g')
    overview.to_csv(REPORT / 'summary.csv', index=False, float_format='%.12g')
    months.to_csv(REPORT / 'monthly.csv', index=False, float_format='%.12g')
    control.to_csv(REPORT / 'matched_random.csv', index=False, float_format='%.12g')
    pd.DataFrame(stress).to_csv(REPORT / 'stress.csv', index=False, float_format='%.12g')
    manifest = {'experiment': 'CO15_20261017_B1_frozen_RSI_timing',
                'input_sha256': {str(p.relative_to(ROOT)): sha(p) for p in
                                 (REPLAY, EARLY, LATE, MARKET, EOD)},
                'frozen_trades': len(frame), 'gross_replay_pct': 100 * gross,
                'source_snapshot_scope': '2026-02-02..2026-09-30 signal; last exit 2026-10-08',
                'market_scope': 'asof D0 14:40', 'fees': 'CO14: buy/sell 2.5bp commission min 5; transfer 0.1bp each; sell stamp 5bp',
                'gates': list(GATES), 'periods': PERIODS, 'random_seed': 20261017,
                'code_sha256': sha(Path(__file__))}
    (REPORT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(overview.to_string(index=False))
    print('random\n', control.to_string(index=False))


if __name__ == '__main__':
    main()
