"""Frozen-data, paired text/image experiment. Does not change the business pipeline.

Run --prepare, then --call. Model requests are sequential, cached and inspectable.
Definitions are pinned to the recorded experiment; later catalog revisions do not
silently change its reference answers or preselected positive examples.
"""
from __future__ import annotations
import argparse
import base64
import gzip
import hashlib
import json
import platform
import subprocess
import time
from pathlib import Path

import numpy as np
import pandas as pd
from src.experiments.kline_patterns.analysis import candidate_facts, subject_interval
from src.experiments.kline_patterns.engine import prepare, detect
from src.experiments.kline_patterns.render import render
from src.experiments.kline_patterns.run import clean

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/development_tasks/evidence/kline_numeric_visual_20260929/final'
DATA = ROOT / 'docs/research/kline_pattern_library_20260928/daily_inputs.jsonl.gz'
DEFINITIONS = ROOT / 'docs/development_tasks/evidence/kline_numeric_visual_20260929/definitions.json'
PATTERNS = {p['id']:p for p in json.loads(DEFINITIONS.read_text())}
MODEL = 'BL/deepseek-v4.1-flash'


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean(value), ensure_ascii=False, indent=2, allow_nan=False))


def reference(pid, f):
    """Independent direct-array specification; never calls the expression evaluator."""
    o,h,l,c = (f[k] for k in ('o','h','l','c'))
    if pid == 'engulf_bull':
        return (c.shift(2)<c.shift(7)) & (c.shift(1)<o.shift(1)) & (c>o) & (o<=c.shift(1)) & (c>=o.shift(1)) & ((c-o).abs()>(c.shift(1)-o.shift(1)).abs())
    if pid == 'inside_break_bear':
        return (h.shift(1)<h.shift(2)) & (l.shift(1)>l.shift(2)) & (c<l.shift(2))
    if pid == 'macd_hist_bull':
        x=f['hist']; return (x.shift(2)<x.shift(1)) & (x.shift(1)<x) & (x<0)
    if pid == 'rsi_reentry_bull':
        return (f.rsi.shift(1)<=30) & (f.rsi>30)
    if pid == 'tweezer_bull':
        return (c.shift(2)<c.shift(7)) & (c.shift(1)<o.shift(1)) & (c>o) & ((l-l.shift(1)).abs()<=.15*f.atr.shift(1))
    if pid == 'double_bottom':
        return ((f.lo_p2-f.lo_p1).abs()<=.5*f.lo_atr2) & (f.lo_neck-pd.concat([f.lo_p1,f.lo_p2],axis=1).max(axis=1)>=f.lo_atr2) & (c>f.lo_neck) & ((c.shift(1)<=f.lo_neck)|(f.lo_event==1))
    raise ValueError(pid)


def metrics(f, pid):
    r=f.iloc[-1]; prev=f.iloc[-2]; prior=f.iloc[-21:-1]
    atr=float(prev.atr)
    m={
      'prior5_return_pct':100*(prev.c/f.c.iloc[-7]-1),
      'close_location_in_range':(r.c-r.l)/(r.h-r.l),
      'body_to_prior_atr':r.body/atr,
      'volume_to_prior20':r.v/r.vbase,
      'volume_to_previous':r.v/prev.v,
      'close_to_ma20_atr':(r.c-r.ma20)/atr,
      'ma20_5day_change_atr':(r.ma20-f.ma20.iloc[-6])/atr,
      'close_to_prior20_low_atr':(r.c-prior.l.min())/atr,
      'close_to_prior20_high_atr':(r.c-prior.h.max())/atr,
    }
    if pid == 'engulf_bull':
        m.update(body_expansion=r.body/prev.body, cover_upper_margin_atr=(r.c-prev.o)/atr, cover_lower_margin_atr=(prev.c-r.o)/atr)
    elif pid == 'inside_break_bear':
        mother=f.iloc[-3]
        m.update(inside_range_to_mother=prev['range']/mother['range'], break_depth_atr=(mother.l-r.c)/atr, mother_low=mother.l, mother_high=mother.h)
    elif pid == 'macd_hist_bull':
        m.update(hist_three=f['hist'].iloc[-3:].tolist(), hist_abs_contraction_pct=100*(1-abs(r['hist'])/abs(f['hist'].iloc[-3])), dif=r.dif, dea=r.dea)
    elif pid == 'rsi_reentry_bull':
        m.update(rsi_previous=prev.rsi, rsi_current=r.rsi, rsi_distance_above30=r.rsi-30)
    elif pid == 'tweezer_bull':
        gap=abs(r.l-prev.l); threshold=.15*atr
        m.update(extreme_gap=gap, extreme_tolerance=threshold, remaining_tolerance=threshold-gap, tolerance_consumed=gap/threshold)
    elif pid == 'double_bottom':
        m.update(pivot1=r.lo_p1, pivot2=r.lo_p2, neckline=r.lo_neck, pivot_separation=r.lo_sep, neckline_break_atr=(r.c-r.lo_neck)/atr, pivot_gap_to_atr=abs(r.lo_p1-r.lo_p2)/r.lo_atr2)
    return clean(m)


