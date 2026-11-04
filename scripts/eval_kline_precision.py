"""Three independent visual precision probes, separate from the live Skill route.

Uses frozen market examples, one candidate per request, with saved image bytes.
The negative example is an explicit evaluation control, not a business hit.
"""
import base64
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pandas as pd
from dotenv import dotenv_values
from openai import OpenAI
from src.experiments.kline_patterns.catalog import CATALOG
from src.experiments.kline_patterns.engine import prepare
from src.experiments.kline_patterns.render import render
from src.experiments.kline_patterns.visual_review import review
from src.utils.ai_service import _create_llm_completion, extract_first_json


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--numeric-tables',action='store_true')
    parser.add_argument('--output')
    parser.add_argument('--include-fact-control',action='store_true')
    args=parser.parse_args()
    out = ROOT / args.output if args.output else ROOT / 'docs/development_tasks/evidence/kline_precision_20260929' / ('visual_tables' if args.numeric_tables else 'visual')
    out.mkdir(parents=True, exist_ok=False)
    source = ROOT / 'docs/research/kline_pattern_library_20260928'
    localized = ROOT / 'docs/development_tasks/evidence/kline_image_audit_20260929/localized'
    source_cases = {c['case_id']:c for c in json.loads((source/'visual_review_results.json').read_text())['cases']}
    local_cases = {c['case_id']:c for c in json.loads((localized/'cases.json').read_text())}
    patterns = {p['id']:p for p in CATALOG}
    config = dotenv_values(ROOT/'.env_tmp')
    client = OpenAI(base_url=config['BASE_URL'], api_key=config['LLM_KEY'], timeout=180, max_retries=0)
    model = 'BL/deepseek-v4.1-flash'
    report = {'model':model, 'endpoint':config['BASE_URL'], 'credential_source':'.env_tmp:LLM_KEY',
              'started_at':datetime.now(timezone.utc).isoformat(),
              'commit':subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
              'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'prompt_sha256':hashlib.sha256((ROOT/'src/experiments/kline_patterns/visual_review.py').read_bytes()).hexdigest(),
              'prompt_assets':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'src/skills/finance-business/skills/kline-analysis/references').glob('visual-*.md')},
              'renderer_sha256':hashlib.sha256((ROOT/'src/experiments/kline_patterns/render.py').read_bytes()).hexdigest(),
              'numeric_tables':args.numeric_tables,
              'scope':'Independent visual branch; not the FinancialQaCcService tool chain; image-only controls lack exact operands.', 'cases':[]}
    tests = [('two_bullish_image_only','engulf_bull_near_4','engulf_bull',False),
             ('engulf_image_and_data','engulf_bull_hit_1','engulf_bull',True),
             ('tiny_macd_cross_image_only','macd_cross_bull_hit_1','macd_cross_bull',False)]
    if args.include_fact_control:tests.append(('two_bullish_with_facts','engulf_bull_near_4','engulf_bull',True))
    for label,cid,pid,with_data in tests:
        folder=out/label;folder.mkdir()
        source_case=source_cases[cid]
        if cid in local_cases and not args.numeric_tables:
            full_target=local_cases[cid]['facts']['target']
            target={k:v for k,v in full_target.items() if k not in ['charts','instance_id']}
            images=[]
            for item in full_target['charts']:
                dest=folder/Path(item['file']).name
                shutil.copy2(localized/item['file'],dest);images.append(dest)
        else:
            raw=pd.DataFrame(json.loads(s) for s in gzip.open(source/'daily_inputs.jsonl.gz','rt'))
            raw['trade_date']=pd.to_datetime(raw.trade_date)
            fact=source_case['facts']
            b=raw[(raw.stk_code==fact['stock_code']) & (raw.trade_date<=fact['signal_date'])].set_index('trade_date')
            b=b.drop(columns=['open','high','low','close']).rename(columns={f'adj{k}':k for k in ['open','high','low','close']})
            f=prepare(b);end=len(f)-1;images=[]
            for size,bars in [('context',80),('detail',17)]:
                dest=folder/f'{size}.png'
                geometry=render(patterns[pid],f,end,fact['stock_code'],dest,display_bars=bars,target_start=end-1,target_end=end,annotate_values=args.numeric_tables)
                (folder/f'{size}_metadata.json').write_text(json.dumps(geometry,ensure_ascii=False,indent=2))
                images.append(dest)
            target={'symbol':fact['stock_code'],'period':'day','price_basis':'source adjusted',
                    'start_date':str(f.index[end-1].date()),'end_date':str(f.index[end].date()),
                    'confirmation_date':str(f.index[end].date()),
                    'anchors':{'A':{'date':str(f.index[end-1].date()),'offset':-1},'B':{'date':str(f.index[end].date()),'offset':0}}}
        target['instance_id']='C1'
        target['numeric_evidence']='同源条件及量价见附带数据。' if with_data else '本次无OHLC或指标数值表，图上只有坐标轴刻度，无逐点精确数值；仅凭图无法完成的精确比较须保留不确定。'
        if args.numeric_tables and not with_data:target['numeric_evidence']='没有另附数值JSON；图下提供候选K线的同源数值表，未表出的精确值不估算。'
        facts={**source_case['facts'],'target':target} if with_data else {'target':target,'operands':[]}
        requests=[]
        def call(prompt,paths,structured):
            index=len(requests)+1;started=time.perf_counter()
            content=[{'type':'text','text':prompt}];image_records=[]
            for path in paths:
                data=path.read_bytes()
                content.append({'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(data).decode(),'detail':'high'}})
                image_records.append({'path':str(path.relative_to(out)),'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'mime':'image/png'})
            item={'prompt':prompt,'images':image_records,'max_tokens':16384,'structured':structured}
            (folder/f'request_{index}_prompt.txt').write_text(prompt)
            try:
                response=_create_llm_completion([{'role':'user','content':content}],model=model,max_tokens=16384,temperature=0,enable_think=False,response_format={'type':'json_object'} if structured else None,client_instance=client)
                item.update(response=response.choices[0].message.content,finish_reason=response.choices[0].finish_reason,returned_model=response.model,usage=response.usage.model_dump() if response.usage else None)
                if structured:item['parsed']=extract_first_json(item['response'],log_errors=False)
            except Exception as e:
                item.update(error_class=type(e).__name__,http_status=getattr(e,'status_code',None))
            item['seconds']=time.perf_counter()-started
            requests.append(item)
            (folder/f'response_{index}.json').write_text(json.dumps(item,ensure_ascii=False,indent=2))
            print(json.dumps({'case':label,'phase':index,'finish_reason':item.get('finish_reason'),'seconds':item['seconds']},ensure_ascii=False),flush=True)
            return item
        result=review(patterns[pid],facts,images,call)
        report['cases'].append({'id':label,'source_case':cid,'formula_match':source_case['formula_match'],
                                'with_numeric_data':with_data,'facts':facts,**result})
        (out/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    confirmed=[];other=[]
    for case in report['cases']:
        q=case['qualification'];a=case.get('interpretation')
        if (q.get('parsed') or {}).get('match') is True and a and a.get('finish_reason')=='stop' and a.get('response'):
            t=case['facts']['target']
            confirmed.append(f"## {case['id']} | {t.get('symbol', '')} | {t['start_date']}至{t['end_date']}\n\n"+a['response'])
        else:other.append(case['id']+'：'+str((q.get('parsed') or {}).get('assessment') or q.get('error_class') or '未取得完整解读'))
    (out/'synthesis_input.md').write_text('# 待综合的单实例结果\n\n这是独立实验保存的交接文本，尚未发送正式主Agent；模型判断未经过人工核验。\n\n'+'\n\n'.join(confirmed)+'\n\n## 其余实例\n\n'+'\n\n'.join(other))
    client.close()


if __name__=='__main__':main()
