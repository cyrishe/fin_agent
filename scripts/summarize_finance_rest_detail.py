"""Compare pure-data requests with saved conversation evaluations (no model calls)."""
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASES = [
    'outputs/d4f10504-8df6-435e-9316-3d89b5fd1015/finance_query_api_batch_results.json',
    'outputs/financial_qa_dsh_opt_full_20260903/dsh_opt_full_results.json',
    'outputs/financial_qa_mainland_full_increment_20260903/dsh_opt_increment_results.json',
]

def calls_info(calls):
    entries = []
    apis = []
    requests = []
    for c in calls:
        if c.get('tool','').endswith('read_finance_catalog') and c.get('subject') and c.get('dataview'):
            entry = c['dataview'] if '.' in c['dataview'] else c['subject']+'.'+c['dataview']
            if entry not in entries: entries.append(entry)
        request = c.get('submitted_request') or c.get('request')
        if request:
            requests.append(request)
            match = re.search(r'=\s*([a-z_]\w*(?:\.\w+)+)\s*\(', request)
            if match and match[1] not in apis: apis.append(match[1])
    return entries, apis, requests

def summarize(folder):
    late_path=Path(folder)/'late_server_records.json'
    late=json.loads(late_path.read_text()) if late_path.exists() else {}
    baseline={}
    for file in BASES:
        for c in json.loads((ROOT/file).read_text())['cases']:
            baseline[c['question']] = (file, c)
    out=[]
    for p in sorted(Path(folder).glob('*.json')):
        record=json.loads(p.read_text())
        if not isinstance(record, dict) or 'case' not in record: continue
        case=record['case'];r=record.get('response',{});d=r.get('detail',{})
        evidence = 'API detail'
        calls=d.get('tool_calls',[])
        trace_path=Path(folder)/(case['case_id']+'_server_trace.json')
        if not d and trace_path.exists():
            calls=json.loads(trace_path.read_text()).get('tracker',{}).get('calls',[])
            evidence='超时后服务器trace（非API返回）'
        if not d and case['case_id'] in late:
            calls=late[case['case_id']].get('tool_calls',[])
            evidence='超时后服务器完成日志（非API返回）'
        entries,apis,requests=calls_info(calls)
        source,old=baseline.get(case['question'],('',{}))
        oldmeta=old.get('financial_qa') or {}
        oldentries,oldapis,oldrequests=calls_info(oldmeta.get('tool_calls',[]))
        expected=case.get('acceptable_first_entries') or [case.get('dataview') or case.get('primary_entry')]
        expected=[x for x in expected if x]
        statics=[c for c in calls if c.get('tool')=='finance_query']
        out.append({'case_id':case['case_id'],'question':case['question'],'transport':record.get('transport') or ('mcp' if case['case_id'].startswith('RTE') else 'api'),
            'expected_entries':expected,'entries':entries,'apis':apis,'requests':requests,
            'first_matches_expected': entries[0] in expected if entries and expected else None,
            'baseline':source,'baseline_entries':oldentries,'baseline_apis':oldapis,'baseline_requests':oldrequests,
            'baseline_status':old.get('status'),
            'first_matches_baseline':entries[0]==oldentries[0] if entries and oldentries else None,
            'api_set_matches_baseline':set(apis)==set(oldapis) if apis and oldapis else None,
            'ok':r.get('ok'),'http_status':record.get('http_status'),'rows':r.get('execution',{}).get('total_rows'),
            'turns':d.get('turns'),'tokens':d.get('total_tokens'),'elapsed_ms':record.get('client_elapsed_ms'),
            'baseline_elapsed_ms':old.get('total_elapsed_ms'),'baseline_engine_ms':oldmeta.get('duration_ms'),
            'static_failed_calls':sum(bool(c.get('validation_errors') or c.get('error')) for c in statics),
            'provider_retries':sum(c.get('provider_retry_count',0) for c in statics),
            'model_ms':sum(s.get('duration_ms') or 0 for s in d.get('steps',[]) if s.get('kind')=='llm') if d else None,
            'provider_ms':sum(a.get('api_execution_ms') or 0 for c in statics for a in c.get('attempts',[])) if d else None,
            'static_ms':sum(a.get('static_validation_ms') or 0 for c in statics for a in c.get('attempts',[])) if d else None,
            'error':r.get('error'),'problems':record.get('problems'),
            'review_notes':record.get('review_notes',[])+([evidence+'；客户端240秒ReadTimeout，无返回detail。'+(f"后台耗时{late[case['case_id']]['duration_ms']}ms，模型响应{late[case['case_id']]['assistant_message_count']}次；详见逐步消耗，不混入API返回统计。" if case['case_id'] in late else '后台完整Token和Turns尚未知。')] if not d else []),
            'entry_evidence':evidence,
        })
    return out

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('folder');args=parser.parse_args()
    rows=summarize(args.folder)
    Path(args.folder,'comparison.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
    for r in rows: print(json.dumps({k:r[k] for k in ['case_id','entries','baseline_entries','apis','baseline_apis','first_matches_expected','ok']},ensure_ascii=False))
