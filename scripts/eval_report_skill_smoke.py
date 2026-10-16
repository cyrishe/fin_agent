"""Read-only report sampling and isolated, unforced real-model skill smoke test."""
from __future__ import annotations

import hashlib
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
from dotenv import load_dotenv
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
parser.add_argument('--output', type=Path)
parser.add_argument('--discover', action='store_true')
parser.add_argument('--diagnostic', action='store_true')
ARGS = parser.parse_args()
load_dotenv(ARGS.env_file, override=False)

OUT = (ARGS.output or ROOT / ('outputs/report_skill_smoke_20260908_diagnostic' if ARGS.diagnostic else 'outputs/report_skill_smoke_20260908')).resolve()
OUT.mkdir(parents=True, exist_ok=True)


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def discover():
    import pymysql
    from src.utils.mysql_utils import StockInfoDbUtils
    db = StockInfoDbUtils(database='kingdomai')
    with db.conn.cursor(pymysql.cursors.DictCursor) as c:
        c.execute("SELECT security_code,security_name,COUNT(*) reports,COUNT(DISTINCT COALESCE(NULLIF(research_institution,''),publisher)) institutions,MIN(publish_at) earliest,MAX(publish_at) latest FROM reports WHERE publish_at >= %s AND publish_at < %s GROUP BY security_code,security_name ORDER BY reports DESC,security_code LIMIT 15", ('2026-06-08', '2026-09-09'))
        save('coverage.json', {'date_field': 'publish_at', 'start_inclusive': '2026-06-08', 'end_exclusive': '2026-09-09', 'rows': c.fetchall()})
        c.execute("SELECT id,security_code,security_name,title,publish_at,COALESCE(NULLIF(research_institution,''),publisher) institution,stock_rating,rating_change,change_reason,investment_highlights,risk_warnings,target_price_lower,target_price_upper,file_hash FROM reports WHERE publish_at >= %s AND publish_at < %s AND security_code IN ('601555','600519','600298') ORDER BY security_code,publish_at DESC,id DESC", ('2026-06-08', '2026-09-09'))
        rows = c.fetchall()
        save('source_reports.json', rows)
        for row in rows:
            print(json.dumps({k: row[k] for k in ('id','security_name','title','institution')}, ensure_ascii=False))


def run():
    if (OUT / 'manifest.json').exists():
        raise SystemExit('Existing run preserved; choose a new --output directory.')
    from src.scenarios.financial_qa.dsh_service import FinanceDeepSeekHarnessSessionService, _load_sdk_class
    from src.scenarios.financial_qa.service import FinancialQaCcService
    from src.scenarios.financial_qa.tools import FinanceDataQueryCcTools
    cases = [
        ('consensus', '请比较2026年6月8日至9月8日各机构对贵州茅台的研报观点：主要共识和分歧是什么？分歧来自增长预期、渠道判断还是估值假设？请说明实际覆盖的报告与机构范围，并给出来源。'),
        ('sensitivity', '如果糖蜜采购价格上涨10%，会怎样影响安琪酵母的利润？请结合2026年6月8日至9月8日的机构研究，分析水解糖替代、库存和提价能否缓冲；如果资料没有量化测算，不要硬给利润变化比例。'),
        ('revisions', '2026年6月8日至9月8日，机构是否调整了安琪酵母的盈利预测？请找出明确的上调或下调及原因，区分同一家机构的预测修订和不同机构之间的分歧，解释为什么下调盈利预测仍可能维持买入评级。'),
    ]
    tools = FinanceDataQueryCcTools()
    class RecordedHarness:
        def __init__(self, **kwargs):
            self.inner = _load_sdk_class()(**kwargs)
        def run(self, *args, **kwargs):
            result = self.inner.run(*args, **kwargs)
            save('raw_events_'+str(kwargs.get('session_id'))+'.json', list(result.events))
            return result
        def __getattr__(self, name):
            return getattr(self.inner, name)
    dsh = FinanceDeepSeekHarnessSessionService(enabled=True, system_tools=tools, worker_count=1, root_dir=OUT/'runtime', log_path=OUT/'events.jsonl', harness_factory=RecordedHarness)
    service = FinancialQaCcService(enabled=True, system_tools=tools, session_service=object(), dsh_session_service=dsh)
    files = sorted(set(ROOT.glob('src/scenarios/financial_qa/*')) | set(ROOT.glob('src/skills/finance-business/**/*')))
    save('manifest.json', {'started_at': datetime.now(timezone.utc).isoformat(), 'commit': subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(), 'source_hashes': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file() and '__pycache__' not in str(p)}, 'model': dsh.model, 'provider': dsh.provider, 'reasoning_effort': dsh.reasoning_effort, 'max_tokens': dsh.max_tokens, 'timeout_seconds': dsh.turn_timeout_seconds, 'loop_policy': dsh.loop_policy_config, 'entry': 'FinancialQaCcService.answer; normal_qa investment_analyst; no explicit_skill_ids; isolated_request', 'scope': 'Current dirty worktree; real DB and model; system catalog. Not HTTP routing, UI, or deployed process verification.', 'cases': cases})
    try:
        for case_id, question in (cases[:1] if ARGS.diagnostic else cases):
            turn_id = uuid.uuid4().hex
            started = time.monotonic()
            events = []
            def sink(event):
                row = {'at': datetime.now(timezone.utc).isoformat(), 'elapsed_ms': round((time.monotonic()-started)*1000), 'event': event}
                events.append(row)
                with (OUT/f'{case_id}.progress.jsonl').open('a', encoding='utf-8') as f:
                    f.write(json.dumps(row, ensure_ascii=False, default=str)+'\n')
                meta = event.get('metadata') or {}
                print(json.dumps({'case': case_id, 'elapsed_ms': row['elapsed_ms'], 'title': meta.get('title'), 'status': meta.get('status')}, ensure_ascii=False), flush=True)
            print('START '+case_id, flush=True)
            try:
                response = service.answer(thread_id=turn_id, turn_id=turn_id, owner_id='report-skill-smoke', user_text=question, dispatch_plan={'selected_agent':'investment_analyst','turn_mode':'normal_qa','entry':'agent_route','semantic_turn':{'resolved_question':question}}, runtime='dsh', research_mode='auto', execution_mode='standard', isolated_request=True, event_sink=sink, response_data_max_rows=100)
                records = [json.loads(line) for line in (OUT/'events.jsonl').read_text().splitlines()] if (OUT/'events.jsonl').exists() else []
                save(f'{case_id}.json', {'question': question, 'turn_id': turn_id, 'elapsed_ms': round((time.monotonic()-started)*1000), 'response':response, 'record': records[-1] if records else None, 'events':events})
                print('END '+case_id, flush=True)
            except Exception as exc:
                save(f'{case_id}.json', {'question':question,'turn_id':turn_id,'elapsed_ms':round((time.monotonic()-started)*1000),'error':str(exc),'events':events})
                print('ERROR '+case_id+' '+type(exc).__name__, flush=True)
    finally:
        dsh.close()


if __name__ == '__main__':
    discover() if ARGS.discover else run()
