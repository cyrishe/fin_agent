"""Independent scalar reconstruction and saved-model/month-table checks for CO25."""
import joblib
import numpy as np
import pandas as pd
from scripts.research_low_base_co25 import OUT,REPORT,FEATURES,predict,dump,sha


def scalar(q,t,market5):
    c=q.close.to_numpy();pc=q.preclose.to_numpy();hi=q.high.to_numpy();lo=q.low.to_numpy()
    op=q.open.to_numpy();vol=q.volume.to_numpy();amt=q.amount.to_numpy()
    good=np.isfinite(q[['open','high','low','close','preclose','volume','amount']]).all(axis=1).to_numpy()
    good &= (q[['open','high','low','close','preclose','volume','amount']]>0).all(axis=1).to_numpy()
    good &= (hi>=c)&(hi>=op)&(lo<=c)&(lo<=op)&(hi>=lo)
    ratio=np.where(good,c/pc,np.nan)
    chain=np.cumprod(np.where(np.isfinite(ratio),ratio,1));chain[~good]=np.nan
    ah=hi/c*chain;al=lo/c*chain
    tr=np.array([max(hi[k]-lo[k],abs(hi[k]-pc[k]),abs(lo[k]-pc[k]))/pc[k] if good[k] else np.nan for k in range(len(q))])
    f={'position60':(chain[t]-min(al[t-59:t+1]))/(max(ah[t-59:t+1])-min(al[t-59:t+1])),
       'return5':chain[t]/chain[t-5]-1,'return20':chain[t]/chain[t-20]-1,
       'ma5_slope3':np.mean(chain[t-4:t+1])/np.mean(chain[t-7:t-2])-1,
       'range5':max(ah[t-4:t+1])/min(al[t-4:t+1])-1,
       'tr_contraction':np.mean(tr[t-4:t+1])/np.mean(tr[t-24:t-4]),
       'volume_contraction':np.mean(vol[t-4:t+1])/np.mean(vol[t-24:t-4]),
       'close_location':(c[t]-lo[t])/(hi[t]-lo[t]) if hi[t]>lo[t] else .5,
       'excess5':chain[t]/chain[t-5]-1-market5,
       'log_amount20':np.log1p(np.mean(amt[t-20:t])),
       'floor_ratio':min(al[t-2:t+1])/min(al[t-12:t-2])}
    low=f['position60']<=.35 and f['return20']<=.08
    stable=low and f['return5']>=-.02 and f['ma5_slope3']>=-.01 and f['floor_ratio']>=.99
    compact=stable and f['range5']<=.08 and f['tr_contraction']<=.85 and f['volume_contraction']<=.90
    f.update(low=low,stable=stable,compact=compact)
    for h in [2,3]:
        future=np.prod(ratio[t+1:t+h+1]);f[f'event{h}']=future-1
        lock=hi[t+h]==lo[t+h] and ratio[t+h]<.955
        f[f'net{h}']=future/(op[t+1]/pc[t+1])*.999/1.001-1 if good[t+1] and hi[t+1]>lo[t+1] and not lock else np.nan
    return f


