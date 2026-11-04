"""Public MCP both-mode pilot, sampled from historical questions without rewriting."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import time
import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/mcp_both_sample_20260907'
SOURCES = ['outputs/d4f10504-8df6-435e-9316-3d89b5fd1015/source_cases.json',
           'outputs/financial_qa_mainland_eval_20260902/cases_mainland_supported.json',
           'outputs/financial_qa_mainland_full_increment_20260903/cases_increment_no_news.json']
STRATA = ['stock.quote','stock.moneyflow','stock.financial_3_table','stock.margin',
          'stock.pricevalue','stock.shareholder','stock.corporate_action','stock.business_segment',
          'index.quote','index.constitution','industry.constitution','plate.quote','plate.moneyflow',
          'plate.constitution','fund.quote','fund.basic_info','bond.quote','hot_event.base_info',
          'stock.report','stock.report_metric']

def prepare():
    rng = random.Random(202609071)
    groups = defaultdict(list)
    for source in SOURCES:
        for c in json.loads((ROOT/source).read_text())['cases']:
            entries = c.get('required_entries') or [v.strip() for v in c['dataview'].split(',')]
            if source != SOURCES[0] and entries not in [['stock.report'], ['stock.report_metric']]:
                continue
            item = {**c, 'required_entries': entries, 'source_file': source}
            for entry in entries:
                groups[entry].append(item)
    candidates=[]
    for group, pool in sorted(groups.items()):
        # Prefer single-domain historical questions; fill from original combination questions.
        single=[c for c in pool if len(c['required_entries'])==1]
        multi=[c for c in pool if len(c['required_entries'])!=1]
        selected=rng.sample(single,min(5,len(single)))
        selected+=rng.sample(multi,min(5-len(selected),len(multi)))
        candidates.extend({**c,'sample_group':group} for c in selected)
    pilot=[]; used=set()
    for group in STRATA:
        choices=[c for c in candidates if c['sample_group']==group and c['case_id'] not in used]
        c=rng.choice(choices); pilot.append(c);used.add(c['case_id'])
    OUT.mkdir(parents=True,exist_ok=True)
    manifest={'seed':202609071,'sources':SOURCES,'candidates':candidates,'pilot':pilot,
              'note':'每个s+v抽5题，优先单入口；不足时补原始组合题。候选集合保留跨组重复归属，20题实跑去重。'}
    (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    for c in pilot: print(c['case_id'],c['sample_group'],c['question'])
    print('groups',len(groups),'candidate rows',len(candidates))

def run(limit):
    manifest=json.loads((OUT/'manifest.json').read_text())
    vals=dotenv_values(ROOT/'.env')
    key=vals.get('FINANCE_API_KEY') or next(iter(json.loads(vals.get('FINANCE_API_KEYS_JSON') or '{}').values()),None)
    if not key:raise RuntimeError('Missing API key')
    base='https://ai-agent.kingdomai.com/fin_agent/mcp'
    headers={'X-API-Key':key,'Accept':'application/json, text/event-stream'}
    with httpx.Client(timeout=30,headers=headers) as client:
        r=client.post(base,json={'jsonrpc':'2.0','id':'preflight','method':'tools/list','params':{}})
        r.raise_for_status()
        tools=r.json()['result']['tools']
        tool=next(t for t in tools if t['name']=='finance_data_query')
        assert 'both' in tool['inputSchema']['properties']['response_mode']['enum']
    def one(c):
        path=OUT/(c['case_id']+'.json')
        if path.exists():return
        req={'query':c['question'],'response_mode':'both','detail':True,'runtime':'dsh',
             'execution_mode':'standard','research_mode':'fast','max_rows':100}
        start=time.monotonic()
        try:
            with httpx.Client(timeout=330,headers=headers) as client:
                res=client.post(base,json={'jsonrpc':'2.0','id':c['case_id'],'method':'tools/call',
                    'params':{'name':'finance_data_query','arguments':req}})
            wire=res.json(); payload=wire.get('result',{}).get('structuredContent',{})
            result={'case':c,'request':req,'response':payload,'http_status':res.status_code,
                    'mcp_error':wire.get('error') or (wire.get('result',{}).get('content') if wire.get('result',{}).get('isError') else None)}
        except Exception as exc:
            result={'case':c,'request':req,'error':type(exc).__name__}
        result.update(elapsed_seconds=round(time.monotonic()-start,3),finished_at=datetime.now(timezone.utc).isoformat())
        path.write_text(json.dumps(result,ensure_ascii=False,indent=2))
        p=result.get('response',{})
        print(json.dumps({'id':c['case_id'],'ok':p.get('ok'),'seconds':result['elapsed_seconds'],
                          'rows':p.get('execution',{}).get('total_rows'),'summary':bool(p.get('summary')),
                          'tokens':p.get('detail',{}).get('total_tokens'),'error':result.get('error')},ensure_ascii=False),flush=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        for f in as_completed([pool.submit(one,c) for c in manifest['pilot'][:limit]]): f.result()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',type=int);args=p.parse_args()
    if args.run:run(args.run)
    else:prepare()
