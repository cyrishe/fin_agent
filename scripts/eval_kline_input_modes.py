"""Paired input-channel experiment; fixed historical selection, real new model calls.

One canonical feature file supplies plots and calculated facts. Image mode gives
the interpretation calls only a plain chart; data mode gives only calculated
facts. Both use the same text synthesis and exact candidate IDs. Selection is
replayed, not billed or represented as a new model call.
"""
import argparse
import base64
from datetime import datetime, timezone
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
from dotenv import dotenv_values, load_dotenv
from openai import OpenAI

from src.experiments.kline_patterns.analysis import (
    PATTERNS, analyze, candidate_facts, context_brief, usage_totals,
)
from src.experiments.kline_patterns.run import dump
from src.experiments.kline_patterns.strength import profile, brief
from src.experiments.kline_patterns.visual_review import evidence_brief, pattern_brief, target_brief
from src.utils.ai_service import _create_llm_completion


def data_prompt(stage, f, selected):
    if stage == 'vlm_basic':
        return ('你是一位擅长研究A股K线的分析师。请根据以下同源计算事实，分析近期走势、量价配合、最新变化和后续观察条件。约200字。\n\n'
                + context_brief(f))
    candidate = selected[int(stage.split('_')[1])-1]
    p = PATTERNS[candidate['pattern_id']]
    facts = candidate['facts']
    strength = profile(p, candidate, f)
    return ('你是一位擅长研究A股K线的分析师。请根据形态定义及同源计算事实，分析结构的力度、作用、后续演变和观察条件。约200字。\n\n'
            + pattern_brief(p)+'\n'+target_brief(facts)+'\n'
            + evidence_brief(facts, p)+'\n程度：'+brief(strength)
            +'\n截至本次分析的演变：'+strength['evolution'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--mode', choices=['image', 'data'], required=True)
    args = parser.parse_args()
    baseline, out = ROOT/args.baseline, ROOT/args.output
    out.mkdir(parents=True, exist_ok=False)
    load_dotenv(ROOT/'.env', override=False)
    cfg = dotenv_values(ROOT/'.env_tmp')
    model = 'BL/deepseek-v4.1-flash'
    sources = list((ROOT/'src/experiments/kline_patterns').glob('*.py'))
    for name in ['kline-analysis', 'kline-basic-reading']:
        sources += list((ROOT/'src/skills/finance-business/skills'/name).rglob('*.md'))
    sources.append(Path(__file__))
    hashes = {}
    for source in sources:
        rel = source.relative_to(ROOT)
        hashes[str(rel)] = hashlib.sha256(source.read_bytes()).hexdigest()
        target = out/'source_snapshot'/rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    manifest = {'started_at':datetime.now(timezone.utc).isoformat(), 'mode':args.mode,
                'model':model, 'endpoint':cfg['BASE_URL'], 'credential_source':'.env_tmp:LLM_KEY',
                'commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
                'source_sha256':hashes, 'baseline':str(baseline.resolve()),
                'temperature':0, 'enable_think':False, 'max_tokens':16384,
                'selection':'Replayed exact baseline IDs; no new selection request or selection token charge.',
                'scope':'Standalone business workflow, not FinancialQa/HTTP/UI.', 'stocks':{}}
    dump(out/'manifest.json', manifest)
    summary = []
    with OpenAI(base_url=cfg['BASE_URL'], api_key=cfg['LLM_KEY'], timeout=180, max_retries=0) as client:
        for stock in ('pingan','maotai','ningde'):
            folder = out/stock;folder.mkdir()
            saved = json.loads((baseline/stock/'result.json').read_text())
            source = baseline/stock/'features.json'
            shutil.copyfile(source, folder/'features.json')
            manifest['stocks'][stock] = {'canonical_features_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                                         'selected_ids':saved['selected_ids'], 'numeric_only_ids':saved['numeric_only_ids']}
            dump(out/'manifest.json', manifest)
            f = pd.DataFrame(json.loads(source.read_text()))
            f['trade_date'] = pd.to_datetime(f.trade_date)
            f = f.set_index('trade_date')
            lookup = {c['id']:c for c in saved['candidates']}
            selected = []
            for cid in saved['selected_ids']:
                c = dict(lookup[cid]);p = PATTERNS[c['pattern_id']]
                c['facts'] = candidate_facts(p, f, c['at'], saved['code'], cid)
                selected.append(c)
            calls = []

            def call(stage, prompt, images, structured):
                if stage == 'main_selection':
                    replay = {'parsed':{'selected_ids':saved['selected_ids'], 'numeric_only_ids':saved['numeric_only_ids']},
                              'finish_reason':'stop', 'source':str((baseline/stock/'02_main_selection.json').resolve())}
                    dump(folder/'selection_replay.json', replay)
                    return replay
                if args.mode == 'data' and stage.startswith('vlm_'):
                    prompt, images = data_prompt(stage, f, selected), []
                content = [{'type':'text','text':prompt}]
                records = []
                for image in images:
                    data = image.read_bytes()
                    content.append({'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(data).decode(), 'detail':'high'}})
                    records.append({'path':str(image.relative_to(folder)), 'sha256':hashlib.sha256(data).hexdigest()})
                rec = {'stage':stage, 'requested_model':model, 'prompt':prompt, 'images':records,
                       'canonical_features_sha256':manifest['stocks'][stock]['canonical_features_sha256']}
                index = len(calls)+1
                (folder/f'{index:02d}_{stage}_prompt.txt').write_text(prompt)
                start = time.perf_counter()
                try:
                    response = _create_llm_completion([{'role':'user','content':content}], model=model,
                        max_tokens=16384, temperature=0, enable_think=False, client_instance=client)
                    rec.update(response=response.choices[0].message.content, finish_reason=response.choices[0].finish_reason,
                               returned_model=response.model, usage=response.usage.model_dump() if response.usage else None)
                except Exception as exc:
                    rec.update(error_class=type(exc).__name__, http_status=getattr(exc,'status_code',None))
                rec['seconds'] = time.perf_counter()-start
                calls.append(rec)
                dump(folder/f'{index:02d}_{stage}.json', rec)
                dump(folder/'usage.json', usage_totals(calls))
                print(json.dumps({'stock':stock, 'mode':args.mode, 'stage':stage, 'tokens':(rec.get('usage') or {}).get('total_tokens'),
                                  'seconds':round(rec['seconds'],2), 'finish':rec.get('finish_reason'), 'error':rec.get('error_class')},ensure_ascii=False),flush=True)
                return rec

            result = analyze(f, saved['code'], saved['question'], folder, call)
            assert result['selected_ids'] == saved['selected_ids']
            assert result['numeric_only_ids'] == saved['numeric_only_ids']
            result['usage'] = usage_totals(calls)
            dump(folder/'result.json', result)
            summary.append({'stock':stock, 'selected_ids':result['selected_ids'],
                            'seconds':result['seconds'], 'usage':result['usage'],
                            'complete':all(c.get('finish_reason')=='stop' for c in calls)})
            dump(out/'summary.json', summary)
    if not all(s['complete'] for s in summary):raise SystemExit(1)


if __name__ == '__main__':
    main()