LABELS={
 'prior5_return_pct':'信号前一日相对五个交易日前收盘变动(%)',
 'close_location_in_range':'信号日收盘在当日振幅中的位置(0=最低、1=最高)',
 'body_to_prior_atr':'信号日实体/前日ATR14',
 'volume_to_prior20':'信号日成交量/此前20日均量',
 'volume_to_previous':'信号日成交量/前一日成交量',
 'close_to_ma20_atr':'信号日(收盘-MA20)/前日ATR14',
 'ma20_5day_change_atr':'MA20五日变化/前日ATR14',
 'close_to_prior20_low_atr':'(信号日收盘-此前20日最低价)/前日ATR14',
 'close_to_prior20_high_atr':'(信号日收盘-此前20日最高价)/前日ATR14',
 'body_expansion':'末根实体/前根实体',
 'cover_upper_margin_atr':'末根收盘超过前阴开盘的距离/前日ATR14',
 'cover_lower_margin_atr':'前阴收盘超过末根开盘的距离/前日ATR14',
 'inside_range_to_mother':'内包日振幅/母线振幅',
 'break_depth_atr':'收盘跌破母线低点的距离/前日ATR14',
 'mother_low':'母线最低价','mother_high':'母线最高价',
 'hist_three':'连续三根MACD柱(DIF-DEA)',
 'hist_abs_contraction_pct':'三根期间MACD负柱绝对值缩短(%)',
 'dif':'当前DIF','dea':'当前DEA',
 'rsi_previous':'前日RSI14','rsi_current':'当前RSI14','rsi_distance_above30':'当前RSI超过30的点数',
 'extreme_gap':'两低点绝对差','extreme_tolerance':'本地规则允许的低点差(0.15×前日ATR14)',
 'remaining_tolerance':'距离容差上限的剩余空间','tolerance_consumed':'低点差/允许低点差',
 'pivot1':'第一低点','pivot2':'第二低点','neckline':'颈线',
 'pivot_separation':'两低点间隔(交易日)','neckline_break_atr':'颈线收盘突破距离/前日ATR14',
 'pivot_gap_to_atr':'两低点差/第二低点ATR14',
}


def table(f, count, pid):
    cols=['o','h','l','c','v','ma20']
    if pid.startswith('macd'):cols += ['dif','dea','hist']
    if pid.startswith('rsi'):cols += ['rsi']
    s=f.iloc[-count:][cols].copy();s['v']/=1e4
    s.insert(0,'date',s.index.strftime('%m-%d'))
    return s.to_csv(index=False, float_format='%.6f')


