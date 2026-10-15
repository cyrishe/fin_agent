"""R18B: audit the Top1 pre-09:40 cap-touch subset, without model changes."""
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
import json, hashlib
import numpy as np
import pandas as pd

BASE=Path('outputs/stock_automl/limit_rebound_segments_r18_20261015')
SRC=BASE/'entry0940_r18a_20261015'
OUT=BASE/'top1_touch_r18b_20261015'

def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def dump(p,x):
    Path(p).write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False)+'\n')

def records(x):
    return json.loads(x.to_json(orient='records',force_ascii=False,double_precision=15))

def clock_first(b,mask):
    return b.loc[mask,'clock'].iloc[0] if mask.any() else None

def metrics(g,name):
    z={'分组':name,'样本':len(g),'三日已知':int(g.observed_3.sum()),
       '三日再板率':float(g.loc[g.observed_3,'label_3'].mean()),
       'D1封板率':float(g.d1_up.mean())}
    for k in (2,3,4):
        v=g[f'return0940_d{k}'].dropna()
        z.update({f'D{k}有效':len(v),f'D{k}均值':float(v.mean()),
                  f'D{k}胜率':float(v.gt(0).mean())})
    return z

def run():
    OUT.mkdir(exist_ok=True)
    f=pd.read_pickle(SRC/'enriched_top5.pkl')
    t=f[f.d0_rank.eq(1)].copy()
    hist=pd.read_csv(OUT/'historical_status_all.csv.gz',dtype=str)
    hist['symbol']=hist.stk_code.str[:6]
    status_by_symbol={k:g for k,g in hist.groupby('symbol')}
    def state(sym,day):
        if pd.isna(day):return None
        h=status_by_symbol[sym];a=h[h.begin_date.le(day)&h.end_date.gt(day)]
        assert len(a)==1,(sym,day)
        return a.st_type.iloc[0]
    for k in (0,1):t[f'historical_type_d{k}']=[state(r.symbol,getattr(r,f'd{k}_date')) for r in t.itertuples()]
    t['ordinary']=t.historical_type_d0.isin(['N','R'])&t.historical_type_d1.isin(['N','R'])
    t.to_pickle(OUT/'top1_with_historical_status.pkl')
    s=t[t.can_buy0940.eq(1)&t.buy_at_cap.eq(1)].copy().sort_values('d0_date')
    assert len(s)==116 and not s.d0_date.duplicated().any()
    needed=set(s.d1_date.str[:7]); daily=[]; sources=[]
    manifest=json.loads((BASE/'source_manifest.json').read_text())
    for item in manifest['raw_sources']:
        p=Path(item['path'])
        if not any(m in p.name for m in needed):continue
        assert sha(p)==item['sha256'],p
        q=pd.read_csv(p,dtype={'stk_code':str,'trade_date':str})
        q['symbol']=q.stk_code.str[:6]
        daily.append(q[q.symbol.isin(s.symbol)&q.trade_date.isin(s.d1_date)].copy())
        sources.append(item)
    ep=Path('outputs/stock_automl/limit_rebound_short_mixed_r12_20261014/price_months/price_2026-10-14_extension.csv.gz')
    q=pd.read_csv(ep,dtype={'stk_code':str,'trade_date':str});q['symbol']=q.stk_code.str[:6]
    daily.append(q[q.symbol.isin(s.symbol)&q.trade_date.isin(s.d1_date)])
    daily=pd.concat(daily).drop_duplicates(['symbol','trade_date'],keep='last').set_index(['symbol','trade_date'])
    sources.append({'path':str(ep),'sha256':sha(ep)})
    feature=pd.read_pickle(BASE/'feature_source_rows.pkl')
    audits=pd.read_pickle(BASE/'segment_audit.pkl').set_index(['symbol','d0_date'])
    out=[];mins=[];charts=[];checks=[];full=[];normal_raw=[];source_hashes={}
    for r in s.itertuples():
        p=SRC/'days'/f'{r.d1_date}_raw.pkl';source_hashes[str(p)]=sha(p)
        raw=pd.read_pickle(p)
        b=raw[raw.symbol.eq(r.symbol)&raw.source.eq(r.source)].sort_values('time').copy()
        b['clock']=pd.to_datetime(b.time).dt.strftime('%H:%M')
        expected=([f'{i//60:02}:{i%60:02}' for i in range(571,691)]+
                  [f'{i//60:02}:{i%60:02}' for i in range(781,901)])
        assert b.clock.tolist()==expected,(r.d0_date,'minute clock')
        d=daily.loc[(r.symbol,r.d1_date)]
        price_checks={key:abs(float(a)-float(d[key])) for key,a in
            [('open',b.open.iloc[0]),('high',b.high.max()),('low',b.low.min()),('close',b.close.iloc[-1])]}
        assert max(price_checks.values())<=.011,(r.d0_date,price_checks)
        assert np.isclose(b.volume.sum(),d.volume,atol=1,rtol=.001)
        cap=float((Decimal(str(d.preclose))*(Decimal('1')+Decimal(str(r.rate)))).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
        assert abs(cap-r.d1_cap)<1e-8 and r.d1_st==0
        assert abs(d.preclose-r.d1_preclose)<1e-8
        assert b.high.le(cap+.011).all()
        assert b.high.ge(b[['open','low','close']].max(axis=1)-.005).all()
        assert b.low.le(b[['open','high','close']].min(axis=1)+.005).all()
        assert b.volume.ge(0).all()
        e=b.iloc[:10].copy();v=e.volume.gt(0)
        touch=v&e.high.ge(cap-.005);below=v&e.low.lt(cap-.005)
        whole=v&e.high.lt(cap-.005);close_below=v&e.close.lt(cap-.005)
        assert touch.any() and below.any() and e.loc[v,'high'].max()==r.buy0940
        assert abs(r.buy0940-cap)<1e-8
        # Rebuild the normal-window return directly from the saved source rows.
        au=audits.loc[(r.symbol,r.d0_date)]
        a=feature[feature.symbol.eq(r.symbol)&feature.trade_date.ge(au.normal_start)&
                  feature.trade_date.le(au.normal_end)].sort_values('trade_date').tail(5)
        assert len(a)==r.normal_days5
        nr=float(np.prod(a.close.to_numpy()/a.preclose.to_numpy())-1)
        assert np.isclose(nr,r.normal_return5,atol=1e-12,rtol=0)
        normal_raw.append(a.assign(d0_date=r.d0_date))
        opening=bool(r.d1_open<cap-.005);at940=bool(e.close.iloc[-1]>=cap-.005)
        pattern=('板下开盘后触板' if opening else '板上开盘后打开')+('，940在板' if at940 else '，940板下')
        below_later=bool((np.flatnonzero(below)>np.flatnonzero(touch)[0]).any())
        touch_later=bool((np.flatnonzero(touch)>np.flatnonzero(below)[0]).any())
        # A bar containing both prices does not reveal their intraminute order.
        whole_vol=float(e.loc[whole,'volume'].sum())
        after_touch_whole=int((whole.to_numpy() & (np.arange(10)>np.flatnonzero(touch)[0])).sum())
        state_label='普通' if r.ordinary else ('退市整理' if r.historical_type_d1=='L' else str(r.historical_type_d1))
        case={'日期':r.d0_date,'D1日期':r.d1_date,'历史状态':state_label,'原表行':int(r.original_order)+2,
          '开盘激活':int(opening),'板前涨幅':r.normal_return5,'原连板':int(r.board_streak_days),
          '开盘涨幅':r.d1_open/d.preclose-1,'涨停价':cap,'买入价':r.buy0940,
          '首触板分':clock_first(e,touch),'首板下分':clock_first(e,below),
          '板下分钟':int(below.sum()),'整分板下':int(whole.sum()),
          '触板后整分板下':after_touch_whole,
          '板下收盘数':int(close_below.sum()),'整分板下量万股':whole_vol/10000,
          '940涨幅':float(e.close.iloc[-1]/d.preclose-1),'940在板':int(at940),
          '窗口最低涨幅':float(e.low.min()/d.preclose-1),'走势':pattern,
          'D1封板':int(r.d1_up),'三日再板':int(r.label_3) if r.observed_3 else None,
          'D2收益':r.return0940_d2,'D3收益':r.return0940_d3,'D4收益':r.return0940_d4}
        out.append(case)
        bar_values=[]
        for i,x in enumerate(e.itertuples()):
            interval=f'09:{30+i:02}—09:{31+i:02}'
            row={'日期':r.d0_date,'D1日期':r.d1_date,'时段':interval,
             '开盘价':x.open,'最高价':x.high,'最低价':x.low,'收盘价':x.close,
             '成交万股':x.volume/10000,'触板':int(touch.iloc[i]),
             '曾在板下':int(below.iloc[i]),'整分板下':int(whole.iloc[i]),
             '收盘涨幅':x.close/d.preclose-1}
            mins.append(row);bar_values.append(row)
        charts.append({'case':case,'preclose':float(d.preclose),'bars':bar_values})
        full.append(b.assign(d0_date=r.d0_date))
        checks.append({'symbol':r.symbol,'d0_date':r.d0_date,'d1_date':r.d1_date,
            'minute_rows':len(b),'ohlc_abs_diff':price_checks,'volume_diff':float(b.volume.sum()-d.volume),
            'cap':cap,'normal_start':str(a.trade_date.iloc[0]),'normal_end':str(a.trade_date.iloc[-1]),
            'normal_days':len(a),'normal_return':nr,'pattern':pattern,
            'later_minute_below_after_touch':below_later,'later_minute_touch_after_below':touch_later,
            'whole_below_minutes':int(whole.sum())})
    cases=pd.DataFrame(out);minute=pd.DataFrame(mins)
    z=s.merge(cases[['日期','整分板下','940在板','走势']],left_on='d0_date',right_on='日期',validate='one_to_one')
    active=z.d1_open.lt(z.d1_cap-.005)
    groups=[metrics(t[t.can_buy0940.eq(1)],'全部可买Top1'),metrics(s,'可买且触板'),
      metrics(t[t.can_buy0940.eq(1)&t.buy_at_cap.eq(0)],'可买未触板'),
      metrics(z[active],'触板且开盘激活'),metrics(z[~active],'触板且板上开盘')]
    groups.extend([metrics(z[z.ordinary],'触板剔除特殊状态'),
                   metrics(z[z.ordinary&active],'开盘激活剔除特殊状态'),
                   metrics(z[z.ordinary&~active],'板上开盘剔除特殊状态')])
    groups.extend(metrics(g,'走势：'+key) for key,g in z.groupby('走势'))
    groups.extend([metrics(z[z['整分板下'].gt(0)],'触板且有整分板下'),metrics(z[z['整分板下'].eq(0)],'触板仅混合分钟'),
      metrics(z[z.normal_return5.lt(0)],'触板且板前负涨幅'),metrics(z[z.normal_return5.ge(0)],'触板且板前非负涨幅')])
    # Observational month summaries are retained for reproducibility, not model selection.
    monthly=[]
    for (m,op),g in z.groupby([z.d0_date.str[:7],z.d1_open.lt(z.d1_cap-.005)]):
        monthly.append({'月份':m,'开盘激活':int(op),**metrics(g,'触板')})
    pd.DataFrame(monthly).to_csv(OUT/'monthly_observation.csv',index=False)
    cases.to_pickle(OUT/'cases.pkl');minute.to_pickle(OUT/'minutes.pkl')
    pd.concat(full).to_pickle(OUT/'verified_full_day_minutes.pkl')
    pd.concat(normal_raw).to_pickle(OUT/'verified_normal_window.pkl')
    daily.reset_index().to_pickle(OUT/'daily_source_subset.pkl')
    dump(OUT/'workbook_data.json',{'核对与对照':records(pd.DataFrame(groups)),
         '触板Top1':records(cases),'早盘分钟':records(minute)})
    dump(OUT/'charts.json',records(pd.DataFrame(charts)))
    dump(OUT/'case_audit.json',checks)
    result={'status':'passed','top1_total':len(t),'target_cases':len(s),'minute_rows':len(minute),
      'full_day_rows':len(pd.concat(full)),'known_d2_d3_d4':[int(s[f'return0940_d{k}'].notna().sum()) for k in (2,3,4)],
      'open_active':int(active.sum()),'open_at_cap':int((~active).sum()),
      'at_cap_0940':int(cases['940在板'].sum()),'below_at_0940':int(cases['940在板'].eq(0).sum()),
      'whole_below_present':int(cases['整分板下'].gt(0).sum()),'only_mixed_minutes':int(cases['整分板下'].eq(0).sum()),
      'whole_below_after_touch':int(cases['触板后整分板下'].gt(0).sum()),
      'special_status_rows':records(s[~s.ordinary][['symbol','d0_date','d1_date','historical_type_d0','historical_type_d1']]),
      'historical_status_sha256':sha(OUT/'historical_status_all.csv.gz'),
      'all_ohlc_volume_cap_and_normal_return_pass':True,
      'source_enriched_sha256':sha(SRC/'enriched_top5.pkl'),
      'daily_sources':sources,'minute_sources':source_hashes,
      'groups':records(pd.DataFrame(groups)),
      'scope':'Existing R18 monthly test forecasts, D0 2025-09-01 through 2026-10-13. Post-hoc exploration only. No fit or selection.'}
    dump(OUT/'audit.json',result)
    print(json.dumps({k:v for k,v in result.items() if k not in ['daily_sources','minute_sources']},ensure_ascii=False,indent=2))

if __name__=='__main__':run()
