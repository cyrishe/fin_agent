"""One real no-upload K-line request through FinancialQaCcService and DSH.

Uses the internal gateway configuration without changing global .env.
No fixture data and no preloaded method. Does not exercise HTTP/UI dispatch.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",default="docs/development_tasks/evidence/kline_live_20260929")
    parser.add_argument("--step-max-tokens",type=int)
    parser.add_argument("--analysis-max-tokens",type=int)
    parser.add_argument("--turn-timeout-seconds",type=int,default=300)
    parser.add_argument("--question",default='请分析平安银行（000001.SZ）截至最新可用交易日最近两周的K线：出现过哪些典型形态，现在还有效吗？结合实际量价给出判断；没有典型形态也请正常分析走势。我没有上传图片。')
    args=parser.parse_args()
    from dotenv import load_dotenv,dotenv_values
    os.chdir(ROOT);load_dotenv(ROOT/'.env',override=False)
    cfg=dotenv_values(ROOT/'.env_tmp')
    os.environ.update(LLM_BASE_URL=cfg['BASE_URL'],LLM_API_KEY=cfg['LLM_KEY'],LLM_DEFAULT_MODEL='BL/deepseek-v4.1-flash',LLM_FLASH_MODEL='BL/deepseek-v4.1-flash',LLM_REASONING_MODEL='BL/deepseek-v4.1-flash',FINANCE_DSH_TURN_TIMEOUT_SECONDS=str(args.turn_timeout_seconds),FINANCE_DSH_MAX_TOKENS=str(args.analysis_max_tokens or 8192))
    from src.scenarios.financial_qa.dsh_service import FinanceDeepSeekHarnessSessionService
    from src.scenarios.financial_qa.service import FinancialQaCcService
    from src.scenarios.financial_qa.tools import FinanceDataQueryCcTools
    from src.services.session_variable_store_service import SessionVariableStoreService
    out=ROOT/args.output
    out.mkdir(parents=True,exist_ok=False)
    def save(name,v): (out/name).write_text(json.dumps(v,ensure_ascii=False,indent=2,default=str))
    question=args.question
    tools=FinanceDataQueryCcTools(result_store=SessionVariableStoreService(data_root=out/'results'))
    policy={'budgets':{stage:{'maxTokens':args.step_max_tokens} for stage in ['query','details','repair','final']}} if args.step_max_tokens else None
    if args.analysis_max_tokens:
        policy=policy or {}
        policy['skillAnalysisMaxTokens']=args.analysis_max_tokens
        policy.setdefault('budgets',{}).setdefault('final',{})['maxTokens']=args.analysis_max_tokens
    dsh=FinanceDeepSeekHarnessSessionService(loop_policy_config=policy,enabled=True,system_tools=tools,worker_count=1,root_dir=out/'runtime',log_path=out/'events.jsonl')
    service=FinancialQaCcService(enabled=True,system_tools=tools,session_service=object(),dsh_session_service=dsh)
    save('manifest.json',{'question':question,'attachments':[],'entry':'FinancialQaCcService.answer + real DSH + actual registered tools; no HTTP/UI or outer dispatcher','explicit_skill_ids':None,'loop_policy_override':policy,'endpoint':cfg['BASE_URL'],'model':dsh.model,'credential_source':'.env_tmp:LLM_KEY','started_at':datetime.now(timezone.utc).isoformat(),'commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'skill_revision':service.business_skill_catalog.revision,'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    start=time.perf_counter()
    def progress(event):
        with (out/'progress.jsonl').open('a') as f:f.write(json.dumps(event,ensure_ascii=False,default=str)+'\n')
        content=event.get('content')
        if isinstance(content,str) and content.strip():print(content[:240],flush=True)
    try:
        result=service.answer(thread_id=int(time.time()*1000),turn_id=1,owner_id='kline-live-eval',user_text=question,attachments=[],dispatch_plan={'selected_agent':'investment_analyst','turn_mode':'normal_qa','entry':'agent_route','semantic_turn':{'resolved_question':question}},runtime='dsh',research_mode='auto',execution_mode='standard',isolated_request=True,event_sink=progress,response_data_max_rows=5)
        save('result.json',result);save('completion.json',{'seconds':time.perf_counter()-start,'returned':True});print('RESULT_SAVED',flush=True)
        if (out/'events.jsonl').exists():
            event=json.loads((out/'events.jsonl').read_text().splitlines()[-1])
            (out/'answer_raw.md').write_text(event.get('result') or '')
    except Exception as e:
        save('completion.json',{'seconds':time.perf_counter()-start,'returned':False,'error_class':type(e).__name__});print('ERROR',type(e).__name__,flush=True)
    finally:dsh.close()

if __name__=='__main__':main()
