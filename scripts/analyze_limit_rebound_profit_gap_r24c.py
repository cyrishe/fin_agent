"""Ex-post explanation of frozen R24 return differences; not a new strategy."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
from scripts.replay_limit_rebound_cash_r23 import replay,BASE,dump,sha

SOURCE=Path('outputs/stock_automl/limit_rebound_four_minutes_r24_20261015')
OUT=Path('outputs/stock_automl/limit_rebound_profit_gap_r24c_20261015')


def trade_stats(f):
    r=f.return_.dropna()
    good=r[r.gt(0)];bad=r[r.le(0)]
    known=f[f.observed_3]
    return dict(n=len(f),closed=len(r),pending=int(f.exit_date.isna().sum()),
        mean=float(r.mean()) if len(r) else None,win=float(r.gt(0).mean()) if len(r) else None,
        mean_win=float(good.mean()) if len(good) else None,mean_loss=float(bad.mean()) if len(bad) else None,
        sum_equal_ticket_returns=float(r.sum()),actual_path_realized_pnl=float(f.pnl.dropna().sum()),
        known=len(known),hits=int(known.label_3.sum()),hit=float(known.label_3.mean()) if len(known) else None)


def main():
    OUT.mkdir(exist_ok=True)
    f=pd.read_pickle(SOURCE/'ledger.pkl');calendar=json.loads((BASE/'calendar.json').read_text())
    a=f[f.policy.eq('five_delay')].copy();b=f[f.policy.eq('four_fit_delay')].copy()
    assert not a.entry_date.duplicated().any() and not b.entry_date.duplicated().any()
    five=a.set_index('entry_date');four=b.set_index('entry_date')
    shared=five.index.intersection(four.index);same=shared[five.loc[shared,'symbol'].values==four.loc[shared,'symbol'].values]
    switched=shared.difference(same);only5=five.index.difference(four.index);only4=four.index.difference(five.index)
    groups=[]
    for name,dates in [('同日同股',same),('同日换股',switched),('仅五K',only5),('仅四K',only4)]:
        for code,g in [('five',a),('four',b)]:
            q=g[g.entry_date.isin(dates)]
            if len(q):groups.append(dict(group=name,policy=code,**trade_stats(q)))
    paired=five.loc[same,['symbol','buy','sell','return_','entry_time']].join(four.loc[same,['buy','sell','return_','entry_time']],lsuffix='_five',rsuffix='_four')
    price=paired.buy_four/paired.buy_five-1
    equal_exit=paired.sell_five.eq(paired.sell_four)|(paired.sell_five.isna()&paired.sell_four.isna())
    assert equal_exit.all()
    paired['price_change']=price;paired.to_pickle(OUT/'same_stock_price_pairs.pkl')
    steps=[('五K延后原方案',a),('移除五K独有日期',a[a.entry_date.isin(shared)]),
        ('共同日期换股',pd.concat([a[a.entry_date.isin(same)],b[b.entry_date.isin(switched)]])),
        ('同股改用四K买价',b[b.entry_date.isin(shared)]),('加入四K独有日期',b)]
    attr=[];prev=None;navs=[]
    for name,q in steps:
        s,m,n,l,e=replay(q,calendar,fee_rate=.001);assert not e
        for r in n.itertuples():
            bought=l[l.entry_date.le(r.date)];sold=l[l.exit_date.fillna('9999').le(r.date)]
            held=bought[bought.exit_date.fillna('9999').gt(r.date)]
            expected=1e6-bought.cost.sum()+sold.proceeds.sum()+(held.shares*held.d1_close).sum()
            assert np.isclose(expected,r.nav,rtol=0,atol=1e-7)
        attr.append(dict(step=name,n=s['entries'],return_=s['total_return'],
            change_pp=(s['total_return']-prev)*100 if prev is not None else None))
        prev=s['total_return'];navs.append(n.assign(step=name))
    original={x['policy']:x for x in json.loads((SOURCE/'accounts.json').read_text())}
    assert attr[0]['return_']==original['five_delay']['total_return']
    assert attr[-1]['return_']==original['four_fit_delay']['total_return']
    label_stats=[]
    for code,g in [('five',a),('four',b)]:
        for label in (0,1):
            q=g[g.observed_3&g.label_3.eq(label)]
            label_stats.append(dict(policy=code,label=label,**trade_stats(q)))
    classifications=pd.read_pickle(SOURCE.parent/'limit_rebound_classification_r24b_20261015/predictions.pkl')
    c=classifications[classifications.part.eq('test')&classifications.primary]
    cf=[]
    for code in ['four_old','four_fit']:
        for x,y in [(True,True),(True,False),(False,True),(False,False)]:
            q=c[c.five.eq(x)&c[code].eq(y)]
            cf.append(dict(comparison=code,five_pass=x,four_pass=y,n=len(q),hits=int(q.label_3.sum())))
    result=dict(comparison='five_delay vs four_fit_delay: same fee/capital/exit; per-stock one-bar delay in each strategy, not same wall-clock entry time.',
        overall=[dict(policy='five',**trade_stats(a)),dict(policy='four',**trade_stats(b))],
        partition_counts=dict(shared_same_stock=len(same),shared_different_stock=len(switched),five_only=len(only5),four_only=len(only4)),
        groups=groups,same_stock_prices=dict(n=len(paired),cheaper=int(price.lt(-1e-12).sum()),higher=int(price.gt(1e-12).sum()),
            same=int(price.abs().le(1e-12).sum()),mean_relative_price_change=float(price.mean()),
            closed_pairs=int(paired.return__five.notna().sum()),
            mean_net_five=float(paired.return__five.mean()),mean_net_four=float(paired.return__four.mean())),
        label_groups=label_stats,sequential_account_attribution=attr,classification_transitions=cf,
        limitation='Ex-post diagnostic paths, not executable selection rules. Contribution depends on decomposition order and capital rotation; adding dates measures their particular return quality, not count alone. Label D1-D3 includes days after D2 exit and ignores price/profit magnitude.',
        source_sha256=sha(SOURCE/'ledger.pkl'),daily_cash_checks=sum(len(n) for n in navs))
    dump(OUT/'result.json',result);pd.concat(navs).to_pickle(OUT/'diagnostic_navs.pkl')
    dump(OUT/'groups.json',groups)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
