"""Compare released V2 monthly gate with R90's cash-dividend-aware shadow.

Read-only: loads a specific released source file; does not connect to DB.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd

ROOT = Path('/data/cyris/work/b2_2016_2020_r90_20261023')
module = Path('/data/cyris/fin_agent_deploy/b2-v2-20261021/scripts/b2_monthly_pause.py')
spec = importlib.util.spec_from_file_location('released_b2_monthly_pause', module)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

cal = json.loads((ROOT/'calendar.json').read_text())
sig = pd.read_pickle(ROOT/'signals_exit.pkl')
trades = [dict(d0=r.d0_date, symbol=r.symbol, buy_day=r.d1_date,
               buy_price=float(r.entry), exit_day=r.d2_date,
               sell_price=float(r.d2_close_original)) for r in sig.itertuples()]
closes = {}
for r in sig.itertuples():
    closes[(r.symbol,r.d1_date)] = float(r.d1_close)
    closes[(r.symbol,r.d2_date)] = float(r.d2_close_original)
coverage = {d:{'audited':True} for d in cal if '2017-05-01' <= d <= '2021-12-31'}
original = json.loads((ROOT/'overall.json').read_text())['gate']
rows = []
for month in pd.period_range('2018-01','2021-12',freq='M'):
    name = str(month)
    prev = str(month-1)
    count = sum(t['d0'].startswith(prev) for t in trades)
    actual = gate.calculate_gate(name,cal,trades,closes,coverage,count,
                                 str(month.start_time),{'audit':'research_shadow'})
    expected = original[name]
    rows.append(dict(month=name, candidate_count=count,
                     released_r5=actual['r5'], research_r5=expected['previous_r5'],
                     released_r10=actual['r10'], research_r10=expected['previous_r10'],
                     released_paused=actual['paused'], research_paused=expected['paused'],
                     diff_r5=actual['r5']-expected['previous_r5'],
                     diff_r10=actual['r10']-expected['previous_r10']))
out = pd.DataFrame(rows)
out.to_csv(ROOT/'audit_r91/live_gate_compare.csv', index=False)
print('gate flip',int(out.released_paused.ne(out.research_paused).sum()))
print(out[(out.diff_r5.abs()>1e-10)|(out.diff_r10.abs()>1e-10)].to_string(index=False))
