"""Verify every new entry/return and preserve pre-existing workbook contents."""
import json,zipfile,xml.etree.ElementTree as ET
import numpy as np,pandas as pd
from scripts.enrich_limit_rebound_segments_r18_0940 import BASE,OUT,CLOCK
from scripts.experiment_limit_rebound_short_mixed_r12 import dump,sha

NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def contents(path):
    with zipfile.ZipFile(path) as z:
        assert z.testzip() is None
        strings=[]
        if 'xl/sharedStrings.xml' in z.namelist():
            strings=[''.join(x.itertext()) for x in ET.fromstring(z.read('xl/sharedStrings.xml'))]
        sheets=[]
        for i in range(1,6):
            root=ET.fromstring(z.read(f'xl/worksheets/sheet{i}.xml'));cells={}
            for c in root.findall('.//s:sheetData/s:row/s:c',NS):
                v=c.find('s:v',NS);kind=c.get('t')
                if kind=='inlineStr':value=''.join(c.find('s:is',NS).itertext())
                elif v is None:continue
                elif kind=='s':value=strings[int(v.text)]
                elif kind in ('str','e'):value=v.text
                else:value=float(v.text)
                cells[c.attrib['r']]=value
            pane=root.find('s:sheetViews/s:sheetView/s:pane',NS)
            sheets.append((cells,pane.attrib if pane is not None else None))
        names=[x.get('name') for x in ET.fromstring(z.read('xl/workbook.xml')).find('s:sheets',NS)]
        tables={}
        for name in z.namelist():
            if name.startswith('xl/tables/') and name.endswith('.xml'):
                t=ET.fromstring(z.read(name));tables[t.attrib['name']]=t.attrib['ref']
        return names,sheets,tables


def equal(a,b):
    if isinstance(a,(int,float)) and isinstance(b,(int,float)):
        return bool(np.isclose(a,b,rtol=1e-13,atol=1e-13))
    return a==b


def run():
    f=pd.read_pickle(OUT/'enriched_top5.pkl');old=pd.read_pickle(BASE/'segments_test_top5.pkl')
    for c in ['symbol','d0_date','score','d0_rank','label_3','observed_3']:
        pd.testing.assert_series_equal(f[c].reset_index(drop=True),old[c].reset_index(drop=True),check_names=True)
    checked=0
    for day,g in f[f.minute_ok.eq(True)].groupby('d1_date'):
        raw=pd.read_pickle(OUT/'days'/f'{day}_raw.pkl')
        for r in g.itertuples():
            b=raw[raw.symbol.eq(r.symbol)&raw.source.eq(r.source)].sort_values('time')
            clock=pd.to_datetime(b.time).dt.strftime('%H:%M');early=b[clock.isin(CLOCK)&b.volume.gt(0)]
            can=bool(early.low.lt(r.d1_cap-.005).any() and r.d1_st==0)
            assert r.can_buy0940==int(can)
            if can:
                assert r.buy0940==early.high.max()
                assert r.buy_at_cap==int(r.buy0940>=r.d1_cap-.005)
            else:assert pd.isna(r.buy0940)
            checked+=1
    for r in f.itertuples():
        good=r.can_buy0940==1 and pd.notna(r.d1_close) and r.d1_close>0
        for k in (2,3,4):
            close=getattr(r,f'd{k}_close');pre=getattr(r,f'd{k}_preclose');prev=getattr(r,f'd{k-1}_close')
            good=good and pd.notna(close) and pd.notna(pre) and close>0 and pre>0 and abs(pre-prev)<=.011
            value=getattr(r,f'return0940_d{k}')
            if good:assert np.isclose(value,close/r.buy0940-1,rtol=0,atol=1e-12)
            else:assert pd.isna(value)
    src=BASE/'涨停前与连板段_特征对照及前五_R18_20261015.xlsx'
    dst=OUT/'涨停分段模型_前五_940前最高价买入_D2D4收益_R18A_20261015.xlsx'
    a,asheets,at=contents(src);b,bsheets,bt=contents(dst);assert a==b
    patches=json.loads((OUT/'endpoint_cell_patches.json').read_text())
    changed={f'{c}{r["excel_row"]}' for r in patches for c in ['E','G']}
    preserved=0
    for i,((before,pa),(after,pb)) in enumerate(zip(asheets,bsheets)):
        assert pa==pb
        for cell,val in before.items():
            if i==0 and cell=='A26' or i==3 and cell in changed:continue
            assert cell in after and equal(val,after[cell]),(i,cell,val,after.get(cell))
            preserved+=1
    assert set(at)==set(bt)
    for name,ref in at.items():assert bt[name]==('A1:AA1336' if name=='R18Table4' else ref)
    rows=json.loads((OUT/'append_rows.json').read_text());app=bsheets[3][0]
    for j,row in enumerate(rows,2):
        for col,value in zip(['V','W','X','Y','Z','AA'],row.values()):
            actual=app.get(f'{col}{j}')
            assert (actual is None if value is None else equal(value,actual)),(j,col,value,actual)
    for r in patches:
        assert app[f'E{r["excel_row"]}']==r['open_activated']
        assert app[f'G{r["excel_row"]}']==r['d1_sealed']
    v={'status':'passed','raw_minute_rows_checked':checked,'returns_checked':len(f)*3,
       'existing_cells_preserved':preserved,'endpoint_rows_completed':len(patches),'new_columns':6,
       'same_sheets_and_freeze_panes':True,'table_extended':True,'unit_tests':4,
       'source_workbook_sha256':sha(src),'workbook_sha256':sha(dst),'workbook_bytes':dst.stat().st_size,
       'model_scores_ranks_labels_unchanged':True}
    dump(OUT/'verification.json',v);print(v)


if __name__=='__main__':run()
