"""Business prompt experiment: visual qualification, then conditional interpretation."""
import gzip
import hashlib
import json
import math
import subprocess
import time
from pathlib import Path

import pandas as pd
from .catalog import CATALOG
from .engine import prepare
from .run import clean

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/research/kline_pattern_library_20260928'
MODEL = 'BL/deepseek-v4.1-flash'

PROMPTS = ROOT / 'src/skills/finance-business/skills/kline-analysis/references'
QUALIFY = (PROMPTS / 'visual-qualification.md').read_text()
INTERPRET = (PROMPTS / 'visual-interpretation.md').read_text()
ANALYZE = (PROMPTS / 'pattern-analysis.md').read_text()


def interpret(pattern, facts, images, call, strength):
    """One chart through the analysis date; facts/strength stay with text synthesis."""
    return {'interpretation': call(ANALYZE.strip()+'\n\n'+pattern_brief(pattern), images, False)}

def pattern_brief(pattern):
    # Executable formulae stay in the numeric engine and trace, not in VLM prose.
    return f"可能的形态：{pattern['name']}（{pattern['revision']}）\n{pattern['description']}"

def target_brief(facts):
    target = facts.get('target') or {}
    lines = [f"股票：{target.get('symbol') or facts.get('stock_code') or '见图'}；实例：{target.get('instance_id', '见图中框')}。"]
    if target.get('start_date') and target.get('end_date'):
        lines.append(f"主体区间：{target['start_date']}至{target['end_date']}。")
    if date := target.get('confirmation_date') or facts.get('signal_date'):
        lines.append(f"判断时点：{date}。")
    anchors = target.get('anchors') or {}
    if anchors:lines.append('图中标记：' + '；'.join(f"{label}={v['date']}" for label,v in anchors.items() if v.get('date')) + '。')
    return '\n'.join(lines)

