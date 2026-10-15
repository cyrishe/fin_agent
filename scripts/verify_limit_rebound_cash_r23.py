"""Independent event-sum accounting and original raw-price checks for R23B."""
import json
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
import numpy as np
import pandas as pd
from scripts.replay_limit_rebound_cash_r23 import OUT,SOURCE,BASE,INITIAL,dump,sha


def main(out=OUT,fee_mode=False):
    OUT=out
    f=pd.read_pickle(OUT/'signals.pkl');l=pd.read_pickle(OUT/'ledger.pkl');n=pd.read_pickle(OUT/'daily_nav.pkl')
    summary=json.loads((OUT/'summary.json').read_text());months=json.loads((OUT/'monthly.json').read_text())
    rate=summary['fee_rate'] if fee_mode else 0.
    original=pd.read_pickle(SOURCE/'policy_picks.pkl')
    original=original[original.policy.eq('body8')&original.d1_date.ge('2026-01-01')]
    pd.testing.assert_frame_equal(f,original)
    assert len(f)==106 and len(l)==106 and not f.d1_date.duplicated().any()
    calendar=json.loads((BASE/'calendar.json').read_text());index={d:i for i,d in enumerate(calendar)}
    rawdir=SOURCE.parent/'limit_rebound_shape_top3_r20_20261015'
    daily=pd.read_pickle(rawdir/'daily_source_subset.pkl')
    daily['trade_date']=daily.trade_date.astype(str).str[:10]
    daily=daily.set_index(['symbol','trade_date'])
    assert daily.index.is_unique
    minute_checks=0;price_checks=0
    for r in l.itertuples():
        raw=pd.read_pickle(rawdir/'minute_days'/f'{r.entry_date}_raw.pkl')
        bars=raw[raw.symbol.eq(r.symbol)&raw.source.eq(r.minute_source)].sort_values('time')
        assert len(bars)==240
        keys=pd.to_datetime(bars.time).dt.strftime('%H:%M')
        bar=bars[keys.eq(r.entry_time)].iloc[0]
        assert bar.volume>0 and bar.open<r.d1_cap-.005
        assert bar.open==r.buy
        eligible=bars[keys.between('09:36','09:40')&bars.open.lt(r.d1_cap-.005)&bars.volume.gt(0)]
        assert pd.Timestamp(eligible.iloc[0].time).strftime('%H:%M')==r.entry_time
        minute_checks+=1
        for k in [1,2] if r.exit_date else [1]:
            date=calendar[index[r.entry_date]+k-1]
            src=daily.loc[(r.symbol,date)]
            assert src.close==getattr(r,f'd{k}_close')
            assert src.preclose==getattr(r,f'd{k}_preclose')
            price_checks+=2
        if r.exit_date:
            assert index[r.exit_date]==index[r.entry_date]+1
            expected=(1+r.ret2)*(1-rate)/(1+rate)-1 if fee_mode else r.ret2
            assert np.isclose(r.return_,expected,atol=1e-14)
        if fee_mode:
            assert np.isclose(r.cost,r.shares*r.buy*(1+rate),atol=1e-7,rtol=0)
            assert np.isclose(r.buy_fee,r.shares*r.buy*rate,atol=1e-7,rtol=0)
            if r.exit_date:
                assert np.isclose(r.sell_fee,r.shares*r.sell*rate,atol=1e-7,rtol=0)
                assert np.isclose(r.proceeds,r.shares*r.sell*(1-rate),atol=1e-7,rtol=0)
            else:assert pd.isna(r.sell_fee)
    # Reconstruct each date from event sums, without the replay position mutation loop.
    navs=[];cash_checks=0;prior=INITIAL
    for r in n.itertuples():
        past_buys=l[l.entry_date.lt(r.date)]
        past_sales=l[l.exit_date.fillna('9999').lt(r.date)]
        cash=INITIAL-past_buys.cost.sum()+past_sales.proceeds.sum()
        held=past_buys[past_buys.exit_date.fillna('9999').ge(r.date)]
        today=l[l.entry_date.eq(r.date)]
        assert len(held)<=1 and len(today)<=1
        assert np.isclose(cash,r.opening_cash,atol=1e-7,rtol=0)
        if len(today):
            amount=cash*(1 if len(held) else .5)
            assert np.isclose(today.cost.iloc[0],amount,atol=1e-7,rtol=0)
        buys=l[l.entry_date.le(r.date)]
        sales=l[l.exit_date.fillna('9999').le(r.date)]
        open_lots=buys[buys.exit_date.fillna('9999').gt(r.date)]
        closing=INITIAL-buys.cost.sum()+sales.proceeds.sum()
        mv=0.
        for lot in open_lots.itertuples():
            src=daily.loc[(lot.symbol,r.date)]
            principal=lot.cost/(1+rate)
            mv+=principal/lot.buy*src.close
        nav=closing+mv
        np.testing.assert_allclose([closing,mv,nav,nav/prior-1],[r.cash,r.market_value,r.nav,r.return_],atol=1e-7,rtol=0)
        assert r.positions==len(open_lots)
        if fee_mode:
            fee=today.buy_fee.sum()+l[l.exit_date.eq(r.date)].sell_fee.sum()
            assert np.isclose(fee,r.fees,atol=1e-7,rtol=0)
        navs.append(nav);prior=nav;cash_checks+=1
    a=np.r_[INITIAL,navs];returns=np.diff(a)/a[:-1]
    dd=1-a/np.maximum.accumulate(a)
    np.testing.assert_allclose(n.drawdown,dd[1:],atol=1e-14)
    assert np.isclose(summary['sharpe'],np.sqrt(252)*returns.mean()/returns.std(ddof=1),atol=1e-12)
    for m in months:
        v=n[n.date.str.startswith(m['month'])]
        prev=INITIAL if v.index[0]==0 else n.nav.iloc[v.index[0]-1]
        seq=np.r_[prev,v.nav]
        assert np.isclose(m['return_'],v.nav.iloc[-1]/prev-1)
        assert np.isclose(m['drawdown'],np.max(1-seq/np.maximum.accumulate(seq)))
    assert np.isclose(np.prod([1+m['return_'] for m in months]),summary['final_nav']/INITIAL)
    assert np.isclose(sum(m['profit'] for m in months),summary['total_profit'])
    if fee_mode:
        assert np.isclose(l.buy_fee.sum()+l.sell_fee.sum(),summary['total_fees'])
        assert np.isclose(sum(m['fees'] for m in months),summary['total_fees'])
        assert np.isclose(summary['final_nav'],INITIAL+l.pnl.sum()+n.unrealized.iloc[-1])
    result=dict(status='PASS',unchanged_signals=len(f),raw_entry_minutes=minute_checks,
        raw_daily_price_fields=price_checks,independent_cash_days=cash_checks,monthly_checks=len(months),
        no_borrowing=True,no_future_sale_cash_used=True,endpoint_marked_not_sold=True,
        spec_sha256=sha(OUT/'specification.json'),signals_sha256=sha(OUT/'signals.pkl'))
    books=list(OUT.glob('*.xlsx'))
    if books:
        assert len(books)==1
        data=json.loads((OUT/'workbook_data.json').read_text());ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        cells=0
        def col(i):
            s='';i+=1
            while i:i,k=divmod(i-1,26);s=chr(65+k)+s
            return s
        with zipfile.ZipFile(books[0]) as z:
            assert z.testzip() is None
            strings=[''.join(x.itertext()) for x in ET.fromstring(z.read('xl/sharedStrings.xml'))] if 'xl/sharedStrings.xml' in z.namelist() else []
            names=[x.get('name') for x in ET.fromstring(z.read('xl/workbook.xml')).find('s:sheets',ns)]
            assert names==list(data)
            for i,name in enumerate(names,1):
                values={}
                for c in ET.fromstring(z.read(f'xl/worksheets/sheet{i}.xml')).findall('.//s:row/s:c',ns):
                    a=c.get('r');t=c.get('t');v=c.find('s:v',ns);assert t!='e'
                    if t=='inlineStr':values[a]=''.join(c.find('s:is',ns).itertext())
                    elif v is not None:values[a]=strings[int(v.text)] if t=='s' else v.text if t=='str' else float(v.text)
                cols=list(data[name][0])
                for j,c in enumerate(cols):assert values[col(j)+'1']==c
                for k,row in enumerate(data[name],2):
                    for j,c in enumerate(cols):
                        expected=row[c];actual=values.get(col(j)+str(k))
                        if c in ['日期','卖出日'] and expected:
                            expected=(datetime.fromisoformat(expected)-datetime(1899,12,30)).days
                        if isinstance(expected,(int,float)):
                            assert actual is not None and np.isclose(expected,actual,rtol=1e-12,atol=1e-12),(name,k,c)
                        else:assert expected==actual,(name,k,c,expected,actual)
                        cells+=1
        result.update(workbook_cells=cells,workbook_sha256=sha(books[0]))
    result['pytest']='5 passed; cash rotation, falling budget, idle-day Sharpe, cross-month marking, fee-aware budget and endpoint' if fee_mode else '4 passed; cash rotation, falling budget, idle-day Sharpe, cross-month marking'
    if fee_mode:result.update(fee_rate=rate,buy_fee_checks=len(l),sell_fee_checks=int(l.exit_date.notna().sum()),zero_fee_reproduction=json.loads((OUT/'gross_reproduction.json').read_text()))
    dump(OUT/'verification.json',result)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
