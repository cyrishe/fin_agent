"""Independently reconcile price attribution, minute exits and cash replay."""
import json
import numpy as np,pandas as pd
from scripts import research_b2_floor_risk_20261021 as old
from scripts.analyze_b2_risk_actions_20261022 import OUT,ROOT,FEE,MODELS,write

def main():
    r=json.loads((OUT/'results.json').read_text());f=old.load();te=f[f.split=='test']
    assert r['code_sha256']==old.sha(ROOT/'scripts/analyze_b2_risk_actions_20261022.py')
    for path,digest in r['sources'].items():assert old.sha(path)==digest
    m=pd.read_csv(OUT/'D2首分钟与离板明细.csv',dtype={'symbol':str})
    assert len(m)==125 and m.sequence.is_unique and m.open_floor.sum()==34
    for row in m.to_dict('records'):
        b=pd.read_csv(OUT/'minutes'/f"{row['D2']}_{row['symbol']}.csv.gz").sort_values('time').reset_index(drop=True)
        assert bool(b.low.iloc[0]<=row['floor']+.001)==row['first_touch']
        elig=np.flatnonzero((b.open.to_numpy()>row['floor']+.001)&b.volume.gt(0).to_numpy());elig=elig[elig>=1]
        if len(elig):
            ix=int(elig[0]);assert b.time.iloc[ix][11:16]==row['proxy_time'];assert b.open.iloc[ix]==row['proxy_price']
            expected=(b.open.iloc[ix]/row['entry'])*(1-FEE)/(1+FEE)-1
            assert np.isclose(expected,row['proxy_net'],atol=1e-12)
        else:assert pd.isna(row['proxy_net'])
        assert row['floor_hyp_net']<=row['original_net']+1e-12
    counts=[x['n'] for x in r['distribution']];assert counts==[19,2,5,5,2,1]
    flags={}
    for model,label,path,col,name in MODELS:
        p=pd.read_csv(path,dtype={'股票':str,'symbol':str});p=p[(p[col]==name)&p.flagged.eq(1)]
        if 'sequence' in p:ids=set(p.sequence)
        else:
            keys=set(zip(p.D0,p['股票']));ids={x.sequence for x in te.itertuples() if (x.d0_date,x.symbol) in keys}
        flags[model]=ids;g=te[te.sequence.isin(ids)];v=g.original_net;entry=next(x for x in r['cancel'] if x['model']==model)
        assert len(g)==entry['flagged']
        assert np.isclose(entry['cancel_net_delta_per_10000'],-10000*v.sum())
        assert np.isclose(entry['avoided_loss_per_10000']-entry['forfeited_profit_per_10000'],entry['cancel_net_delta_per_10000'])
    # Cash reconstruction uses dated source rows, not the replay function or its ledger.
    summaries={'baseline':r['baseline_account'],**{x['model']:x['result'] for x in r['cancel_accounts']}}
    for model,summary in summaries.items():
        source=te if model=='baseline' else te[~te.sequence.isin(flags[model])]
        byday={x['d1_date']:x for x in source.to_dict('records')}
        daily=pd.read_csv(OUT/('baseline_daily.csv' if model=='baseline' else model+'_cancel_daily.csv'))
        cash=1000000.;held=[];pnl=0.;peak=cash;dd=0.
        for day in daily.to_dict('records'):
            date=day['date']
            if date in byday:
                x=byday[date];budget=cash if held else cash/2;shares=budget/(1+FEE)/x['entry']
                held.append((x,shares,budget));cash-=budget
            rest=[];mark=0.
            for x,shares,cost in held:
                if date==x['卖出日']:
                    proceeds=shares*x['d2_close']*(1-FEE);cash+=proceeds;pnl+=proceeds-cost
                else:
                    assert date==x['d1_date'];mark+=shares*x['d1_close'];rest.append((x,shares,cost))
            held=rest;nav=cash+mark;peak=max(peak,nav);dd=max(dd,1-nav/peak)
            assert np.isclose(nav,day['nav'],rtol=0,atol=1e-6)
            assert np.isclose(cash,day['cash'],rtol=0,atol=1e-6)
        assert not held and np.isclose(cash,1000000+pnl,atol=1e-6)
        assert np.isclose(cash,summary['final_nav']) and np.isclose(dd,summary['max_drawdown'])
    write('verification.json',dict(source_hashes=True,opening_distribution=counts,minute_records=125,
      original_returns_reproduced=True,first_post_trigger_open_above_floor_reproduced=True,
      unavailable_minutes_never_counted_as_fills=True,cancellation_gains_and_losses_reconcile=True,
      independent_cash_replay_accounts=4,all_daily_cash_nav_and_drawdown_match=True,
      code_sha256=old.sha(__file__),production_modified=False))
    print('verified: 125 minute paths; three fixed warning sets; four independent daily cash replays')

if __name__=='__main__':main()
