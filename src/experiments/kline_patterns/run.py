"""Scan frozen market snapshot, select a few cases per pattern, replay and time hits only."""
from __future__ import annotations
import ast,gzip,hashlib,json,platform,subprocess,time
from pathlib import Path
import numpy as np
import pandas as pd
from .catalog import CATALOG,SOURCES
from .engine import prepare,detect,evaluate,REVISION,parse
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'docs/research/kline_pattern_library_20260928'

def clean(v):
 if v is None or v is pd.NA:return None
 if isinstance(v,dict):return {k:clean(x) for k,x in v.items()}
 if isinstance(v,(tuple,list)):return [clean(x) for x in v]
 if isinstance(v,np.generic):v=v.item()
 if isinstance(v,float) and not np.isfinite(v):return None
 return v

def dump(path,v):path.write_text(json.dumps(clean(v),ensure_ascii=False,indent=2,allow_nan=False))

def operands(expr,f,at):
 values={}
 for n in ast.walk(parse(expr)):
  if isinstance(n,ast.Subscript):
   token=ast.unparse(n);values[token]=evaluate(token,f,at)
 return clean(values)

def main():
 from .render import render
 OUT.mkdir(parents=True,exist_ok=True);(OUT/'images').mkdir(exist_ok=True)
 raw=pd.DataFrame([json.loads(s) for s in gzip.open(OUT/'daily_inputs.jsonl.gz','rt')]);raw['trade_date']=pd.to_datetime(raw.trade_date)
 pools={p['id']:[] for p in CATALOG};near={p['id']:[] for p in CATALOG};frames={};fs={};coverage=[]
 for code,b in raw.groupby('stk_code',sort=False):
  b=b.set_index('trade_date').drop(columns=['open','high','low','close']).rename(columns={'adjopen':'open','adjhigh':'high','adjlow':'low','adjclose':'close'})
  f=prepare(b);frames[code]=b;fs[code]=f
  coverage.append({'code':code,'rows':len(b),'invalid_or_zero_volume':int((f.valid==0).sum()),'first':str(b.index[0].date()),'last':str(b.index[-1].date())})
  for p in CATALOG:
   result=detect(p,f);hits=np.flatnonzero(result.fillna(False).to_numpy(bool))
   pools[p['id']].extend((code,int(t)) for t in hits)
   checks=pd.DataFrame({k:evaluate(c['expr'],f) for k,c in enumerate(p['conditions'])})
   score=checks.fillna(False).sum(axis=1)
   near_idx=np.flatnonzero(((score==len(p['conditions'])-1)&checks.notna().all(axis=1)&(f.valid==1)).to_numpy(bool))
   near[p['id']].extend((code,int(t)) for t in near_idx[-5:])
  print('scanned',code,flush=True)
 # Fixed source order, spread across securities and dates. No future returns used in sampling.
 samples=[];stats=[]
 for p in CATALOG:
  selected=[];seen=set()
  for code,t in sorted(pools[p['id']],key=lambda z:(z[0],-z[1])):
   if code not in seen:selected.append((code,t));seen.add(code)
   if len(selected)==3:break
  if len(selected)<3:
   for cand in pools[p['id']]:
    if cand not in selected and all(cand[0]!=c or abs(cand[1]-t)>=10 for c,t in selected):selected.append(cand)
    if len(selected)==3:break
  timings=[];renders=[];cold=[];cases=[]
  pair=[(c,t,True) for c,t in selected]
  if near[p['id']]:
   c,t=near[p['id']][-1];pair.append((c,t,False))
  for idx,(code,t,expected) in enumerate(pair):
   f=fs[code];b=frames[code]
   case_id=f'{p["id"]}_{"hit" if expected else "near"}_{idx+1}'
   evidence=[]
   for cond in p['conditions']:
    value=evaluate(cond['expr'],f,t)
    evidence.append({**cond,'result':None if pd.isna(value) else bool(value),'operands':operands(cond['expr'],f,t)})
   item={'case_id':case_id,'pattern_id':p['id'],'stock_code':code,'signal_date':str(f.index[t].date()),'matched':expected,'evidence':evidence,'input_bars_from_anchor':t+1,'image':f'images/{case_id}.png','same_formula_replay':None}
   if expected:
    # Cached condition time: average of 25 repeated positive decisions after one warmup.
    assert bool(detect(p,f,t));started=time.perf_counter_ns()
    for _ in range(25):assert bool(detect(p,f,t))
    cached=(time.perf_counter_ns()-started)/25/1e6
    # Standalone positive replay includes shared feature preparation from the fixed anchor.
    started=time.perf_counter_ns();prefix=prepare(b.iloc[:t+1]);replay=detect(p,prefix,t);cold_ms=(time.perf_counter_ns()-started)/1e6
    assert bool(replay);item['same_formula_replay']=True
    timings.append(cached);cold.append(cold_ms);item['cached_match_ms']=cached;item['prepare_and_match_ms']=cold_ms
    started=time.perf_counter_ns();render(p,f,t,code,OUT/item['image']);render_ms=(time.perf_counter_ns()-started)/1e6
    item['render_ms']=render_ms;renders.append(render_ms)
   else:
    assert not bool(detect(p,f,t));render(p,f,t,code,OUT/item['image'])
   # Keep only the displayed OHLC window as a convenience; full fixed-anchor snapshot is separately hashed.
   snap=b.iloc[max(0,t-79):t+1][['open','high','low','close','volume']].reset_index();item['display_bars']=json.loads(snap.to_json(orient='records',date_format='iso'))
   samples.append(item);cases.append(case_id)
  stats.append({'id':p['id'],'name':p['name'],'origin':p['origin'],'total_matches':len(pools[p['id']]),'tested_hits':len(selected),'near_miss_examples':len(pair)-len(selected),
    'mean_cached_match_ms':float(np.mean(timings)) if timings else None,'mean_prepare_and_match_ms':float(np.mean(cold)) if cold else None,'mean_render_ms':float(np.mean(renders)) if renders else None,'case_ids':cases})
  print('sampled',p['id'],len(selected),'of',len(pools[p['id']]),flush=True)
 for p in CATALOG:p['examples']=[x['case_id'] for x in samples if x['pattern_id']==p['id']]
 dump(OUT/'patterns.json',CATALOG);dump(OUT/'results.json',stats)
 (OUT/'cases.jsonl').write_text(''.join(json.dumps(clean(v),ensure_ascii=False,allow_nan=False)+'\n' for v in samples))
 files=list(Path(__file__).parent.glob('*.py'))+[ROOT/'src/services/technical_indicator_calculator.py',ROOT/'tests/test_kline_pattern_lab.py']
 manifest={'run_at':pd.Timestamp.now(tz='UTC').isoformat(),'head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'revision':REVISION,
 'environment':{'python':platform.python_version(),'pandas':pd.__version__,'numpy':np.__version__,'platform':platform.platform()},
 'file_sha256':{str(x.relative_to(ROOT)):hashlib.sha256(x.read_bytes()).hexdigest() for x in files},'input_sha256':hashlib.sha256((OUT/'daily_inputs.jsonl.gz').read_bytes()).hexdigest(),
 'workload':{'stocks':len(frames),'bars':len(raw),'patterns':len(CATALOG),'matched_samples':sum(s['tested_hits'] for s in stats),'near_miss_samples':sum(s['near_miss_examples'] for s in stats)},'coverage':coverage,
 'timing':'Only successful selected samples are timed. cached_match_ms uses precomputed features and 25 warm repeats per sample; prepare_and_match_ms replays each positive prefix once including the entire shared feature stack; render_ms separate. No DB/network/model time included; excludes rejected cases. Not full-market SLA.',
 'validation':'Positive prefix replay, scalar/vector checks and 1 real near-miss per pattern. Same-definition verification, not independent labelled accuracy or trading profitability.',
 'model_calls':0}
 dump(OUT/'run_manifest.json',manifest)
if __name__=='__main__':main()
