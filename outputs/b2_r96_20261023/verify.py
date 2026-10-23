import json,zipfile,xml.etree.ElementTree as E
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path('/Volumes/ext/fin_agent')
OUT=ROOT/'outputs/b2_r96_20261023'
d=json.loads((ROOT/'docs/stock_automl_runs/20261023_b2_minute_review_r96/review_data.json').read_text())
z=zipfile.ZipFile(OUT/'B2_R96_分钟原始数据核对.xlsx')
ns={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
def cells(idx):
    return {c.attrib['r']:c for c in E.fromstring(z.read(f'xl/worksheets/sheet{idx}.xml')).findall('.//m:c',ns)}
def number(c):
    assert c.attrib.get('t','n')=='n', c.attrib
    return float(c.find('m:v',ns).text)
checked=0
for idx,c in enumerate(d['cases'],2):
    cs=cells(idx)
    assert 'A246' in cs and 'A247' not in cs
    for row,r in enumerate(c['raw'],6):
        assert number(cs[f'A{row}'])==r['api_order']
        seconds=round((number(cs[f'C{row}'])-25569)*86400)
        assert datetime.fromtimestamp(seconds,timezone.utc).strftime('%Y-%m-%d %H:%M:%S')==r['trade_time']
        for col,key in [('D','open'),('E','high'),('F','low'),('G','close'),('H','vol'),('I','amount')]:
            assert number(cs[f'{col}{row}'])==r[key], (idx,row,col)
        checked+=1
cs=cells(6)
assert 'A245' in cs and 'A246' not in cs
for row,r in enumerate(d['cases'][0]['standard'],6):
    seconds=round((number(cs[f'C{row}'])-25569)*86400)
    assert datetime.fromtimestamp(seconds,timezone.utc).strftime('%Y-%m-%d %H:%M:%S')==r['time']
    for col,key in [('D','open'),('E','high'),('F','low'),('G','close'),('H','volume'),('I','turnover')]:
        assert number(cs[f'{col}{row}'])==r[key]
assert number(cells(1)['E6'])==6.68
result=dict(raw_rows_verified=checked,standard_rows_verified=240,numeric_cells=True,timestamps_match_source_to_second=True,summary_low=6.68)
(OUT/'verification.json').write_text(json.dumps(result,indent=2))
print(result)