def prepare_experiment():
    OUT.mkdir(parents=True,exist_ok=True)
    raw=pd.DataFrame([json.loads(x) for x in gzip.open(DATA,'rt')]);raw.trade_date=pd.to_datetime(raw.trade_date)
    frames={}; audit=[]; raw_frames={}
    pids=['engulf_bull','inside_break_bear','macd_hist_bull','rsi_reentry_bull','tweezer_bull','double_bottom']
    for code,b in raw.groupby('stk_code',sort=True):
        b=b.set_index('trade_date').sort_index();raw_frames[code]=b.copy()
        b=b.drop(columns=['open','high','low','close']).rename(columns={f'adj{k}':k for k in ['open','high','low','close']})
        f=prepare(b);frames[code]=f
        for pid in pids:
            got=detect(PATTERNS[pid],f).tail(250)
            expected=reference(pid,f).tail(250)
            known=got.notna(); diff=int((got[known].astype(bool)!=expected[known]).sum())
            audit.append(dict(code=code,pattern=pid,compared=int(known.sum()),unknown=int((~known).sum()),matched=int(got.fillna(False).sum()),errors=diff))
    dump(OUT/'numeric_audit.json',audit)
    library=[json.loads(s) for s in (DATA.parent/'cases.jsonl').read_text().splitlines()]
    requests=[]; cases=[]
    def normalized_at(code, date):
        # Keep adjusted history, put the price axis on the signal date's actual close.
        # This is one constant scaling of the whole as-of series, with no later bars.
        source=raw_frames[code].loc[:date]
        scale=source.close.iloc[-1]/source.adjclose.iloc[-1]
        bars=source.drop(columns=['open','high','low','close']).rename(columns={f'adj{k}':k for k in ['open','high','low','close']})
        bars[['open','high','low','close']]*=scale
        return prepare(bars)
    # Case selection is fixed before any model responses: four definitions, one positive
    # and one archived near-miss each. The model never receives these labels/results.
    for pid in pids[:4]:
        for suffix in ['hit_1','near_4']:
            old=next(x for x in library if x['case_id']==pid+'_'+suffix)
            f=normalized_at(old['stock_code'],old['signal_date'])
            cases.append((old['case_id'],pid,old['stock_code'],f,'qualification'))
    for pid in ['engulf_bull','inside_break_bear','rsi_reentry_bull','double_bottom']:
        old=next(x for x in library if x['case_id']==pid+'_hit_1')
        cases.append((pid+'_interpret',pid,old['stock_code'],normalized_at(old['stock_code'],old['signal_date']),'interpretation'))
    for stock,pid,code in [('ningde','macd_hist_bull','300750.SZ'),('maotai','tweezer_bull','600519.SH')]:
        f=pd.read_json(ROOT/f'docs/development_tasks/evidence/kline_split_20260929/final/{stock}/features.json')
        f.index=pd.to_datetime(f.pop('trade_date'))
        cases.append((pid+'_interpret',pid,code,f,'interpretation'))
    summary=[]
    for cid,pid,code,f,stage in cases:
        folder=OUT/cid;folder.mkdir(exist_ok=True)
        p=PATTERNS[pid];at=len(f)-1;start,end=subject_interval(p,f,at)
        facts=candidate_facts(p,f,at,code,cid)
        match=bool(detect(p,f,at));expected=bool(reference(pid,f).iloc[-1]);assert match==expected
        if stage=='interpretation':assert match
        times=[]
        for _ in range(30):
            t=time.perf_counter();detect(p,f,at);times.append((time.perf_counter()-t)*1000)
        m=metrics(f,pid)
        image=folder/'chart.png'
        meta=render(p,f,at,code,image,display_bars=20 if stage=='interpretation' else 10,target_start=start,target_end=end,annotate_values=True,compact=True)
        dump(folder/'chart_metadata.json',meta)
        dump(folder/'facts.json',{'pattern':p,'facts':facts,'metrics':m,'numeric_match':match,'reference_match':expected,'numeric_ms':{'mean':float(np.mean(times)),'median':float(np.median(times))} if match else None})
        count=max(20 if stage=='interpretation' else 10,at-start+3)
        csv=table(f,count,pid);(folder/'data.csv').write_text(csv)
        base=f'股票{code}，截至{f.index[-1]:%Y-%m-%d}。待分析形态：{p["name"]}。主体区间{f.index[start]:%Y-%m-%d}—{f.index[end]:%Y-%m-%d}。\n定义：{p["description"]}\n同源数据，O/H/L/C为开高低收，复权价统一缩放至截止日实际收盘价，v单位万股，指标已经充分预热；MACD柱=DIF-DEA。图如有提供，红阳绿阴。\n'+csv
        if stage=='qualification':
            # No machine match labels or operands/results: the two modalities get
            # exactly the same text. Only the attached deterministic chart differs.
            prompt='你是擅长研究A股K线的分析师。按所给定义判断指定区间是否符合该形态，只返回JSON：{"match":true或false,"assessment":"简短理由"}。判断是否发生与后续上涨下跌强弱分开。\n'+base
        else:
            detail='\n'.join(LABELS[k]+'：'+str([round(v,6) for v in value] if isinstance(value,list) else round(value,6)) for k,value in m.items())
            prompt='你是擅长研究A股K线的分析师。下列形态已满足给定数值定义。请用180—260字解释其在当时的意义，结合位置、强弱、支持与反证，给出后续观察条件。只使用截止日已知情况；区分形态已发生与未来趋势能否延续。以定性分析为主，涉及精确值时以已给数据为准。\n'+base+'\n程序计算的程度与背景事实：\n'+detail
        (folder/'prompt.txt').write_text(prompt)
        for arm in ['text','text_image']:
            requests.append({'case':cid,'stage':stage,'arm':arm,'prompt':str((folder/'prompt.txt').relative_to(OUT)),'images':[str(image.relative_to(OUT))] if arm=='text_image' else [],'expected':match if stage=='qualification' else None})
        summary.append({'id':cid,'pattern':pid,'code':code,'date':str(f.index[-1].date()),'stage':stage,'numeric_match':match,'metrics':m,'display_bars':meta['display_bars']})
    # Measured sensitivity is a property of the rule, not a request for an image vote.
    sample=next(x for x in cases if x[1]=='tweezer_bull')[3]
    sensitivity=[]
    for delta in [-.01,0,.01]:
        modified=sample.copy();modified.iloc[-1,modified.columns.get_loc('l')]+=delta
        gap=abs(modified.l.iloc[-1]-modified.l.iloc[-2]);atr=modified.atr.iloc[-2]
        sensitivity.append({'last_low_delta':delta,'gap':gap,'match_at_0.10_atr':gap<=.10*atr,'match_at_0.15_atr':gap<=.15*atr,'match_at_0.20_atr':gap<=.20*atr})
    # Identical candle structure and history; only current volume differs.
    engulf=next(x for x in cases if x[0]=='engulf_bull_interpret')[3]
    volume_examples=[]
    for multiple in [.6,1.8]:
        x=engulf.copy();x.iloc[-1,x.columns.get_loc('v')]=multiple*x.vbase.iloc[-1]
        volume_examples.append({'synthetic':True,'volume_to_prior20':multiple,'same_price_structure_match':bool(detect(PATTERNS['engulf_bull'],x,len(x)-1)),'metrics':metrics(x,'engulf_bull')})
    dump(OUT/'sensitivity.json',{'real_tweezer_boundary':sensitivity,'controlled_volume_only':volume_examples})
    dump(OUT/'cases.json',summary);dump(OUT/'requests.json',requests)
    paths=[Path(__file__),ROOT/'src/experiments/kline_patterns/engine.py',DEFINITIONS,ROOT/'src/experiments/kline_patterns/render.py',ROOT/'src/experiments/kline_patterns/analysis.py',ROOT/'src/services/technical_indicator_calculator.py',DATA]
    dump(OUT/'manifest.json',{'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'python':platform.python_version(),'model':MODEL,'temperature':0,'max_tokens':16384,'enable_think':False,'file_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},'workload':'24 frozen stocks, six definitions, last 250 rows each; 8 paired qualification cases; 6 paired interpretation cases. No production pipeline changes.','evaluation':'Independent array reference checks formula implementation, not trading profitability or universal human definitions. Interpretation assessed manually against stored OHLCV/metrics; same text in both arms.'})
    print(json.dumps({'audit_rows':sum(r['compared'] for r in audit),'numeric_errors':sum(r['errors'] for r in audit),'requests':len(requests)},ensure_ascii=False),flush=True)