def main():
    p=pd.read_parquet(OUT/'panel.parquet');raw=pd.read_parquet(OUT/'daily_inputs.parquet')
    cal=pd.to_datetime(pd.read_csv(OUT/'calendar.csv').date);index={x.strftime('%Y-%m-%d'):i for i,x in enumerate(cal)}
    samples=[]
    for year in ['2024','2025','2026']:
        z=p[p.date.str.startswith(year)]
        samples += [z.sample(20,random_state=25),z[z.compact].sample(20,random_state=26)]
    sample=pd.concat(samples).drop_duplicates('sample_id')
    market=pd.read_csv(REPORT/'daily_counts.csv').set_index('date').market_return
    # Sample dates are after the start; fetch the warm-up market if required by
    # the Jan2024 sample. Compute independent valid-row daily means from raw.
    good=(raw[['open','high','low','close','preclose','volume','amount']]>0).all(axis=1)
    good &= raw.high.ge(raw.open)&raw.high.ge(raw.close)&raw.low.le(raw.open)&raw.low.le(raw.close)&raw.high.ge(raw.low)
    mr=(raw.close/raw.preclose-1).where(good).groupby(raw.trade_date).mean()
    m5=(mr+1).rolling(5).apply(np.prod,raw=True)-1
    frozen=[]
    for code,g in sample.groupby('stk_code'):
        q=raw[raw.stk_code.eq(code)].set_index('trade_date').reindex(cal)
        for row in g.itertuples(index=False):
            t=index[row.date];expected=scalar(q,t,m5.loc[pd.Timestamp(row.date)])
            for k,v in expected.items():
                actual=getattr(row,k)
                if isinstance(v,(bool,np.bool_)):assert actual==v,(row.sample_id,k)
                else:np.testing.assert_allclose(actual,v,rtol=2e-10,atol=2e-12,equal_nan=True,err_msg=f'{row.sample_id} {k}')
            part=q.iloc[t-60:t+4].copy();part['sample_id']=row.sample_id;part['stk_code']=code
            frozen.append(part.reset_index())
    pd.concat(frozen,ignore_index=True).to_parquet(OUT/'independent_check_inputs.parquet',index=False)
    predictions=pd.read_parquet(OUT/'predictions.parquet');model_count=0
    for month in sorted(predictions.month.unique()):
        first=pd.Period(month).start_time.strftime('%Y-%m-%d')
        since=(pd.Period(month)-6).start_time.strftime('%Y-%m-%d')
        for h in [2,3]:
            members=pd.read_csv(OUT/'models'/month/f'members_d{h}.csv.gz')
            train=p.set_index('sample_id').loc[members.sample_id]
            expected=p[p.stable&p.date.ge(since)&p.date.lt(first)&p[f'label_end{h}'].lt(first)&p[f'net{h}'].notna()]
            assert members.sample_id.tolist()==expected.sample_id.tolist()
            assert train[f'label_end{h}'].max()<first
            np.testing.assert_allclose(members.groupby('date').sample_weight.sum().std(),0,atol=1e-10)
            z=predictions[predictions.month.eq(month)&predictions.horizon.eq(h)]
            test=p.set_index('sample_id').loc[z.sample_id]
            np.testing.assert_array_equal(predict(joblib.load(OUT/'models'/month/f'd{h}.joblib'),test),z.predicted_net)
            model_count+=1
    picks=pd.read_csv(REPORT/'model_top3.csv.gz',dtype={'stk_code':str},float_precision='round_trip')
    monthly_checks=0;s=pd.read_csv(REPORT/'summary.csv')
    for method in ['ridge_top3','narrow_top3']:
        for (month,h),z in predictions.groupby(['month','horizon']):
            key='predicted_net' if method=='ridge_top3' else 'range5'
            selected=[]
            for day,g in z.groupby('date'):
                selected.extend(g.sort_values([key,'stk_code'],ascending=[key!='predicted_net',True]).head(3).sample_id.tolist())
            got=picks[picks.method.eq(method)&picks.month.eq(month)&picks.horizon.eq(h)]
            assert set(selected)==set(got.sample_id)
            v=p.set_index('sample_id').loc[selected,f'net{h}'].dropna()
            r=s[s.method.eq(method)&s.period.eq(month)&s.horizon.eq(h)&s.target.eq('net')].iloc[0]
            assert len(v)==r.known
            cls=v.round(10)
            np.testing.assert_allclose([r['mean'],r.up,r.strong,r.loss2],[v.mean(),cls.gt(0).mean(),cls.ge(.03).mean(),cls.lt(-.02).mean()],atol=1e-12)
            monthly_checks+=1
    dump(REPORT/'verification.json',{'independent_scalar_rows':len(sample),'features_and_labels_agree':True,
        'model_readbacks':model_count,'membership_and_maturity_checks':model_count,'monthly_pick_checks':monthly_checks,
        'tests':'7 passed; pytest tests/experiments/test_low_base_co25.py -q',
        'test_fixture_repairs':'Independent copies of OHLC arrays; correct prior20 volume hand denominator 0.98; no production formula change.',
        'panel_sha256':sha(OUT/'panel.parquet'),'predictions_sha256':sha(OUT/'predictions.parquet')})
    print('VERIFIED',len(sample),model_count,monthly_checks,flush=True)


if __name__=='__main__':main()
