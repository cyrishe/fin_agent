import json
from pathlib import Path
root=Path(__file__).resolve().parents[1]/'outputs/mcp_both_sample_20260907'
manifest=json.loads((root/'manifest.json').read_text())
for case in manifest['pilot']:
    path=root/(case['case_id']+'.json')
    if not path.exists():continue
    d=json.loads(path.read_text());p=d.get('response',{});detail=p.get('detail',{})
    print('\nCASE',case['case_id'],case['question'], 'OK',p.get('ok'),'ROWS',p.get('execution',{}).get('total_rows'))
    for s in detail.get('steps',[]):
        if s.get('tool')=='finance_query': print('REQUEST',json.dumps(s.get('arguments'),ensure_ascii=False))
    for c in detail.get('tool_calls',[]):
        if c.get('validation_errors') or c.get('error') or c.get('execution_error'):print('ERROR',c)
    for page in p.get('data',{}).get('results',[]):print('ROWS',page.get('row_count'),json.dumps(page.get('rows',[])[:2],ensure_ascii=False))
    print('SUMMARY',p.get('summary'))
