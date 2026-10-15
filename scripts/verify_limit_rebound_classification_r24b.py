"""Read final XLSX caches and reconcile them with independent classification counts."""
import json
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
import numpy as np
from scripts.analyze_limit_rebound_classification_r24b import OUT,DOC,dump,sha

NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
BOOK='四K五K_分类指标与混淆矩阵_R24B_20261015.xlsx'


def column(i):
    s='';i+=1
    while i:i,j=divmod(i-1,26);s=chr(65+j)+s
    return s


def main():
    book=OUT/BOOK
    with zipfile.ZipFile(book) as z:
        assert z.testzip() is None
        strings=[''.join(x.itertext()) for x in ET.fromstring(z.read('xl/sharedStrings.xml'))] if 'xl/sharedStrings.xml' in z.namelist() else []
        names=[x.get('name') for x in ET.fromstring(z.read('xl/workbook.xml')).find('s:sheets',NS)]
        sheets={};formulas=0
        for i,name in enumerate(names,1):
            root=ET.fromstring(z.read(f'xl/worksheets/sheet{i}.xml'));cells={}
            for c in root.findall('.//s:sheetData/s:row/s:c',NS):
                assert c.get('t')!='e',(name,c.get('r'))
                if c.find('s:f',NS) is not None:formulas+=1
                v=c.find('s:v',NS);kind=c.get('t');address=c.get('r')
                if kind=='inlineStr':cells[address]=''.join(c.find('s:is',NS).itertext())
                elif v is not None and v.text is not None:cells[address]=strings[int(v.text)] if kind=='s' else v.text if kind=='str' else float(v.text)
            sheets[name]=cells
        assert len([x for x in z.namelist() if x.startswith('xl/tables/table') and x.endswith('.xml')])==2
    payload=json.loads((OUT/'workbook_data.json').read_text());notes=json.loads((OUT/'workbook_notes.json').read_text());checked=0
    for name in ['分类对照','逐月对照']:
        rows=payload[name];cols=list(rows[0]);s=sheets[name]
        for j,k in enumerate(cols):assert s[column(j)+'5']==k
        for i,row in enumerate(rows,6):
            for j,k in enumerate(cols):
                expected=row[k];actual=s.get(column(j)+str(i))
                if k=='目标月':expected=(datetime.fromisoformat(expected)-datetime(1899,12,30)).days
                if expected is None:assert actual in (None,''),(name,i,k,actual)
                elif isinstance(expected,(int,float)):assert actual is not None and np.isclose(actual,expected,atol=1e-11,rtol=0),(name,i,k,expected,actual)
                else:assert actual==expected,(name,i,k,expected,actual)
                checked+=1
    s=sheets['规则与口径']
    for i,note in enumerate(notes,6):assert s['B'+str(i)]==note;checked+=1
    for i,r in enumerate(payload['规则与口径'],len(notes)+10):
        assert s['A'+str(i)]==(datetime.fromisoformat(r['目标月'])-datetime(1899,12,30)).days
        assert s['B'+str(i)]==f"训练 {r['训练段']}；验证 {r['验证段']}。五K：{r['五K规则']}。四K重选：{r['四K重选']}。";checked+=2
    spec=json.loads((OUT/'specification.json').read_text())
    for source in spec['source_hashes']:assert sha(Path(source['path']))==source['sha256']
    result=json.loads((OUT/'verification.json').read_text())
    result.update(export_cells_checked=checked,export_formula_cells=formulas,export_sha256=sha(book),source_hashes_still_unchanged=True)
    dump(OUT/'verification.json',result)
    files=[p for p in OUT.iterdir() if p.is_file() and p.name!='asset_manifest.json']+[DOC/'README.md',
        Path('scripts/analyze_limit_rebound_classification_r24b.py'),Path('scripts/build_limit_rebound_classification_r24b.mjs'),Path(__file__)]
    dump(OUT/'asset_manifest.json',{'files':[{'path':str(p),'sha256':sha(p),'bytes':p.stat().st_size} for p in files]})
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
