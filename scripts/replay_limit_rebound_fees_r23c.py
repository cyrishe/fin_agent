"""R23C: same cash rotation, 0.1% on each buy and sell transaction."""
import json
import shutil
from pathlib import Path
import numpy as np
import pandas as pd
from scripts.replay_limit_rebound_cash_r23 import OUT as GROSS,BASE,replay,dump,sha

OUT=Path('outputs/stock_automl/limit_rebound_fee001_r23c_20261015')
RATE=.001


def main():
    OUT.mkdir(exist_ok=True)
    calendar=json.loads((BASE/'calendar.json').read_text())
    f=pd.read_pickle(GROSS/'signals.pkl')
    spec=json.loads((GROSS/'specification.json').read_text())
    spec.update(name='R23C_CNY1m_cash_rotation_0.1pct_each_side',fee_rate=RATE,
        budget='Cash budget is half available cash when flat, all available cash when carrying yesterday lot. Buy notional=budget/(1+0.001); buy fee=notional*0.001. Budget includes fee, no overdraft.',
        fees='Each buy and each sell charged 0.1% of executed notional; sell cash proceeds=notional*(1-0.001). These are the requested aggregate modeled fees; no extra tax/minimum charge/slippage modeled.',
        endpoint='Last open lot charged buy fee only; marked at last close, no unexecuted future sale fee deducted. Open-lot PnL includes already-paid buy fee.',
        source_signals_sha256=sha(GROSS/'signals.pkl'),gross_spec_sha256=sha(GROSS/'specification.json'))
    p=OUT/'specification.json'
    if p.exists():assert json.loads(p.read_text())==spec
    else:dump(p,spec)
    # The fee=0 path must preserve every original account day before introducing fees.
    z,_,zn,zl,_=replay(f,calendar,fee_rate=0.)
    old=pd.read_pickle(GROSS/'daily_nav.pkl')
    for c in ['cash','market_value','nav','return_','drawdown']:
        np.testing.assert_allclose(zn[c],old[c],rtol=0,atol=1e-9)
    s,m,n,l,e=replay(f,calendar,fee_rate=RATE)
    for name in ['signals.pkl','inherited_models.json','inherited_rules.json']:
        shutil.copy2(GROSS/name,OUT/name)
    dump(OUT/'summary.json',s);dump(OUT/'monthly.json',m);dump(OUT/'excluded.json',e)
    n.to_pickle(OUT/'daily_nav.pkl');l.to_pickle(OUT/'ledger.pkl')
    dump(OUT/'gross_reproduction.json',dict(days=len(old),status='PASS',max_nav_error=float(np.max(np.abs(zn.nav-old.nav)))))
    dump(OUT/'output_config.json',dict(tag='R23C',filename='百万资金轮换_单边千一费后_R23C_20261015.xlsx'))
    print(json.dumps(s,ensure_ascii=False,indent=2))
    print(pd.DataFrame(m)[['month','profit','return_','drawdown','sharpe','fees']].to_string(index=False))


if __name__=='__main__':main()
