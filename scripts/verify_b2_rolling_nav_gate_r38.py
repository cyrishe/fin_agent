"""Audit the R38 gate timestamps, account paths, and report totals."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path('docs/stock_automl_runs/20261019_b2_rolling20_gate_r38')
INITIAL = 1_000_000.


def main():
    summary = pd.DataFrame(json.loads((ROOT/'summary.json').read_text()))
    monthly = pd.DataFrame(json.loads((ROOT/'monthly.json').read_text()))
    daily = pd.DataFrame(json.loads((ROOT/'daily.json').read_text()))
    signals = pd.DataFrame(json.loads((ROOT/'signals.json').read_text()))
    checked = 0
    for scope, group in summary.groupby('账户'):
        base = group[group.方案 == 'B2原策略'].iloc[0]
        paper = daily[(daily.账户 == scope) & (daily.方案 == 'B2原策略')]
        paper = paper.sort_values('date').reset_index(drop=True)
        shadow = np.r_[INITIAL, paper.nav.to_numpy()]
        for row in group.to_dict('records'):
            scheme = row['方案']
            d = daily[(daily.账户 == scope) & (daily.方案 == scheme)]
            d = d.sort_values('date').reset_index(drop=True)
            s = signals[(signals.账户 == scope) & (signals.方案 == scheme)]
            m = monthly[(monthly.账户 == scope) & (monthly.方案 == scheme)]
            nav = np.r_[INITIAL, d.nav.to_numpy()]
            assert len(d) == len(paper) and len(s) == base['signals']
            assert row['entries'] + row['跳过信号'] == base['entries']
            np.testing.assert_allclose(row['final_nav'], nav[-1], rtol=0, atol=1e-6)
            np.testing.assert_allclose(np.prod(1+m.return_.to_numpy())-1,
                                       row['total_return'], rtol=0, atol=1e-10)
            np.testing.assert_allclose(np.max(1-nav/np.maximum.accumulate(nav)),
                                       row['max_drawdown'], rtol=0, atol=1e-10)
            dates = d.date.to_list()
            idx = {x: i for i, x in enumerate(dates)}
            month_starts = {}
            for i, date in enumerate(dates):
                month_starts.setdefault(date[:7], i)
            months = list(month_starts)
            for rec in s.to_dict('records'):
                i = idx[rec['date']]
                obs = rec['rolling_return']
                if scheme == '20日≤−10%':
                    expected = nav[i]/nav[i-20]-1 if i >= 20 else None
                elif scheme == '20日影子≤−10%':
                    expected = shadow[i]/shadow[i-20]-1 if i >= 20 else None
                elif scheme == '上月≤−10%':
                    j = months.index(rec['date'][:7])
                    expected = (nav[month_starts[months[j]]]/
                                nav[month_starts[months[j-1]]]-1) if j else None
                else:
                    continue
                if expected is None:
                    assert pd.isna(obs) and rec['action'] == '买入'
                else:
                    np.testing.assert_allclose(obs, expected, rtol=0, atol=1e-12)
                    assert (rec['action'] == '跳过') == (expected <= -.10)
                checked += 1
    print('R38 verified:', len(summary), 'account cases,', checked,
          'pre-decision signal calculations, month compounding and daily NAV/DD')


if __name__ == '__main__':
    main()
