"""Read back R18B exports and reconcile them to the user's R18A workbook."""
import json,zipfile,re
import xml.etree.ElementTree as ET
from datetime import datetime
import numpy as np
import pandas as pd
from scripts.audit_limit_rebound_segments_r18_touch import OUT,SRC,sha,dump

NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
def read(path):
    with zipfile.ZipFile(path) as z:
        assert z.testzip() is None
        strings=[''.join(s.itertext()) for s in ET.fromstring(z.read('xl/sharedStrings.xml'))] if 'xl/sharedStrings.xml' in z.namelist() else []
        names=[s.get('name') for s in ET.fromstring(z.read('xl/workbook.xml')).find('s:sheets',NS)]
        sheets={}
        for i,name in enumerate(names,1):
            root=ET.fromstring(z.read(f'xl/worksheets/sheet{i}.xml'));d={}
            for c in root.findall('.//s:sheetData/s:row/s:c',NS):
                v=c.find('s:v',NS);t=c.get('t');a=c.get('r')
                assert t!='e',(path,a)
                if t=='inlineStr':d[a]=''.join(c.find('s:is',NS).itertext())
                elif v is not None:d[a]=strings[int(v.text)] if t=='s' else v.text if t=='str' else float(v.text)
            sheets[name]=d
        return sheets

def equal(a,b):
    if b is None:return a is None
    if isinstance(b,(int,float)):return a is not None and np.isclose(a,b,atol=1e-12,rtol=1e-12)
    return a==b

def col(n):
    s='';n+=1
    while n:n,k=divmod(n-1,26);s=chr(65+k)+s
    return s

def run():
    xlsx=OUT/'Top1触板_分钟核对与收益_R18B_20261015.xlsx'
    old=SRC/'涨停分段模型_前五_940前最高价买入_D2D4收益_R18A_20261015.xlsx'
    original=read(old)['分段模型前五'];new=read(xlsx)
    data=json.loads((OUT/'workbook_data.json').read_text());cells=0
    for name,rows in data.items():
        cols=list(rows[0]);sheet=new[name]
        for i,c in enumerate(cols):assert sheet[col(i)+'1']==c
        for j,r in enumerate(rows,2):
            for i,c in enumerate(cols):
                v=r[c]
                if c in ['日期','D1日期']:v=(datetime.fromisoformat(v)-datetime(1899,12,30)).days
                assert equal(sheet.get(col(i)+str(j)),v),(name,i,j,v)
                cells+=1
    cases=data['触板Top1'];assert len(cases)==116
    for r in cases:
        n=str(r['原表行'])
        assert original['C'+n]==original['V'+n]==original['X'+n]==1
        for c,key in [('L','板前涨幅'),('W','买入价'),('Y','D2收益'),('Z','D3收益'),('AA','D4收益')]:
            assert equal(original.get(c+n),r[key]),(n,c,r[key])
    f=pd.read_pickle(SRC/'enriched_top5.pkl').set_index('original_order')
    count=0
    for r in cases:
        x=f.loc[r['原表行']-2];good=True
        for k in (2,3,4):
            good=good and pd.notna(x[f'd{k}_close']) and x[f'd{k}_close']>0 and abs(x[f'd{k}_preclose']-x[f'd{k-1}_close'])<=.011
            assert equal(r[f'D{k}收益'],x[f'd{k}_close']/r['买入价']-1 if good else None)
            count+=1
    html=OUT/'Top1触板_全部分钟K_R18B_20261015.html'
    payload=re.search(r'<script type="application/json" id="payload">(.*?)</script>',html.read_text(),re.S).group(1)
    charts=json.loads(payload);assert charts==json.loads((OUT/'charts.json').read_text())
    assert len(charts)==116 and all(len(x['bars'])==10 for x in charts)
    assert [x['case'] for x in charts]==cases
    assert [b for x in charts for b in x['bars']]==data['早盘分钟']
    a=json.loads((OUT/'audit.json').read_text());assert a['status']=='passed'
    result={'status':'passed','export_cells_reconciled':cells,'original_excel_target_rows':len(cases),
      'return_cells_recalculated':count,'candlestick_cases':len(charts),'minute_rows':1160,
      'original_workbook_sha256':sha(old),'workbook_sha256':sha(xlsx),'html_sha256':sha(html),
      'all_116_prices_consistent_with_saved_daily':True,'special_status_cases':len(a['special_status_rows'])}
    dump(OUT/'verification.json',result);print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':run()
