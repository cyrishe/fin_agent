"""One blind arithmetic audit of selected positive/negative cases using configured DS Flash."""
import json,random,time
from pathlib import Path
from src.utils.ai_service import _create_llm_completion,DEFAULT_FLASH_MODEL,extract_first_json
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'docs/research/kline_pattern_library_20260928'
def main():
 pats={x['id']:x for x in json.loads((OUT/'patterns.json').read_text())}
 cases=list(map(json.loads,(OUT/'cases.jsonl').read_text().splitlines()))
 chosen={'engulf_bull','morning_doji','three_methods_bull','macd_div_bull','rsi_failure_bull','boll_squeeze_bull'}
 cases=[c for c in cases if c['pattern_id'] in chosen];random.Random(20260928).shuffle(cases)
 tasks=[];mapping={}
 for i,c in enumerate(cases):
  key=f'case{i+1:02}';mapping[key]=c
  tasks.append({'id':key,'pattern':pats[c['pattern_id']]['name'],'conditions':[{'expr':e['expr'],'values':e['operands']} for e in c['evidence']]})
 prompt='你是公式复核员。这些是盲序打乱的真实K线候选，有命中也有反例。只按提供的数值和条件逐项判断，所有条件AND，&为并且、|为或者。不按你自己的形态口径替换公式，不预测收益。返回JSON对象 {"cases":[{"id":"case01","match":true/false/null,"reason":"简短指出关键条件"},...]}。数据：\n'+json.dumps(tasks,ensure_ascii=False)
 start=time.perf_counter();report={'model':DEFAULT_FLASH_MODEL,'purpose':'text-only blind arithmetic consistency audit, not vision or independent expert labels','case_count':len(cases)}
 try:
  r=_create_llm_completion([{'role':'user','content':prompt}],model=DEFAULT_FLASH_MODEL,max_tokens=3500,temperature=0,enable_think=False)
  content=r.choices[0].message.content;parsed=extract_first_json(content,log_errors=False) or {};answers={x['id']:x for x in parsed.get('cases',[]) if isinstance(x,dict) and 'id' in x}
  comparisons=[]
  for k,c in mapping.items():
   a=answers.get(k,{});m=a.get('match');comparisons.append({'blind_id':k,'case_id':c['case_id'],'formula_result':c['matched'],'model_result':m,'agrees':type(m)is bool and m==c['matched'],'reason':a.get('reason','missing')})
  report.update({'response':content,'comparisons':comparisons,'agreement_count':sum(x['agrees'] for x in comparisons),'usage':r.usage.model_dump() if r.usage else None,'batch_seconds':time.perf_counter()-start})
 except Exception as e:report.update({'error_class':type(e).__name__,'batch_seconds':time.perf_counter()-start})
 report['requests']=tasks
 (OUT/'ds_flash_text_audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
 print(json.dumps({k:v for k,v in report.items() if k not in ('requests','response','comparisons')},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
