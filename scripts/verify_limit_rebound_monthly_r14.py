"""Reconcile saved monthly selections, account NAV and delivered detail rows."""
import json
import platform
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn

from scripts.experiment_limit_rebound_monthly_r14 import OUT, SOURCE, MONTHS, window, selection_key
from scripts.experiment_limit_rebound_short_mixed_r12 import sha, dump


def run():
    manifest=json.loads((OUT/'model_manifest.json').read_text())
    assert len(manifest['models'])==22*7*4
    for entry in manifest['models']:
        assert sha(Path(entry['path']))==entry['sha256']
    for month in MONTHS:
        frozen=json.loads((OUT/month/'selection_frozen.json').read_text())
        assert frozen['months']==window(month)
        assert frozen['candidate']==max(frozen['candidates'],key=selection_key)['candidate']
        assert frozen['spec_sha256']==sha(OUT/'specification.json')
        assert frozen['pool_sha256']==sha(OUT/'candidate_pool_protocol.json')
        for c in frozen['candidates']:
            assert c['model_sha256']==sha(OUT/month/(c['candidate']['id']+'.joblib'))
    keys=['范围','模型方案','交易规则','卖出']
    totals=pd.read_csv(OUT/'account_comparison.csv').set_index(keys)
    nav=pd.read_csv(OUT/'daily_nav.csv.gz')
    ledger=pd.read_csv(OUT/'cash_ledger.csv.gz')
    data=json.loads((OUT/'workbook_data.json').read_text())
    monthly=pd.DataFrame(data['逐月收益'])
    for key,g in nav.groupby(keys,sort=False):
        g=g.sort_values('date');r=totals.loc[key]
        v=np.r_[1.,g.nav.to_numpy()]
        assert np.isclose(v[-1]-1,r['账户总收益'])
        assert np.isclose((1-v/np.maximum.accumulate(v)).max(),r['账户最大回撤'])
        assert np.allclose(g.nav,g.cash+g.market_value)
        assert g.cash.min()>=-1e-12 and g.positions.max()<=4
        t=ledger[np.logical_and.reduce([ledger[c].eq(x) for c,x in zip(keys,key)])]
        assert len(t)==r['笔数'] and not t.entry_date.duplicated().any()
        assert np.isclose(1+(t.proceeds-t.cost).sum(),v[-1])
        m=monthly[np.logical_and.reduce([monthly[c].eq(x) for c,x in zip(keys,key)])]
        assert np.isclose((1+m['账户月收益']).prod(),v[-1])
        assert np.isclose(m['账户月收益'].mean(),r['账户月均收益'])
    detail=pd.DataFrame(data['月度前五'])
    top=pd.read_pickle(OUT/'enriched_predictions.pkl')
    top=top[top.model_policy.eq('滚动选优')]
    assert len(detail)==len(top) and top.groupby('d0_date').size().le(5).all()
    assert detail.D0日期.tolist()==top.d0_date.tolist()
    assert detail.D0名次.tolist()==top.d0_rank.tolist()
    for cn,col in [('D2收涨','d2_gross'),('D3收涨','d3_gross'),('D4收涨','d4_gross'),('策略退出涨','exit_gross')]:
        mask=detail[cn].notna().to_numpy()
        assert np.allclose(detail.loc[mask,cn],top.loc[mask,col])
        assert detail.loc[detail['观察完整'].eq(0),cn].isna().all()
    for k in (1,2,3,4):
        mask=top[f'd{k}_up'].notna().to_numpy()
        assert detail.loc[mask,f'D{k}封板'].tolist()==top.loc[mask,f'd{k}_up'].astype(int).tolist()
    git='/Users/imac/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/fallback/git'
    revision=subprocess.check_output([git,'rev-parse','HEAD'],text=True).strip()
    branch=subprocess.check_output([git,'branch','--show-current'],text=True).strip()
    assets=[Path(__file__),Path('scripts/experiment_limit_rebound_monthly_r14.py'),
            Path('scripts/report_limit_rebound_monthly_r14.py'),
            Path('scripts/audit_limit_rebound_account_drawdown_r13.py'),
            Path('scripts/experiment_limit_rebound_short_mixed_r12.py'),
            OUT/'build_workbook.mjs',SOURCE/'panel_replay_through_20261014.pkl',
            SOURCE/'calendar_through_20261014.json']
    result={'checks':'passed','models_hashed':len(manifest['models']),'monthly_selections':len(MONTHS),
            'account_scenarios':len(totals),'detail_rows':len(detail),
            'python':platform.python_version(),'sklearn':sklearn.__version__,
            'numpy':np.__version__,'pandas':pd.__version__,'git_commit':revision,'branch':branch,
            'working_tree':'contains uncommitted work; script hashes below identify this run',
            'assets':[{'path':str(p),'sha256':sha(p)} for p in assets]}
    dump(OUT/'verification.json',result)
    print(json.dumps({k:v for k,v in result.items() if k!='assets'},ensure_ascii=False))


if __name__=='__main__':run()
