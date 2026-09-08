"""Isolated real-HTTP MCP regression: source questions, data only, no replay.

Run from an isolated checkout. Credentials stay in memory; only the reporting
counter is disabled, not the financial gateway or model/tool execution path.
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PILOT = ['RTEF001', 'RTE003', 'RTEF164', 'RTEF194', 'RTEF037', 'RTE016']
REPORT_SOURCES = (
    'outputs/financial_qa_mainland_eval_20260902/cases_mainland_supported.json',
    'outputs/financial_qa_mainland_full_increment_20260903/cases_increment_no_news.json',
)


def load_report_cases(root=ROOT):
    return [{**case, 'source_file': source} for source in REPORT_SOURCES
            for case in json.loads((root / source).read_text())['cases']]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--full', action='store_true')
    parser.add_argument('--case-ids', nargs='*')
    parser.add_argument('--cases-file', type=Path, help='Optional small protocol regression set with a cases array.')
    parser.add_argument('--concurrency', type=int, default=3)
    parser.add_argument('--response-mode', choices=['data', 'both'], default='data')
    parser.add_argument('--max-rows', type=int, default=2)
    parser.add_argument('--conversation-id', help='Optional explicit context continuity. Omit for the independent benchmark.')
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(args.env_file)
    # The gateway is unchanged; isolate listeners, worker/session files and
    # reporting counters from the production service and its usage statistics.
    os.environ['FINANCE_API_ROOT_PATH'] = ''
    os.environ['FINANCE_API_ALLOWED_HOSTS'] = '127.0.0.1:*,localhost:*'
    os.environ['FINANCE_STATUS_ENABLED'] = '0'
    os.environ['FINANCE_DSH_BIN'] = str(ROOT / 'scripts/dsh_source_runtime.sh')
    os.environ['FINANCE_DSH_SDK_SOURCE'] = str(Path(os.environ['FINANCE_DSH_SOURCE_ROOT']) / 'python/sdk/src')
    os.environ['FINANCE_DSH_WORKERS'] = str(args.concurrency)
    os.environ['FINANCE_DSH_FINANCIAL_QA_ENABLED'] = '1'
    os.environ['FINANCE_DSH_TURN_TIMEOUT_SECONDS'] = '150'
    os.chdir(ROOT)
    import httpx
    import uvicorn
    from scripts.eval_finance_rest_detail import payload_problems
    from src.finance_api.app import create_app
    from src.finance_api.auth import FinanceApiKeyAuth
    from src.finance_api.service import FinanceApiGateway
    from src.scenarios.financial_qa.service import FinancialQaCcService
    from src.scenarios.financial_qa.dsh_service import FinanceDeepSeekHarnessSessionService, _load_sdk_class
    from src.scenarios.financial_qa.tools import FinanceDataQueryCcTools

    cases = (json.loads(args.cases_file.read_text())['cases']
             if args.cases_file else load_report_cases())
    by_id = {c['case_id']: c for c in cases}
    assert len(cases) == len(by_id) and cases
    if not args.cases_file:
        assert len(cases) == 184
    selected = args.case_ids or (list(by_id) if args.full or args.cases_file else PILOT)
    folder = args.output_dir.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    pending = [by_id[cid] for cid in selected if not (folder / f'{cid}.json').exists()]
    key = secrets.token_urlsafe(32)
    class RecordedHarness:
        def __init__(self, **kwargs):
            self.inner = _load_sdk_class()(**kwargs)

        def run(self, *positional, **kwargs):
            result = self.inner.run(*positional, **kwargs)
            path = folder / ('native_' + str(kwargs.get('session_id')) + '.json')
            path.write_text(json.dumps(list(result.events), ensure_ascii=False, indent=2, default=str))
            return result

        def __getattr__(self, name):
            return getattr(self.inner, name)

    query_tools = FinanceDataQueryCcTools()
    dsh = FinanceDeepSeekHarnessSessionService(
        enabled=True, system_tools=query_tools, worker_count=args.concurrency,
        root_dir=folder / 'runtime', log_path=folder / 'events.jsonl',
        harness_factory=RecordedHarness)
    engine = FinancialQaCcService(enabled=True, system_tools=query_tools,
        session_service=object(), dsh_session_service=dsh)
    gateway = FinanceApiGateway(engine=engine, usage_recorder=lambda **_: None)
    app = create_app(auth=FinanceApiKeyAuth({'report-eval': key}), gateway=gateway)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 30
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError('Isolated MCP listener did not start')
            threading.Event().wait(.05)
        # Exercise the real authentication middleware before any paid query.
        auth_checks = {}
        with httpx.Client(timeout=10) as client:
            probe = {'jsonrpc': '2.0', 'id': 'auth-check', 'method': 'tools/call',
                     'params': {'name': 'finance_data_query',
                                'arguments': {'query': '认证边界检查'}}}
            for label, headers in [('missing_key', {}), ('invalid_key', {'X-API-Key': 'invalid-evaluation-key'})]:
                response = client.post(f'http://127.0.0.1:{port}/mcp', json=probe, headers=headers)
                auth_checks[label] = response.status_code
                if response.status_code != 401:
                    raise RuntimeError(f'MCP authentication preflight failed: {label}')
            response = client.post(f'http://127.0.0.1:{port}/mcp',
                headers={'X-API-Key': key, 'Accept': 'application/json, text/event-stream'},
                json={'jsonrpc': '2.0', 'id': 'auth-check', 'method': 'tools/list', 'params': {}})
            auth_checks['valid_key'] = response.status_code
            if response.status_code != 200 or not response.json().get('result', {}).get('tools'):
                raise RuntimeError('MCP authentication preflight failed: valid_key')
        warm = gateway.prewarm()
        manifest = {'revision': args.revision, 'model': dsh.model, 'provider': dsh.provider,
            'reasoning_effort': dsh.reasoning_effort, 'loop_policy': dsh.loop_policy_config,
            'runtime': 'dsh', 'execution_mode': 'standard', 'response_mode': args.response_mode,
            'research_mode': 'fast', 'max_rows': args.max_rows,
            'case_count': len(selected), 'concurrency': args.concurrency,
            'transport': 'real HTTP MCP on isolated loopback listener',
            'conversation_id_supplied': args.conversation_id,
            'usage_counter': 'disabled for benchmark', 'prewarm': warm,
            'authentication_checks': auth_checks,
            'policy_sha256': hashlib.sha256((ROOT / 'src/scenarios/financial_qa/dsh_loop_policy.mjs').read_bytes()).hexdigest(),
            'created_at': datetime.now(timezone.utc).isoformat()}
        (folder / 'run_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        print(json.dumps({'ready': True, 'pending': len(pending), 'prewarm': warm}, ensure_ascii=False), flush=True)

        def run(case):
            request = {'query': case['question'], 'response_mode': args.response_mode, 'runtime': 'dsh',
                       'execution_mode': 'standard', 'research_mode': 'fast',
                       'detail': True, 'max_rows': args.max_rows}
            if args.conversation_id:
                request['conversation_id'] = args.conversation_id
            started = time.monotonic()
            try:
                with httpx.Client(timeout=180, headers={'X-API-Key': key,
                    'Accept': 'application/json, text/event-stream'}) as client:
                    response = client.post(f'http://127.0.0.1:{port}/mcp', json={'jsonrpc': '2.0',
                        'id': case['case_id'], 'method': 'tools/call',
                        'params': {'name': 'finance_data_query', 'arguments': request}})
                    wire = response.json()
                payload = wire.get('result', {}).get('structuredContent', {})
                problems = payload_problems({**payload, 'summary': None}, response.status_code)
                if args.response_mode == 'both' and not payload.get('summary'):
                    problems.append('missing_summary')
                if args.response_mode == 'data' and payload.get('summary'):
                    problems.append('unexpected_generated_summary')
                if wire.get('error') or wire.get('result', {}).get('isError'):
                    problems.append('mcp_error')
                if payload.get('response_mode') != args.response_mode or payload.get('runtime') != 'dsh':
                    problems.append('wrong_execution_mode')
                if payload.get('conversation_id') != args.conversation_id:
                    problems.append('unexpected_conversation_id')
                for page in (payload.get('data') or {}).get('results', []):
                    if page['rows_returned'] != len(page['rows']) or len(page['rows']) > args.max_rows:
                        problems.append('invalid_data_page')
                result = {'case': case, 'request': request, 'transport': 'mcp',
                    'http_status': response.status_code, 'response': payload,
                    'problems': sorted(set(problems)), 'revision': args.revision}
            except Exception as exc:
                result = {'case': case, 'request': request, 'transport': 'mcp',
                          'problems': ['transport_error'], 'error_type': type(exc).__name__}
            result['client_elapsed_ms'] = round((time.monotonic() - started) * 1000, 3)
            result['finished_at'] = datetime.now(timezone.utc).isoformat()
            with (folder / f"{case['case_id']}.json").open('x') as out:
                json.dump(result, out, ensure_ascii=False, indent=2)
            return result

        iterator = iter(pending)
        completed = failed = 0
        stopped = False
        with futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            active = {pool.submit(run, c) for c in [next(iterator, None) for _ in range(args.concurrency)] if c}
            while active:
                done, active = futures.wait(active, return_when=futures.FIRST_COMPLETED)
                for future in done:
                    result = future.result()
                    completed += 1
                    failed += bool(result['problems'])
                    payload = result.get('response', {})
                    detail = payload.get('detail') or {}
                    print(json.dumps({'case_id': result['case']['case_id'], 'ok': payload.get('ok'),
                        'seconds': round(result['client_elapsed_ms']/1000, 2), 'turns': detail.get('turns'),
                        'tokens': detail.get('total_tokens'), 'rows': payload.get('execution', {}).get('total_rows'),
                        'problems': result['problems']}, ensure_ascii=False), flush=True)
                    if 'transport_error' in result['problems'] or 'unexpected_generated_summary' in result['problems']:
                        stopped = True
                    if result['client_elapsed_ms'] > 120000 or (completed >= 10 and failed / completed > .2):
                        stopped = True
                if not stopped:
                    for _ in done:
                        case = next(iterator, None)
                        if case:
                            active.add(pool.submit(run, case))
        print(json.dumps({'completed': completed, 'failed': failed, 'stopped': stopped}), flush=True)
        if stopped:
            raise SystemExit(2)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        gateway.close()
        sock.close()
        # No temporary key was persisted; dropping the process revokes access.


if __name__ == '__main__':
    main()
