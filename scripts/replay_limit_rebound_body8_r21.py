"""R21B: original Top3 choice, body > -8%; rejected daily choice is NOT replaced."""
from __future__ import annotations
import argparse,json
from pathlib import Path
import joblib,numpy as np,pandas as pd
from scripts.experiment_limit_rebound_shape_top3_r20 import OUT as SOURCE,BASE,select,stats
from scripts.experiment_limit_rebound_shape_r19 import mask
from scripts.experiment_limit_rebound_short_mixed_r12 import dump,sha,xmatrix

OUT=Path('outputs/stock_automl/limit_rebound_body8_nofallback_r21b_20261015')
MONTHS=[str(m) for m in pd.period_range('2025-01','2026-10',freq='M')]
POLICIES={'old':'原形态','body8':'原形态＋实体>-8%'}
EXITS={'D2':2,'D3':3,'D4':4,'策略':None}


def body_keep(series):return series.gt(-.08+1e-12)


def select_policy(frame,policy):
    original=select(frame,3,True)
    return original if policy=='old' else original[original.body_keep]


def prepare():
    OUT.mkdir(exist_ok=True)
    spec={'name':'R21B_Top3_shape_body_above_minus8_no_replacement','threshold':'D1 C09:35/O09:30-1 > -0.08 strictly; equal excluded',
      'user_choice':'Add to original shape rules; earlier period separately marked. User correction: if new rule filters original daily choice, no replacement; keep cash that day.',
      'stage1':'Saved R18 daily-only models for each month Jan2025..Oct2026; no retraining, no D1 minute feature in D0 RF.',
      'early':'Jan-Aug2025 uses first saved September2025 R19 shape rule for retrospective coverage; this rule was selected using Jan-Jun training and Jul-Aug validation and is NOT contemporaneous or independently heldout in early months.',
      'later':'Sep2025..Oct2026 uses each month original frozen R19 rule; extra -8 threshold is user-proposed after seeing historical results, not a new blind test.',
      'entry':'First find original R20 daily choice: first eligible bar-end 09:36..09:40 open below cap, simultaneous candidates sorted by D0 rank<=3. Then keep only if body>-8%; otherwise NO TRADE for the entire day, never replace with another rank or later minute.',
      '36bar':'09:36 means 09:35-09:36. Full high/low is retrospective display only, never an input to the opening-price execution decision.',
      'prices':'Buy return and 09:36 high/low returns relative to D1 preclose. Five-minute body relative to D1 open.',
      'portfolio':'NAV starts1, each entry min(25% prior closing NAV, available cash), <=4 overlapping lots, no debt, daily close mark-to-market, sells at close fund next morning. Fractional shares and zero fees/slippage, as prior R13/R15 gross convention.',
      'exits':'Separate accounts for D2/D3/D4 close; strategy exits D2 sealed close else D3 sealed close else D4 close. Pending endpoint positions remain marked at known daily closes.',
      'anomalies':'Chosen records with historical missing/nonpositive or discontinuous prices up to relevant exit are excluded from that account, no stock replacement. Ex-post data cleaning disclosed, never claimed knowable at entry. Pending future observations not excluded.',
      'monthly':'NAV end/prior-month-end-1; monthly close-based maximum drawdown resets peak at prior month NAV. Account months are holding-calendar months; trade counts/means use D1 entry month. Final Oct2026 incomplete.',
      'source_rules_sha256':sha(SOURCE/'rules_frozen.json'),'source_data_sha256':sha(SOURCE/'enriched_top3.pkl')}
    p=OUT/'specification.json'
    if p.exists():assert json.loads(p.read_text())==spec
    else:dump(p,spec)
    cached=pd.read_pickle(SOURCE/'enriched_top3.pkl')
    data=cached.drop_duplicates(['symbol','d0_date']).drop(columns=['score','d0_rank','model_month','part','shape_pass'])
    source_rules=json.loads((SOURCE/'rules_frozen.json').read_text());rules={s['month']:s['selected'] for s in source_rules}
    first=source_rules[0]['selected'];frames=[];models=[];fullrules=[]
    for month in MONTHS:
        path=BASE/month/'segments_forecast_scores.pkl';s=pd.read_pickle(path);s=s[s.d0_rank.le(3)]
        g=s.merge(data,on=['symbol','d0_date'],validate='one_to_one');assert len(g)==len(s)
        g['model_month']=month;g['phase']='早期回看' if month<'2025-09' else '原测试段'
        rule=rules.get(month,first);g['rule_text']=rule['text'];g['rule_source']=month if month in rules else '2025-09后定'
        g['shape_pass']=g.eligible&mask(g,rule);g['body_keep']=body_keep(g.body5);g['new_pass']=g.shape_pass&g.body_keep
        frames.append(g);fullrules.append({'month':month,'source':g.rule_source.iloc[0],'phase':g.phase.iloc[0],'rule':rule,'body_min_exclusive':-.08})
        bundle=joblib.load(BASE/month/'segments.joblib');keys=pd.DataFrame(bundle['training_keys'])
        start,end=str(pd.Period(month,'M')-8),str(pd.Period(month,'M')-3)
        assert keys.d0_date.min()>=start+'-01' and keys.d0_date.max()<str(pd.Period(end,'M')+1)+'-01'
        score=bundle['model'].predict_proba(xmatrix(g,bundle['features']))[:,1]
        assert np.array_equal(score,g.score.to_numpy()),month
        models.append({'month':month,'train_start':start,'train_end':end,'actual_min':keys.d0_date.min(),'actual_max':keys.d0_date.max(),
          'fit_rows':len(keys),'model_path':str(BASE/month/'segments.joblib'),'model_sha256':sha(BASE/month/'segments.joblib'),
          'scores_path':str(path),'scores_sha256':sha(path),'reproduced_top3_scores':len(g)})
    f=pd.concat(frames,ignore_index=True).sort_values(['d0_date','d0_rank']);assert len(f)==1284
    assert not f.duplicated(['d0_date','d0_rank']).any()
    # Read each selected source only once; preserve the 09:36 fields as display evidence.
    bars=[]
    for day,g in f[f.audit_ok.eq(True)].groupby('d1_date'):
        raw=pd.read_pickle(SOURCE/'minute_days'/f'{day}_raw.pkl')
        for r in g.itertuples():
            b=raw[raw.symbol.eq(r.symbol)&raw.source.eq(r.minute_source)].sort_values('time');assert len(b)==240
            bar=b.iloc[5];assert pd.Timestamp(bar.time).strftime('%H:%M')=='09:36'
            bars.append({'symbol':r.symbol,'d0_date':r.d0_date,'m36_open':float(bar.open),'m36_high':float(bar.high),
              'm36_low':float(bar.low),'m36_close':float(bar.close),'m36_volume':float(bar.volume)})
    f=f.merge(pd.DataFrame(bars),on=['symbol','d0_date'],how='left',validate='one_to_one')
    f['entry_ret']=np.where(f.trade,f.entry/f.d1_preclose-1,np.nan)
    f['m36_high_ret']=f.m36_high/f.d1_preclose-1;f['m36_low_ret']=f.m36_low/f.d1_preclose-1
    picks=[]
    for policy in POLICIES:
        a=select_policy(f,policy).copy();a['policy']=policy;picks.append(a)
        keys=set(zip(a.symbol,a.d0_date));f[f'selected_{policy}']=[(s,d) in keys for s,d in zip(f.symbol,f.d0_date)]
    picked=pd.concat(picks,ignore_index=True);f.to_pickle(OUT/'all_candidates.pkl');picked.to_pickle(OUT/'policy_picks.pkl')
    dump(OUT/'models.json',models);dump(OUT/'rules.json',fullrules)
    dump(OUT/'data_coverage.json',{'rows':len(f),'D0_days':int(f.d0_date.nunique()),'first_D0':f.d0_date.min(),'last_D0':f.d0_date.max(),
      'first_D1':f.d1_date.dropna().min(),'last_D1':f.d1_date.dropna().max(),'audited':int(f.audit_ok.eq(True).sum()),
      'eligible':int(f.eligible.sum()),'selected_old':int(f.selected_old.sum()),'selected_new':int(f.selected_body8.sum()),
      'new_rejected_body':int((f.shape_pass&~f.body_keep).sum()),'frozen_original_rules_sha256':sha(SOURCE/'rules_frozen.json')})
    print('prepared',len(f),'selected',picked.groupby('policy').size().to_dict(),flush=True)


