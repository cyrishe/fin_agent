"""Bounded full comparison run after manual pilot review; no automatic retries."""
import concurrent.futures as futures
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.eval_finance_rest_detail import ROOT, load_cases, call_case, blocking_problems
from dotenv import dotenv_values
import httpx

def main():
    folder=ROOT/'outputs/finance_api_detail_pilot_20260906'
    values=dotenv_values(ROOT/'.env')
    key=os.getenv('FINANCE_API_KEY') or values.get('FINANCE_API_KEY')
    if not key: key=next(iter(json.loads(values.get('FINANCE_API_KEYS_JSON') or '{}').values()),None)
    if not key: raise SystemExit('Missing API key')
    pending=[]
    for case in load_cases():
        path=folder/(case['case_id']+'.json')
        if path.exists():
            previous=json.loads(path.read_text())
            if blocking_problems(previous,True): raise SystemExit('Unreviewed infrastructure failure: '+case['case_id'])
        else: pending.append(case)
    def run(case):
        try:
            with httpx.Client(timeout=240,headers={'X-API-Key':key,'Accept':'application/json, text/event-stream'}) as client:
                result=call_case(client,'https://ai-agent.kingdomai.com/fin_agent',case,'mcp' if case['case_id'].startswith('RTE') else 'api')
        except Exception as exc:
            result={'case':case,'problems':['transport_error'],'error_type':type(exc).__name__}
        with (folder/(case['case_id']+'.json')).open('x') as out: json.dump(result,out,ensure_ascii=False,indent=2)
        return result
    iterator=iter(pending)
    with futures.ThreadPoolExecutor(max_workers=3) as pool:
        active={pool.submit(run,c) for c in [next(iterator,None) for _ in range(3)] if c}
        stopped=False
        while active:
            done,active=futures.wait(active,return_when=futures.FIRST_COMPLETED)
            for f in done:
                result=f.result(); r=result.get('response',{});d=r.get('detail',{})
                print(json.dumps({'case_id':result['case']['case_id'],'ok':r.get('ok'),'turns':d.get('turns'),'ms':result.get('client_elapsed_ms'),'tokens':d.get('total_tokens'),'problems':result.get('problems')},ensure_ascii=False),flush=True)
                if blocking_problems(result,True): stopped=True
            if not stopped:
                for _ in done:
                    c=next(iterator,None)
                    if c: active.add(pool.submit(run,c))
        if stopped: raise SystemExit('Stopped after infrastructure/detail failure; in-flight requests saved without retry')

if __name__=='__main__': main()
