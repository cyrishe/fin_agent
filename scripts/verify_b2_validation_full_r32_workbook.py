"""Read back every R32 Excel header/data cell, including cached formulas and dates."""
from datetime import datetime
import json
import math
from pathlib import Path
import zipfile
from xml.etree import ElementTree as ET
from scripts.experiment_b2_validation_full_r32 import OUT,r

NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
REL='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
def col(i):
    text='';i+=1
    while i:i,j=divmod(i-1,26);text=chr(65+j)+text
    return text

def main():
    p=OUT/'B2_两层一月全期对照_R32_20261019.xlsx'
    source=json.loads((OUT/'workbook_data.json').read_text())
    checked=0;formula_count=0;counts={}
    with zipfile.ZipFile(p) as z:
        assert z.testzip() is None
        strings=[]
        if 'xl/sharedStrings.xml' in z.namelist():
            strings=[''.join(n.itertext()) for n in ET.fromstring(z.read('xl/sharedStrings.xml'))]
        relationships={n.attrib['Id']:n.attrib['Target'] for n in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
        sheets=ET.fromstring(z.read('xl/workbook.xml')).find('s:sheets',NS)
        assert [s.attrib['name'] for s in sheets]==list(source)
        for s in sheets:
            name=s.attrib['name'];target=relationships[s.attrib['{'+REL+'}id']]
            target=target.lstrip('/') if target.startswith('/') else 'xl/'+target
            root=ET.fromstring(z.read(target));cells={}
            for c in root.findall('.//s:sheetData/s:row/s:c',NS):
                typ=c.attrib.get('t');v=c.find('s:v',NS);text=v.text if v is not None else None
                assert typ!='e',(name,c.attrib['r'],text)
                if typ=='s':value=strings[int(text)]
                elif typ=='inlineStr':value=''.join(c.find('s:is',NS).itertext()) if c.find('s:is',NS) is not None else None
                elif typ in ['str','d']:value=text
                elif typ=='b':value=int(text) if text else None
                else:value=float(text) if text else None
                cells[c.attrib['r']]=value
                if c.find('s:f',NS) is not None:formula_count+=1
            data=source[name]
            for i,h in enumerate(data['headers']):assert cells.get(col(i)+'5')==h
            for rownum,row in enumerate(data['rows'],6):
                for i,expected in enumerate(row):
                    actual=cells.get(col(i)+str(rownum));location=(name,col(i)+str(rownum),expected,actual)
                    if expected is None:assert actual is None or actual=='',location
                    elif i in data['dates'] and isinstance(expected,str) and len(expected)==10 and expected[4]=='-':
                        if isinstance(actual,str):assert actual[:10]==expected,location
                        else:assert actual==(datetime.fromisoformat(expected)-datetime(1899,12,30)).days,location
                    elif isinstance(expected,(float,int)):
                        assert isinstance(actual,(float,int)) and math.isclose(actual,expected,rel_tol=1e-12,abs_tol=1e-8),location
                    else:assert expected==actual,location
                    checked+=1
            counts[name]=len(data['rows'])
        assert len([n for n in z.namelist() if n.startswith('xl/tables/') and n.endswith('.xml')])==len(source)
    receipt=dict(file=str(p),sha256=r.sha(p),bytes=p.stat().st_size,verified_data_cells=checked,cached_formulas=formula_count,sheet_rows=counts,zip_integrity=True,formula_errors=0,date_types_checked=True)
    r.dump(OUT/'workbook_verification.json',receipt);print(json.dumps(receipt,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
