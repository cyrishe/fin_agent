"""Independent identity, as-of feature, and cash-ledger checks for CO16."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from scripts.research_b1_strong_market_timing_co16 import GATES, OUT, REPORT, build_frame
from scripts.research_b1_frozen_rsi_timing_co15 import ROOT, sha


def main() -> None:
    manifest = json.loads((REPORT/'manifest.json').read_text())
    for name, expected in manifest['input_sha256'].items():
        assert sha(ROOT/name) == expected, name
    frame = build_frame()
    saved = pd.read_csv(OUT/'frozen_decisions.csv', dtype={'symbol6': str})
    assert len(saved) == 104 and saved.signal_date.tolist() == frame.signal_date.tolist()
    assert saved.symbol6.tolist() == frame.symbol6.tolist()
    assert np.allclose(saved.gross_return, frame.gross_return, atol=1e-14, rtol=0)
    for row in saved.itertuples():
        assert bool(row.up10) == (row.trend10_1440 >= 0)
        assert bool(row.up20) == (row.trend20_1440 >= 0)
        assert bool(row.rsi_high) == (row.rsi14_1440 >= 50)
        assert bool(row.today_up) == (row.market_return_now >= 0)
        assert bool(row.breadth_high) == (row.market_up_fraction_now >= .5)
        assert bool(row.up10_and_rsi_high) == (bool(row.up10) and bool(row.rsi_high))
    daily = pd.read_csv(OUT/'daily_replays.csv.gz', dtype={'symbol6': str})
    summary = pd.read_csv(REPORT/'summary.csv')
    monthly = pd.read_csv(REPORT/'monthly.csv')
    checks = 0
    for gate in GATES:
        d = daily[daily.gate.eq(gate)].sort_values('signal_date')
        assert len(d) == 104 and d.signal_date.is_unique
        assert d.symbol6.tolist() == saved.symbol6.tolist()
        assert d.exposure.between(0, 1).all()
        cap = 1_000_000.
        for row in d.itertuples():
            assert abs(row.nav_before - cap) < 1e-6
            if row.exposure:
                budget = cap * row.exposure
                buy = budget/1.00026
                assert buy*.00025 >= 5
                sale = buy*(1+row.gross_return)
                cap = cap-budget+sale*(1-.00076)
            assert abs(row.nav_after-cap) < 1e-6
            checks += 1
        result = summary[(summary.gate == gate) & (summary.period == 'all')].iloc[0]
        assert abs(result.nav_return_pct - 100*(cap/1_000_000.-1)) < 1e-8
        assert abs(np.prod(1+monthly[monthly.gate == gate].nav_return_pct.to_numpy()/100)
                   -cap/1_000_000.) < 1e-10
    baseline = summary[(summary.gate == 'all') & (summary.period == 'all')].iloc[0]
    assert abs(baseline.nav_return_pct-78.0506875731) < 1e-8
    assert abs(summary[(summary.gate == 'down10') & (summary.period == 'all')].iloc[0].nav_return_pct
               -28.9885283849) < 1e-8
    result = {'passed': True, 'frozen_identity_and_prices': 104,
              'asof_gate_checks': 104*6, 'independent_ledger_rows': checks,
              'gates': len(GATES), 'source_hashes': len(manifest['input_sha256']),
              'baseline_net_pct': float(baseline.nav_return_pct)}
    (REPORT/'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
