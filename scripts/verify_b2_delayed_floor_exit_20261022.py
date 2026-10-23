"""Independent accounting and causal timing audit for the fixed 34-trade study."""
import json
import numpy as np
import pandas as pd
from scripts import analyze_b2_delayed_floor_exit_20261022 as a


def run():
    r=json.loads((a.OUT/'results.json').read_text())
    assert r['code_sha256']==a.old.sha(a.__file__)
    for path,sha in r['sources'].items():assert a.old.sha(path)==sha
    f=a.cohort().set_index('sequence')
    d=pd.read_csv(a.OUT/'逐笔退出与未卖出.csv',dtype={'symbol':str})
    ev=pd.read_csv(a.OUT/'D3量价及封板.csv',dtype={'symbol':str}).set_index('sequence')
    daily=pd.read_csv(a.OUT/'d3_daily.csv.gz').set_index(['stk_code','trade_date'])
    actions=pd.read_csv(a.OUT/'corporate_action.csv')
    assert len(d)==204 and not d.duplicated(['policy','sequence']).any()
    assert len(ev)==34 and ev.d2_available.sum()==15
    sources={x['sequence']:x for x in json.loads((a.OUT/'minute_sources.json').read_text())}
    for x in d.to_dict('records'):
        base=f.loc[x['sequence']];q=ev.loc[x['sequence']]
        assert x['D2']==base['卖出日'] and x['D3']==base.d3_date
        path=sources[x['sequence']]['path']
        assert a.old.sha(path)==sources[x['sequence']]['sha256']
        bars=pd.read_csv(path).sort_values('time').reset_index(drop=True)
        assert len(bars)==240 and bars.time.is_unique
        expected_clock=pd.date_range(x['D3']+' 09:31',periods=120,freq='min').append(pd.date_range(x['D3']+' 13:01',periods=120,freq='min'))
        assert list(pd.to_datetime(bars.time))==list(expected_clock)
        p2=a.PRIOR/'minutes'/f"{x['D2']}_{x['symbol']}.csv.gz"
        assert a.old.sha(p2)==q.d2_minutes_sha256
        assert (abs(q.d3_close-q.d3_floor)<.001)==bool(q.d3_close_floor)
        expected=None;signal=None
        if x['policy'].startswith('d2_') and q.d2_available:
            expected=('D2','15:00',base.d2_close,'D2_closing_attempt')
        else:
            if not x['policy'].endswith('_close'):
                fixedref=daily.loc[(base.stk_code,x['D3']),'preclose']
                if x['policy'].endswith('above_open'):fixedref=bars.open.iloc[0]
                refs=bars.close.shift(1).fillna(bars.open.iloc[0]) if x['policy'].endswith('uptick') else pd.Series(fixedref,index=bars.index)
                hit=np.flatnonzero((bars.close>refs+.001).to_numpy())
                if len(hit):
                    signal=int(hit[0]);eligible=bars[(bars.index>signal)&(bars.open>q.d3_floor+.001)&(bars.volume>0)]
                    if len(eligible):
                        bar=eligible.iloc[0];expected=('D3',bar.time[11:16],bar.open,'rise_then_next_eligible_open')
                        assert x['trigger_clock']==bars.time.iloc[signal][11:16]
                        assert np.isclose(x['trigger_reference'],refs.iloc[signal])
            if expected is None and bars.close.iloc[-1]>q.d3_floor+.001 and bars.volume.iloc[-1]>0:
                expected=('D3','15:00',bars.close.iloc[-1],'closing_attempt')
        assert bool(x['filled'])==(expected is not None)
        if expected:
            assert (x['day'],x['clock'],x['reason'])==(expected[0],expected[1],expected[3])
            assert np.isclose(x['price'],expected[2])
        else:
            assert pd.isna(x['price']) and pd.isna(x['day']) and x['reason']=='unfilled_at_D3_close'
        shares=10000/base.entry/1.001
        cash=shares*expected[2]*.999 if expected else 0
        mv=0 if expected else shares*q.d3_close
        eligible_actions=actions[actions.stk_code.eq(base.stk_code)&actions.record_date.eq(x['D2'])]
        dividend=shares*eligible_actions.div_cash_pre_tax.sum() if not(expected and expected[0]=='D2') else 0
        assert np.allclose([x['realized_proceeds'],x['open_market_value'],x['dividend_cash']],[cash,mv,dividend],atol=1e-8)
        assert np.isclose(x['marked_return'],(cash+mv+dividend)/10000-1)
        assert np.isclose(x['liquidation_return'],(cash+mv*.999+dividend)/10000-1)
        assert np.isclose(x['delta_to_open_value'],cash+mv+dividend-10000*base.d2_open/base.entry*.999/1.001)
    for s in r['summaries']:
        g=d[d.policy.eq(s['policy'])]
        if s['n']==19:g=g[~g.d2_available]
        assert len(g)==s['n'] and (~g.filled).sum()==s['unfilled']
        total=(g.realized_proceeds+g.open_market_value+g.dividend_cash).sum()
        assert np.isclose(total,s['total_value'])
        assert np.isclose(total/(len(g)*10000)-1,s['mean_marked_return'])
        assert np.isclose(g.liquidation_return.mean(),s['mean_liquidation_return'])
    # Signal candle's own open cannot be used; persistent signal may fill below trigger reference.
    bars=pd.DataFrame({'time':['2026-01-01 09:31:00','2026-01-01 09:32:00','2026-01-01 15:00:00'],
        'open':[9.,9.2,9.], 'close':[10.1,9.1,9.], 'volume':[100,100,100]})
    ex=a.exit_d3(bars,9.,10.,'rise');assert ex['price']==9.2 and ex['clock']=='09:32'
    bars.loc[:,'open']=9.;bars.loc[:,'close']=9.
    assert not a.exit_d3(bars,9.,10.,'rise')['filled']
    checks={'status':'PASS','trades':34,'policy_trade_rows':204,'D3_complete_minutes':34,
       'checks':['Source and code hashes','34 retained in every policy; 15 D2 exits / 19 deferred',
       'Exact 240-bar session grid','Independent signal and next-bar exit reconstruction',
       'No floor-price execution; unfilled has zero sale proceeds and no sale fee',
       'Independent cash / position / dividend accounting','12 summary groups reconcile',
       'Synthetic signal-bar lookahead and all-floor nonfill cases'],
       'verifier_sha256':a.old.sha(__file__)}
    a.write('verification.json',checks);print(json.dumps(checks,ensure_ascii=False))

if __name__=='__main__':run()
