"""Independent readback of CO15 inputs, trade identities, fees, and ledgers."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from scripts.research_b1_frozen_rsi_timing_co15 import (
    EOD, GATES, MARKET, OUT, REPLAY, REPORT, add_market, frozen_frame, sha,
)
from scripts.research_b1_market_gate_co14 import market_features


def main() -> None:
    manifest = json.loads((REPORT / 'manifest.json').read_text())
    from scripts.research_b1_frozen_rsi_timing_co15 import ROOT
    for name, expected in manifest['input_sha256'].items():
        assert sha(ROOT / name) == expected, name
    frozen = add_market(frozen_frame())
    assert len(frozen) == 104 and not frozen.signal_date.duplicated().any()
    original = json.loads(REPLAY.read_text())
    assert abs(np.prod(1 + frozen.gross_return.to_numpy()) - 1 - .9798174059753015) < 1e-10
    assert abs(original['monthly'][7]['ending_equity'] / 1_000_000 - 1 - 1.001595397441029) < 1e-10
    saved = pd.read_csv(OUT / 'frozen_decisions.csv', dtype={'symbol6': str})
    assert saved.signal_date.tolist() == frozen.signal_date.tolist()
    assert saved.symbol6.tolist() == frozen.symbol6.tolist()
    assert np.allclose(saved.gross_return, frozen.gross_return, atol=1e-14, rtol=0)
    market = pd.read_csv(MARKET).set_index('date')
    eod = pd.read_csv(EOD).set_index('date').eod_return
    features = market_features(market, eod)
    for r in saved.itertuples():
        i = features.index.get_loc(r.signal_date)
        assert i >= 5
        assert abs(r.rsi_prev5 - features.iloc[i - 5].rsi14_1440) < 1e-12
        assert bool(r.down10) == (r.trend10_1440 < 0)
        assert bool(r.rsi_low) == (r.rsi14_1440 < 50)
    # Latest/next-day market data cannot change any earlier signal's features.
    altered_eod = eod.copy()
    altered_market = market.copy()
    cutoff = '2026-06-02'
    altered_eod.loc[altered_eod.index >= cutoff] = .5
    altered_market.loc[altered_market.index >= cutoff, 'market_return_now'] = .5
    previous = market_features(altered_market, altered_eod)
    for key in ('trend10_1440', 'trend20_1440', 'rsi14_1440'):
        assert np.allclose(features.loc[features.index < cutoff, key],
                           previous.loc[previous.index < cutoff, key], equal_nan=True)
    daily = pd.read_csv(OUT / 'daily_replays.csv.gz', dtype={'symbol6': str})
    summary = pd.read_csv(REPORT / 'summary.csv')
    monthly = pd.read_csv(REPORT / 'monthly.csv')
    checks = 0
    for gate in GATES:
        d = daily[daily.gate.eq(gate)].sort_values('signal_date')
        assert len(d) == 104 and d.signal_date.is_unique
        assert d.symbol6.tolist() == saved.symbol6.tolist()
        assert d.exposure.between(0, 1).all()
        cap = 1_000_000.
        for r in d.itertuples():
            assert abs(r.nav_before - cap) < 1e-6
            if r.exposure:
                budget = cap * r.exposure
                buy = budget / 1.00026
                assert buy * .00025 >= 5
                sale = buy * (1 + r.gross_return)
                cap = cap - budget + sale * (1 - .00076)
            assert abs(r.nav_after - cap) < 1e-6
            checks += 1
        actual = summary[(summary.gate == gate) & (summary.period == 'all')].iloc[0]
        assert abs(actual.nav_return_pct - 100 * (cap / 1_000_000 - 1)) < 1e-8
        monthprod = np.prod(1 + monthly[monthly.gate == gate].nav_return_pct.to_numpy() / 100)
        assert abs(monthprod - cap / 1_000_000) < 1e-10
    result = {'passed': True, 'frozen_trade_identity_price_and_gross': 104,
              'causal_market_feature_checks': len(saved),
              'independent_ledger_rows': checks, 'gates': len(GATES),
              'source_hashes': len(manifest['input_sha256']),
              'baseline_gross_pct': 97.98174059753015,
              'baseline_net_pct': float(summary[(summary.gate == 'all') & (summary.period == 'all')].iloc[0].nav_return_pct)}
    (REPORT / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