def dd(values):
    a=np.asarray(values,float);return float(np.max(1-a/np.maximum.accumulate(a)))


def horizon(row,exit_name):
    if exit_name!='策略':return EXITS[exit_name]
    return 2 if row.d2_up==True else 3 if row.d3_up==True else 4


def account(frame,exit_name,calendar,start,end):
    """Cash-funded closing NAV; incomplete endpoint positions remain open."""
    dates=[(i,d) for i,d in enumerate(calendar) if start<=d<=end]
    end_i=dates[-1][0];index={d:i for i,d in enumerate(calendar)}
    known=frame[frame.d1_date.ge(start)&frame.d1_date.le(end)].sort_values('d1_date')
    assert not known.d1_date.duplicated().any()
    entries={};excluded=[]
    for r in known.itertuples():
        begin=index[r.d1_date];gap=horizon(r,exit_name);last=min(gap,end_i-begin+1);reason=None
        if not np.isfinite(r.d1_preclose-r.close) or abs(r.d1_preclose-r.close)>.011:reason='买日前复权参考不连续'
        for k in range(1,last+1):
            price=getattr(r,f'd{k}_close')
            if not np.isfinite(price) or price<=0:reason='历史持仓收价缺失';break
            delta=getattr(r,f'd{k}_preclose')-getattr(r,f'd{k-1}_close') if k>=2 else 0.
            if k>=2 and (not np.isfinite(delta) or abs(delta)>.011):
                reason='持仓参考价不连续';break
        if reason:excluded.append({'symbol':r.symbol,'d0_date':r.d0_date,'d1_date':r.d1_date,'reason':reason});continue
        entries[r.d1_date]=(r,gap)
    cash=1.;prior=1.;positions=[];daily=[];ledger=[];maxpos=0;cash_skips=0
    for i,date in dates:
        if date in entries:
            r,gap=entries[date];budget=min(cash,prior*.25)
            if budget>1e-12:
                assert len(positions)<4
                pos={'row':r,'start':i,'gap':gap,'shares':budget/r.entry,'cost':budget,'buy':r.entry}
                positions.append(pos);cash-=budget;maxpos=max(maxpos,len(positions))
                ledger.append({'symbol':r.symbol,'d0_date':r.d0_date,'entry_date':date,'buy':r.entry,'cost':budget,
                  'shares':pos['shares'],'exit_date':None,'proceeds':None,'return':None})
                pos['ledger']=len(ledger)-1
            else:cash_skips+=1
        keep=[];mv=0.
        for p in positions:
            k=i-p['start']+1;assert 1<=k<=p['gap']
            price=float(getattr(p['row'],f'd{k}_close'));value=p['shares']*price
            if k==p['gap']:
                cash+=value;ledger[p['ledger']].update(exit_date=date,proceeds=value,return_=price/p['buy']-1)
                ledger[p['ledger']]['return']=ledger[p['ledger']].pop('return_')
            else:keep.append(p);mv+=value
        positions=keep;nav=cash+mv;assert cash>=-1e-12 and nav>0
        daily.append({'date':date,'month':date[:7],'nav':nav,'cash':cash,'market_value':mv,'positions':len(positions)})
        prior=nav
    realized=sum(t['proceeds']-t['cost'] for t in ledger if t['exit_date'])
    unrealized=sum(p['shares']*getattr(p['row'],f'd{end_i-p["start"]+1}_close')-p['cost'] for p in positions)
    assert np.isclose(prior,1+realized+unrealized,rtol=0,atol=1e-10)
    monthly=[];previous=1.;n=pd.DataFrame(daily);l=pd.DataFrame(ledger)
    for month,g in n.groupby('month',sort=True):
        endnav=float(g.nav.iloc[-1]);tr=known[known.d1_date.str.startswith(month)]
        entered=l[l.entry_date.str.startswith(month)] if len(l) else l
        vals=entered['return'].dropna() if len(entered) else pd.Series(dtype=float)
        monthly.append({'月份':month,'退出':exit_name,'信号笔数':len(tr),'账户买入':len(entered),'收益有效笔':len(vals),
          '每笔均值':float(vals.mean()) if len(vals) else None,'胜率':float(vals.gt(0).mean()) if len(vals) else None,
          '账户月收益':endnav/previous-1,'月内回撤':dd(np.r_[previous,g.nav]),'月末净值':endnav,
          '月末持仓':int(g.positions.iloc[-1]),'异常排除':sum(x['d1_date'].startswith(month) for x in excluded)})
        previous=endnav
    result={'signals':len(known),'entries':len(ledger),'closed':sum(t['exit_date'] is not None for t in ledger),
      'pending':len(positions),'excluded':len(excluded),'cash_skips':cash_skips,'total_return':prior-1,
      'max_drawdown':dd(np.r_[1.,n.nav]),'monthly_mean_return':float(np.mean([x['账户月收益'] for x in monthly])),
      'positive_months':sum(x['账户月收益']>0 for x in monthly),'months':len(monthly),'max_positions':maxpos,
      'closing_cash':cash,'closing_market_value':float(n.market_value.iloc[-1])}
    return result,monthly,daily,ledger,excluded