def evidence_brief(facts, pattern=None, *, include_conditions=True):
    lines = []
    for item in facts.get('operands', []) if include_conditions else []:
        result = item.get('result')
        status = '满足' if result is True else '不满足' if result is False else '未提供确定结果'
        lines.append(f"{item['condition']}：{status}。")
    # Present same-source comparisons as facts, without asking the model to redo them.
    def finite(row, *keys):
        return all(isinstance(row.get(k), (int,float)) and math.isfinite(row[k]) for k in keys)
    def relation(a,b):return '高于' if a>b else '低于' if a<b else '等于'
    expressions = ' '.join(c['expr'] for c in pattern['conditions']) if pattern else ''
    days = facts.get('recent_days', [])
    rows = days[-2:]
    if pattern and (target := facts.get('target')):
        rows = [r for r in days if target['start_date'] <= r['date'] <= target['end_date']]
        if len(rows)>5: rows = [rows[0], rows[-1]]
        if days and days[-1] not in rows: rows = rows + [days[-1]]
    for row in rows:
        parts=[]
        if pattern and finite(row,'o','h','l','c'):
            parts.append(f"开{row['o']:.4f}、高{row['h']:.4f}、低{row['l']:.4f}、收{row['c']:.4f}")
        if finite(row,'o','c'):parts.append('阳线' if row['c']>row['o'] else '阴线' if row['c']<row['o'] else '开收相同的十字线')
        if pattern and finite(row, 'upper', 'lower', 'body') and (pattern['family'] == '单根影线' or pattern['id'].startswith('tweezer_')):
            parts.append(f"上影{row['upper']:.4f}、下影{row['lower']:.4f}、实体{row['body']:.4f}")
            parts.append('上影'+relation(row['upper'],row['lower'])+'下影')
        for key in ['ma5','ma10','ma20','ma30','ma60']:
            if pattern and key+'[' not in expressions: continue
            if finite(row,'c',key):parts.append(f"收盘{relation(row['c'],row[key])}{key.upper()}")
        if (pattern is None or pattern['family']=='MACD') and finite(row,'dif','dea'):
            parts.append('DIF'+relation(row['dif'],row['dea'])+'DEA')
            parts.append('DIF'+relation(row['dif'],0)+'零轴，DEA'+relation(row['dea'],0)+'零轴')
        if (pattern is None or pattern['family']=='MACD') and finite(row,'hist'):parts.append('MACD柱为正' if row['hist']>0 else 'MACD柱为负' if row['hist']<0 else 'MACD柱为零')
        if pattern and pattern['family']=='RSI' and finite(row,'rsi'):parts.append(f"RSI14={row['rsi']:.2f}")
        if parts:lines.append(str(row.get('date') or row.get('trade_date') or '数据日')+'：'+'；'.join(parts)+'。')
    date = facts.get('signal_date') or (days[-1].get('date') if days else None)
    signal = date if date else '信号日'
    ratio=facts.get('volume_ratio_to_prior20')
    if isinstance(ratio,(int,float)) and math.isfinite(ratio):lines.append(f'{signal}成交量为此前20日均量的{ratio:.3f}倍，'+relation(ratio,1)+'此前20日均量。')
    days=facts.get('recent_days', [])
    if len(days)>=2:
        previous,current=days[-2:]
        if finite(previous,'c') and finite(current,'c'):
            lines.append(signal+'收盘'+relation(current['c'],previous['c'])+'前一交易日收盘；阴阳线按各日开收判断。')
        if finite(previous,'v') and finite(current,'v') and previous['v']>0:
            lines.append(f"{signal}成交量为前一交易日的{current['v']/previous['v']:.3f}倍，"+relation(current['v'],previous['v'])+'前一日成交量。')
        if (pattern is None or pattern['family']=='MACD') and finite(previous,'hist') and finite(current,'hist'):
            lines.append(signal+'MACD柱绝对值'+relation(abs(current['hist']),abs(previous['hist']))+'前一交易日；柱体正负见当日事实。')
            if current['hist']*previous['hist'] > 0:
                color = '负柱' if current['hist'] < 0 else '正柱'
                change = '缩短' if abs(current['hist']) < abs(previous['hist']) else '扩大' if abs(current['hist']) > abs(previous['hist']) else '持平'
                lines.append(f'{signal}相对前一交易日：MACD{color}{change}。')
    if facts.get('context_notes'):lines.append(facts['context_notes'])
    return '\n'.join(lines) if lines else '本次未提供程序计算结果，结合图片判断；看不清的关系保留不确定。'

def review(pattern, facts, images, call):
    """Only affirmative qualification can trigger the interpretation request."""
    brief = pattern_brief(pattern)
    # Local calculated relations are evidence, not a claim of predictive strength.
    # Retain these precise relations when visual differences are sub-pixel.
    observations = evidence_brief(facts, pattern, include_conditions=True)
    context = target_brief(facts) + '\n' + brief + '\n同源观测事实：\n' + observations
    first_prompt = QUALIFY + '\n' + context
    first = call(first_prompt, images, True)
    answer = first.get('parsed') or {}
    result = {'qualification': first, 'interpretation': None}
    if answer.get('match') is True:
        # Transfer the decision, not the previous model's potentially wrong numbers.
        second_prompt = INTERPRET + '\n' + context
        result['interpretation'] = call(second_prompt, images, False)
    return result

