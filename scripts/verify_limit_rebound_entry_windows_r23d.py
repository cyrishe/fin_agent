"""Verify independent minute facts, delayed account, and preservation of original workbook."""
import json
import zipfile
import xml.etree.ElementTree as ET
import numpy as np
import pandas as pd
from scripts.analyze_limit_rebound_entry_windows_r23d import OUT,SOURCE,BOOK,RAW
from scripts.replay_limit_rebound_cash_r23 import BASE,dump,sha,INITIAL

NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def read_book(path):
    with zipfile.ZipFile(path) as z:
        assert z.testzip() is None
        strings=[''.join(x.itertext()) for x in ET.fromstring(z.read('xl/sharedStrings.xml'))] if 'xl/sharedStrings.xml' in z.namelist() else []
        names=[x.get('name') for x in ET.fromstring(z.read('xl/workbook.xml')).find('s:sheets',NS)]
        styles=ET.fromstring(z.read('xl/styles.xml'));nf=styles.find('s:numFmts',NS)
        nfs={int(x.get('numFmtId')):x.get('formatCode') for x in ([] if nf is None else nf)}
        xfs=list(styles.find('s:cellXfs',NS));cells={};formats={};panes={};merged={};tables={}
        for i,name in enumerate(names,1):
            root=ET.fromstring(z.read(f'xl/worksheets/sheet{i}.xml'));values={};fm={}
            for c in root.findall('.//s:sheetData/s:row/s:c',NS):
                a=c.get('r');t=c.get('t');v=c.find('s:v',NS);assert t!='e'
                if t=='inlineStr':values[a]=''.join(c.find('s:is',NS).itertext())
                elif v is not None:values[a]=strings[int(v.text)] if t=='s' else v.text if t=='str' else float(v.text)
                xf=xfs[int(c.get('s','0'))];num=int(xf.get('numFmtId','0'));fm[a]=nfs.get(num,num)
                formula=c.find('s:f',NS);assert formula is None  # Original report is static, not flattened by this edit.
            cells[name]=values;formats[name]=fm
            panes[name]=[x.attrib for x in root.findall('.//s:pane',NS)]
            merged[name]=[x.get('ref') for x in root.findall('.//s:mergeCell',NS)]
        for p in z.namelist():
            if p.startswith('xl/tables/table') and p.endswith('.xml'):
                r=ET.fromstring(z.read(p));tables[r.get('name')]=dict(ref=r.get('ref'),
                    columns=[x.get('name') for x in r.find('s:tableColumns',NS)],
                    style=r.find('s:tableStyleInfo',NS).attrib,filter=r.find('s:autoFilter',NS).get('ref'))
    return cells,formats,panes,merged,tables


