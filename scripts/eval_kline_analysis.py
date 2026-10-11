"""Real main + VLM calls in the standalone K-line business workflow.

No uploads/fixtures substitute for model responses. Public daily market data is
fetched read-only or replayed. This does not exercise the FinancialQa/HTTP route.
"""
import argparse
import base64
import contextlib
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
from dotenv import dotenv_values, load_dotenv
from openai import OpenAI

from src.experiments.kline_patterns.analysis import analyze, usage_totals
from src.experiments.kline_patterns.engine import prepare
from src.experiments.kline_patterns.run import dump
from src.utils.ai_service import _create_llm_completion, extract_first_json

STOCKS = {'pingan': ('000001.SZ', '平安银行'), 'maotai': ('600519.SH', '贵州茅台'),
          'ningde': ('300750.SZ', '宁德时代')}


def fetch(codes, asof):
    from src.experiments.kline_patterns.fetch import ReadOnlyDB
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        db = ReadOnlyDB(database='kingdomai')
    rows = []
    try:
        with db.conn.cursor() as cur:
            cur.execute('SET SESSION MAX_EXECUTION_TIME=10000')
            cur.execute('START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY')
            for code in codes:
                cur.execute('SELECT trade_date,stk_code,open,high,low,close,adjopen,adjhigh,adjlow,adjclose,volume,turn_ratio FROM kcrp_stock_price WHERE stk_code=%s AND trade_date>=%s AND trade_date<=%s ORDER BY trade_date LIMIT 1000', (code, '2024-01-01', asof))
                for row in cur.fetchall():
                    rows.append({k: str(v) if k in ('trade_date', 'stk_code') else None if v is None else float(v) for k, v in row.items()})
    finally:
        db.conn.rollback()
        db.close_db()
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--inputs', help='Frozen daily_inputs.jsonl.gz; omitted = read-only DB fetch')
    parser.add_argument('--asof', default='2026-09-28')
    parser.add_argument('--stocks', nargs='+', choices=list(STOCKS), default=list(STOCKS))
    parser.add_argument('--window', type=int, default=10)
    parser.add_argument('--question', help='Optional real user question, shared across the requested stocks')
    parser.add_argument('--vlm-model', default='BL/deepseek-v4.1-flash',
                        help='Basic visual reading and per-instance visual model; selection and text synthesis stay unchanged')
    args = parser.parse_args()
    out = ROOT/args.output
    out.mkdir(parents=True, exist_ok=False)
    load_dotenv(ROOT/'.env', override=False)
    cfg = dotenv_values(ROOT/'.env_tmp')
    model = 'BL/deepseek-v4.1-flash'
    if args.inputs:
        rows = [json.loads(s) for s in gzip.open(ROOT/args.inputs, 'rt')]
    else:
        rows = fetch([STOCKS[s][0] for s in args.stocks], args.asof)
    serialized = ''.join(json.dumps(row, ensure_ascii=False)+'\n' for row in rows).encode()
    with gzip.GzipFile(filename=str(out/'daily_inputs.jsonl.gz'), mode='wb', mtime=0) as handle:
        handle.write(serialized)
    files = list((ROOT/'src/experiments/kline_patterns').glob('*.py')) + list((ROOT/'src/skills/finance-business/skills/kline-analysis').rglob('*.md')) + list((ROOT/'src/skills/finance-business/skills/kline-basic-reading').rglob('*.md')) + [Path(__file__), ROOT/'src/services/technical_indicator_calculator.py']
    manifest = {'entry': 'Standalone business workflow: scan, independent basic reading, main selection, isolated VLM loop, text-only main synthesis. Not FinancialQa/HTTP/UI dispatch.',
                'started_at': datetime.now(timezone.utc).isoformat(), 'asof_requested': args.asof,
                'stocks': args.stocks, 'signal_window_bars': args.window, 'attachments': [],
                'model': model, 'vlm_model': args.vlm_model,
                'endpoint': cfg['BASE_URL'], 'credential_source': '.env_tmp:LLM_KEY',
                'input_source': args.inputs or 'read-only kingdomai.kcrp_stock_price, 2024-01-01 through asof, <=1000 per stock',
                'data_sha256': hashlib.sha256(serialized).hexdigest(),
                'price_basis': 'Source hfq OHLC, constant scaled by latest raw close / latest adjclose, ending at asof. Volume shares unchanged.',
                'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                'file_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
                'python': platform.python_version(), 'pandas': pd.__version__,
                'max_tokens_each_call': 16384, 'temperature': 0, 'enable_think': False, 'max_retries': 0,
                'usage_scope': 'Every request made by this runner (main + VLM). Program data fetch/scan/render use no model tokens. Does not include outer dispatcher or unrelated earlier experiments.'}
    dump(out/'manifest.json', manifest)
    raw = pd.DataFrame(rows)
    raw['trade_date'] = pd.to_datetime(raw.trade_date)
    summary = []
    with OpenAI(base_url=cfg['BASE_URL'], api_key=cfg['LLM_KEY'], timeout=180, max_retries=0) as client:
        for label in args.stocks:
            code, name = STOCKS[label]
            folder = out/label
            folder.mkdir()
            calls = []
            def call(stage, prompt, images, structured):
                number = len(calls)+1
                start = time.perf_counter()
                content = [{'type': 'text', 'text': prompt}]
                image_records = []
                for path in images:
                    data = path.read_bytes()
                    content.append({'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,'+base64.b64encode(data).decode(), 'detail': 'high'}})
                    image_records.append({'path': str(path.relative_to(folder)), 'sha256': hashlib.sha256(data).hexdigest()})
                request_model = args.vlm_model if stage.startswith('vlm_') else model
                item = {'stage': stage, 'requested_model': request_model,
                        'prompt': prompt, 'images': image_records, 'structured': structured}
                (folder/f'{number:02d}_{stage}_prompt.txt').write_text(prompt)
                try:
                    response = _create_llm_completion([{'role': 'user', 'content': content}], model=request_model, max_tokens=16384,
                        temperature=0, enable_think=False, response_format={'type': 'json_object'} if structured else None, client_instance=client)
                    item.update(response=response.choices[0].message.content,
                                finish_reason=response.choices[0].finish_reason, returned_model=response.model,
                                usage=response.usage.model_dump() if response.usage else None)
                    if structured:
                        item['parsed'] = extract_first_json(item['response'], log_errors=False)
                except Exception as exc:
                    item.update(error_class=type(exc).__name__, http_status=getattr(exc, 'status_code', None))
                item['seconds'] = time.perf_counter()-start
                calls.append(item)
                dump(folder/f'{number:02d}_{stage}.json', item)
                dump(folder/'usage.json', usage_totals(calls))
                print(json.dumps({'stock': label, 'stage': stage, 'seconds': round(item['seconds'], 2),
                                  'tokens': (item.get('usage') or {}).get('total_tokens'),
                                  'finish': item.get('finish_reason'), 'error': item.get('error_class')}, ensure_ascii=False), flush=True)
                return item
            bars = raw[(raw.stk_code==code) & (raw.trade_date<=pd.Timestamp(args.asof))].set_index('trade_date').sort_index()
            scale = bars.close.iloc[-1]/bars.adjclose.iloc[-1]
            b = bars.drop(columns=['open', 'high', 'low', 'close']).rename(columns={f'adj{k}': k for k in ['open', 'high', 'low', 'close']})
            b[['open', 'high', 'low', 'close']] *= scale
            start = time.perf_counter()
            f = prepare(b)
            prepare_seconds = time.perf_counter()-start
            dump(folder/'features.json', json.loads(f.reset_index().to_json(orient='records', date_format='iso')))
            question = f'请分析{name}（{code}）截至{args.asof}最近两周的K线：出现过哪些典型形态，现在还有效吗？结合实际量价给出判断；没有典型形态也请正常分析走势。我没有上传图片。'
            if args.question:
                question = f'{name}（{code}），截至{args.asof}。'+args.question
            try:
                result = analyze(f, code, question, folder, call, args.window)
                result.update(prepare_seconds=prepare_seconds, input_rows=len(b), usage=usage_totals(calls))
                dump(folder/'result.json', result)
                summary.append({'stock': label, 'candidates': len(result['candidates']), 'selected_ids': result['selected_ids'],
                                'numeric_only_ids': result['numeric_only_ids'],
                                'weak_skipped': len(result['weak_ids']),
                                'completed_interpretations': sum((r['interpretation'] or {}).get('finish_reason') == 'stop' for r in result['reviews']),
                                'seconds': result['seconds']+prepare_seconds, 'usage': result['usage']})
            except Exception as exc:
                dump(folder/'failure.json', {'error_class': type(exc).__name__, 'usage': usage_totals(calls)})
                summary.append({'stock': label, 'error_class': type(exc).__name__, 'usage': usage_totals(calls)})
            dump(out/'summary.json', summary)
    if any('error_class' in item for item in summary):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