def main():
    from src.utils.ai_service import build_multimodal_user_message, _create_llm_completion, extract_first_json
    from dotenv import dotenv_values
    from openai import OpenAI
    config = dotenv_values(ROOT / '.env_tmp')
    llm_client = OpenAI(base_url=config['BASE_URL'], api_key=config['LLM_KEY'], timeout=50, max_retries=0)
    from .render import render
    raw = pd.DataFrame([json.loads(s) for s in gzip.open(OUT/'daily_inputs.jsonl.gz', 'rt')])
    raw['trade_date'] = pd.to_datetime(raw.trade_date)
    cases = [json.loads(s) for s in (OUT/'cases.jsonl').read_text().splitlines()]
    selected = ['engulf_bull_hit_1', 'engulf_bull_near_4', 'macd_cross_bull_hit_1', 'rsi_reentry_bull_hit_1']
    # RSI identifier is taken from the catalogue rather than fabricated if renamed.
    selected = selected[:3] + [next(c['case_id'] for c in cases if c['pattern_id'].startswith('rsi_') and c['matched'])]
    patterns = {p['id']:p for p in CATALOG}
    report = {'model':MODEL, 'endpoint':str(llm_client.base_url), 'credential_source':'.env_tmp:LLM_KEY', 'head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(), 'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'input_sha256':hashlib.sha256((OUT/'daily_inputs.jsonl.gz').read_bytes()).hexdigest(), 'cases':[]}
    def call(prompt, images, structured):
        started = time.perf_counter()
        item = {'prompt':prompt, 'images':[str(p.relative_to(OUT)) for p in images]}
        try:
            r = _create_llm_completion([build_multimodal_user_message(text=prompt,image_paths=images,detail='high')], model=MODEL, max_tokens=1800,temperature=0,enable_think=False, response_format={'type':'json_object'} if structured else None, client_instance=llm_client.with_options(timeout=50,max_retries=0))
            item.update(response=r.choices[0].message.content, returned_model=r.model, usage=r.usage.model_dump() if r.usage else None)
            if structured:item['parsed'] = extract_first_json(item['response'],log_errors=False)
        except Exception as e:
            item.update(error_class=type(e).__name__,http_status=getattr(e,'status_code',None))
        item['seconds'] = time.perf_counter()-started
        return item
    for cid in selected:
        started = time.perf_counter()
        c = next(c for c in cases if c['case_id']==cid); p = patterns[c['pattern_id']]
        b = raw[(raw.stk_code==c['stock_code']) & (raw.trade_date<=pd.Timestamp(c['signal_date']))].set_index('trade_date').drop(columns=['open','high','low','close']).rename(columns={'adjopen':'open','adjhigh':'high','adjlow':'low','adjclose':'close'})
        f = prepare(b)
        closeup = OUT/'images'/f'{cid}_visual_detail.png'
        render(p,f,len(f)-1,c['stock_code'],closeup,display_bars=max(28,p['focus_bars']+15))
        keys = ['o','h','l','c','v','vbase','ma5','ma20','ma60','dif','dea','hist','rsi']
        snapshot = f[keys].tail(8).copy(); snapshot.insert(0,'date',snapshot.index.strftime('%Y-%m-%d'))
        facts = clean({'stock_code':c['stock_code'],'signal_date':c['signal_date'],'operands':[{'condition':e['label'],'values':e['operands'],'result':e['result']} for e in c['evidence']], 'recent_days':snapshot.to_dict('records'),'volume_ratio_to_prior20':float(f.v.iloc[-1]/f.vbase.iloc[-1])})
        facts['computed_relationships'] = [f"{idx:%Y-%m-%d}：" + '；'.join([f"收盘{row.c:.4f}{'高于' if row.c > row[k] else '低于' if row.c < row[k] else '等于'}{k.upper()} {row[k]:.4f}" for k in ['ma5','ma20','ma60']]) + f"；DIF {row.dif:.4f}{'高于' if row.dif > row.dea else '低于' if row.dif < row.dea else '等于'}DEA {row.dea:.4f}" for idx,row in f.tail(8).iterrows()]
        result = review(p,facts,[OUT/c['image'],closeup],call)
        result.update(case_id=cid,formula_match=c['matched'],facts=facts,total_seconds=time.perf_counter()-started)
        report['cases'].append(result)
        (OUT/'visual_review_results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
        print(json.dumps({'case':cid,'formula_match':c['matched'],'visual_match':(result['qualification'].get('parsed') or {}).get('match'),'interpretation_called':result['interpretation'] is not None,'seconds':result['total_seconds']},ensure_ascii=False),flush=True)

if __name__ == '__main__':main()