def main():
    f=pd.read_pickle(OUT/'windows.pkl');b=pd.read_pickle(OUT/'minute_evidence.pkl');l=pd.read_pickle(SOURCE/'ledger.pkl')
    assert len(f)==len(l)==106 and len(b)==530
    checks=0
    for r in f.itertuples():
        lot=l[l.entry_date.eq(r.entry_date)].iloc[0]
        w=b[b.entry_date.eq(r.entry_date)].sort_values('time')
        after=w[w.from_entry];clock=pd.to_datetime(w.time).dt.strftime('%H:%M')
        assert clock.tolist()==['09:36','09:37','09:38','09:39','09:40']
        flag=[v>0 and h<cap-.005 for v,h,cap in zip(after.volume,after.high,after.cap)]
        below=[v>0 and low<cap-.005 for v,low,cap in zip(after.volume,after.low,after.cap)]
        prefix=next((i for i,x in enumerate(flag) if not x),len(flag))
        assert r.whole_minutes==sum(flag) and r.below_minutes==sum(below) and r.whole_prefix==prefix
        assert r.entry_whole==flag[0] and np.isclose(r.entry_share_ratio,lot.shares/after.volume.iloc[0])
        later=after.iloc[1:];eligible=later[(later.volume>0)&(later.open<later.cap-.005)]
        if len(eligible):
            assert r.delayed_price==eligible.open.iloc[0]
            assert r.delayed_time==pd.Timestamp(eligible.time.iloc[0]).strftime('%H:%M')
        else:assert pd.isna(r.delayed_index)
        checks+=1
    # Independent recomputation of delayed same-stock cash rotation, no early use of sale proceeds.
    dl=pd.read_pickle(OUT/'delay_ledger.pkl');dn=pd.read_pickle(OUT/'delay_daily_nav.pkl')
    ds=json.loads((OUT/'delay_summary.json').read_text());calendar=json.loads((BASE/'calendar.json').read_text());ix={d:i for i,d in enumerate(calendar)}
    prior=INITIAL;nav=[]
    for day in dn.itertuples():
        buys=dl[dl.entry_date.lt(day.date)];sales=dl[dl.exit_date.fillna('9999').lt(day.date)]
        morning=INITIAL-buys.cost.sum()+sales.proceeds.sum();held=buys[buys.exit_date.fillna('9999').ge(day.date)]
        today=dl[dl.entry_date.eq(day.date)]
        if len(today):assert np.isclose(today.cost.iloc[0],morning*(1 if len(held) else .5),atol=1e-7,rtol=0)
        all_buys=dl[dl.entry_date.le(day.date)];all_sales=dl[dl.exit_date.fillna('9999').le(day.date)]
        cash=INITIAL-all_buys.cost.sum()+all_sales.proceeds.sum();mv=0.
        for lot in all_buys[all_buys.exit_date.fillna('9999').gt(day.date)].itertuples():
            k=ix[day.date]-ix[lot.entry_date]+1
            mv+=lot.cost/1.001/lot.buy*getattr(lot,f'd{k}_close')
        np.testing.assert_allclose([cash,mv,cash+mv,(cash+mv)/prior-1],
            [day.cash,day.market_value,day.nav,day.return_],atol=1e-7,rtol=0)
        nav.append(cash+mv);prior=cash+mv
    a=np.r_[INITIAL,nav];rr=np.diff(a)/a[:-1]
    assert np.isclose(ds['max_drawdown'],np.max(1-a/np.maximum.accumulate(a)))
    assert np.isclose(ds['sharpe'],np.sqrt(252)*rr.mean()/rr.std(ddof=1))
    dest=OUT/'百万资金轮换_信号与交易窗口_R23D_20261015.xlsx'
    before=read_book(SOURCE/BOOK);after=read_book(dest);assert before[0].keys()==after[0].keys()
    kept=0
    for name,cells in before[0].items():
        for addr,v in cells.items():
            new=after[0][name].get(addr)
            assert (np.isclose(v,new,atol=1e-12,rtol=1e-12) if isinstance(v,float) else v==new),(name,addr,v,new)
            assert before[1][name][addr]==after[1][name][addr],(name,addr,'number format')
            kept+=1
        assert before[2][name]==after[2][name],(name,'panes')
        assert set(before[3][name]).issubset(after[3][name]),(name,'merged')
    for name,t in before[4].items():
        u=after[4][name];assert t['style']==u['style']
        if name=='R23CTable4':
            assert u['ref']=='A1:AD107' and u['filter']==u['ref']
            assert u['columns'][:16]==t['columns'] and len(u['columns'])==30
        else:assert t==u
    rows=json.loads((OUT/'added_columns.json').read_text());newcells=0
    def col(i):
        s='';i+=1
        while i:i,k=divmod(i-1,26);s=chr(65+k)+s
        return s
    for j,row in enumerate(rows,2):
        for i,(name,value) in enumerate(row.items(),16):
            assert after[0]['买卖明细'][col(i)+'1']==name
            actual=after[0]['买卖明细'].get(col(i)+str(j))
            assert (np.isclose(value,actual,rtol=1e-12,atol=1e-12) if isinstance(value,(float,int)) else value==actual),(j,name)
            newcells+=1
    result=dict(status='PASS',trades=106,minute_bars=530,independent_window_checks=checks,
        delayed_cash_days=len(dn),original_cells_preserved=kept,new_cells_checked=newcells,
        all_original_number_formats_panes_and_table_styles_preserved=True,
        tests='3 targeted window tests; 5 existing cash/fee tests',
        source_workbook_sha256=sha(SOURCE/BOOK),workbook_sha256=sha(dest))
    dump(OUT/'verification.json',result);print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