def call_experiment():
    from dotenv import dotenv_values
    from openai import OpenAI
    from src.utils.ai_service import _create_llm_completion, extract_first_json
    cfg=dotenv_values(ROOT/'.env_tmp')
    all_results=[]
    with OpenAI(base_url=cfg['BASE_URL'],api_key=cfg['LLM_KEY'],timeout=90,max_retries=0) as client:
        for spec in json.loads((OUT/'requests.json').read_text()):
            target=OUT/spec['case']/(spec['arm']+'.json')
            if target.exists():
                all_results.append(json.loads(target.read_text()));continue
            prompt=(OUT/spec['prompt']).read_text();content=[{'type':'text','text':prompt}]
            image_hashes=[]
            for name in spec['images']:
                data=(OUT/name).read_bytes();image_hashes.append({'path':name,'sha256':hashlib.sha256(data).hexdigest()})
                content.append({'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(data).decode(),'detail':'high'}})
            started=time.perf_counter();item={**spec,'image_hashes':image_hashes,'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest()}
            try:
                r=_create_llm_completion([{'role':'user','content':content}],model=MODEL,temperature=0,enable_think=False,max_tokens=16384,response_format={'type':'json_object'} if spec['stage']=='qualification' else None,client_instance=client)
                item.update(response=r.choices[0].message.content,finish_reason=r.choices[0].finish_reason,returned_model=r.model,usage=r.usage.model_dump())
                if spec['stage']=='qualification':
                    item['parsed']=extract_first_json(item['response'],log_errors=False)
                    item['correct']=(item['parsed'] or {}).get('match') is spec['expected']
            except Exception as e:
                item.update(error_class=type(e).__name__,http_status=getattr(e,'status_code',None))
            item['seconds']=time.perf_counter()-started
            dump(target,item);all_results.append(item);dump(OUT/'results.json',all_results)
            print(json.dumps({'case':spec['case'],'arm':spec['arm'],'seconds':round(item['seconds'],2),'tokens':(item.get('usage') or {}).get('total_tokens'),'correct':item.get('correct'),'error':item.get('error_class')},ensure_ascii=False),flush=True)
    dump(OUT/'results.json',all_results)
    usage={}
    for stage in ['qualification','interpretation']:
        for arm in ['text','text_image']:
            rows=[r for r in all_results if r['stage']==stage and r['arm']==arm]
            usage[stage+'_'+arm]={'calls':len(rows),'tokens':sum(r.get('usage',{}).get('total_tokens',0) for r in rows),'seconds':sum(r['seconds'] for r in rows),'correct':sum(r.get('correct',False) for r in rows) if stage=='qualification' else None}
    dump(OUT/'usage.json',usage)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--prepare',action='store_true');parser.add_argument('--call',action='store_true');args=parser.parse_args()
    if args.prepare:prepare_experiment()
    if args.call:call_experiment()