def report():
    f=pd.read_pickle(OUT/'all_candidates.pkl');p=pd.read_pickle(OUT/'policy_picks.pkl')
    calendar=json.loads((BASE/'calendar.json').read_text());end=calendar[-1]
    results=[];month_rows=[];navs=[];ledgers=[];excluded=[];trade_stats={}
    for policy in POLICIES:
        chosen=p[p.policy.eq(policy)]
        for scope,start in [('全期含早期回看','2025-01-01'),('原测试段','2025-09-01')]:
            q=chosen[chosen.d0_date.ge(start)]
            trade_stats[f'{policy}_{scope}']=stats(q)
            for exit_name in EXITS:
                # Account start is D1 calendar month; skip a previous-month D0 only in standalone scope.
                s,m,n,l,e=account(q,exit_name,calendar,start,end)
                ctx={'policy':policy,'scope':scope,'exit':exit_name}
                results.append({**ctx,**s})
                if scope=='全期含早期回看':month_rows.extend([{'方案':POLICIES[policy],**r} for r in m])
                navs.extend([{**ctx,**r} for r in n]);ledgers.extend([{**ctx,**r} for r in l]);excluded.extend([{**ctx,**r} for r in e])
    dump(OUT/'account_results.json',results);dump(OUT/'monthly_results.json',month_rows);dump(OUT/'trade_stats.json',trade_stats)
    pd.DataFrame(navs).to_pickle(OUT/'daily_nav.pkl');pd.DataFrame(ledgers).to_pickle(OUT/'cash_ledger.pkl');dump(OUT/'account_exclusions.json',excluded)
    print(json.dumps(results,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('stage',choices=['prepare','report']);v=a.parse_args();globals()[v.stage]()
