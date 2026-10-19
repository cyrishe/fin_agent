"""Audit R39 timestamps, pause/recovery decisions and account arithmetic."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path('docs/stock_automl_runs/20261019_b2_recovery_gate_r39')
SOURCES = {
    'B2原策略': (None, None),
    '30日单门槛': ('own', None),
    '30日+真实10日': ('own', 'own'),
    '30日+影子10日': ('own', 'shadow'),
    '影子30日+影子10日': ('shadow', 'shadow'),
}


def close(actual, expected):
    if expected is None:
        assert pd.isna(actual)
    else:
        np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-12)


def main():
    summary = pd.DataFrame(json.loads((ROOT/'summary.json').read_text()))
    monthly = pd.DataFrame(json.loads((ROOT/'monthly.json').read_text()))
    daily = pd.DataFrame(json.loads((ROOT/'daily.json').read_text()))
    signals = pd.DataFrame(json.loads((ROOT/'signals.json').read_text()))
    checked = 0
    for scope, group in summary.groupby('账户'):
        baseline = group[group.方案 == 'B2原策略'].iloc[0]
        paper = daily[(daily.账户 == scope) & (daily.方案 == 'B2原策略')]
        paper = paper.sort_values('date').reset_index(drop=True)
        shadow = np.r_[1_000_000., paper.nav.to_numpy()]
        for r in group.to_dict('records'):
            scheme = r['方案']
            d = daily[(daily.账户 == scope) & (daily.方案 == scheme)]
            d = d.sort_values('date').reset_index(drop=True)
            s = signals[(signals.账户 == scope) & (signals.方案 == scheme)]
            m = monthly[(monthly.账户 == scope) & (monthly.方案 == scheme)]
            own = np.r_[1_000_000., d.nav.to_numpy()]
            idx = {date: i for i, date in enumerate(d.date)}
            assert len(s) == baseline.signals and len(d) == len(paper)
            assert r['entries']+r['跳过信号'] == baseline.entries
            np.testing.assert_allclose(own[-1], r['final_nav'], rtol=0, atol=1e-6)
            np.testing.assert_allclose(np.prod(1+m.return_.to_numpy())-1,
                                       r['total_return'], rtol=0, atol=1e-10)
            np.testing.assert_allclose(np.max(1-own/np.maximum.accumulate(own)),
                                       r['max_drawdown'], rtol=0, atol=1e-10)
            source30, source10 = SOURCES[scheme]
            for rec in s.to_dict('records'):
                i = idx[rec['date']]
                own30 = own[i]/own[i-30]-1 if i >= 30 else None
                own10 = own[i]/own[i-10]-1 if i >= 10 else None
                shadow30 = shadow[i]/shadow[i-30]-1 if i >= 30 else None
                shadow10 = shadow[i]/shadow[i-10]-1 if i >= 10 else None
                close(rec['own30'], own30)
                close(rec['own10'], own10)
                close(rec['shadow30'], shadow30)
                close(rec['shadow10'], shadow10)
                loss = {'own': own30, 'shadow': shadow30}.get(source30)
                recovery = {'own': own10, 'shadow': shadow10}.get(source10)
                under = loss is not None and loss <= -.10
                recovered = under and recovery is not None and recovery >= .01
                blocked = under and (source10 is None or not recovered)
                assert rec['loss_trigger'] == int(under)
                assert rec['recovery_override'] == int(recovered)
                assert rec['blocked'] == int(blocked)
                assert rec['action'] == ('跳过' if blocked else '买入')
                checked += 1
    print('R39 verified:', len(summary), 'account cases,', checked,
          'pre-decision signals, monthly compounding and daily NAV/DD')


if __name__ == '__main__':
    main()
