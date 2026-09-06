"""Read-only off/on comparisons. Run in an isolated source snapshot, not a service.

The baseline uses the SAME current SQL templates, with only the recent probe off.
This must not attribute pre-existing index/Top-N improvements to the probe.
"""
import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--cases', nargs='*')
    parser.add_argument('--max-query-ms', type=int, default=15000)
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(args.env_file)
    from src.utils.mysql_utils import StockInfoDbUtils
    from src.experiments.staged_data_protocol.phase2 import (
        quote_provider as q, report_provider as r, margin_provider as m,
        financial_provider as f, moneyflow_provider as mf, pricevalue_provider as pv,
        intraday_quote_provider as minute, stock_corporate_provider as corp,
    )

    class BoundedDB(StockInfoDbUtils):
        def connect_db(self):
            super().connect_db()
            with self.conn.cursor() as cursor:
                cursor.execute('SET SESSION MAX_EXECUTION_TIME=%s', (args.max_query_ms,))

    for module in (q, r, m, f, mf, pv, minute, corp):
        module.StockInfoDbUtils = BoundedDB
    cases = [
        ('daily_20', q.execute_quote_api, {'subject': 'stock', 'args': {'codes': ['300750.SZ'], 'count': 20}, 'outputs': ['code', 'tradedate', 'close']}),
        ('daily_multi', q.execute_quote_api, {'subject': 'stock', 'args': {'codes': ['300750.SZ', '600519.SH'], 'count': 20}, 'outputs': ['code', 'tradedate', 'close']}),
        ('daily_250', q.execute_quote_api, {'subject': 'stock', 'args': {'codes': ['300750.SZ'], 'count': 250}, 'outputs': ['code', 'tradedate', 'close']}),
        ('daily_explicit', q.execute_quote_api, {'subject': 'stock', 'args': {'codes': ['300750.SZ'], 'count': 20, 'start': '2025-01-01', 'end': '2025-01-31'}, 'outputs': ['code', 'tradedate', 'close']}),
        ('report_latest', r.execute_report_api, {'args': {'filter': 'code = 300308.SZ', 'limit': 20}, 'outputs': ['code', 'report_date', 'title']}),
        ('report_metric_latest', r.execute_report_metric_api, {'args': {'filter': 'code = 300308.SZ', 'limit': 20}, 'outputs': ['code', 'report_date', 'forecast_year']}),
        ('report_empty', r.execute_report_api, {'args': {'filter': 'code = 999999.SZ', 'limit': 20}, 'outputs': ['code', 'report_date', 'title']}),
        ('margin_latest', m.execute_margin_api, {'subject': 'stock', 'args': {'filter': 'code = 300750.SZ', 'limit': 20}, 'outputs': ['code', 'tradedate']}),
        ('moneyflow_latest', mf.execute_moneyflow_api, {'subject': 'stock', 'args': {'filter': 'code = 300750.SZ', 'limit': 20}, 'outputs': ['code', 'tradedate']}),
        ('pricevalue_latest', pv.execute_pricevalue_api, {'subject': 'stock', 'args': {'filter': 'code = 300750.SZ', 'limit': 20}, 'outputs': ['code', 'tradedate']}),
        ('financial_8', f.execute_financial_3_table_api, {'subject': 'stock', 'args': {'filter': 'code = 300750.SZ', 'limit': 8}, 'outputs': ['code', 'report_period', 'parent_net_profit']}),
        ('index_20', q.execute_quote_api, {'subject': 'index', 'args': {'codes': ['000300.SH'], 'count': 20}, 'outputs': ['code', 'tradedate', 'close']}),
        ('fund_20', q.execute_quote_api, {'subject': 'fund', 'args': {'codes': ['510300.SH'], 'count': 20}, 'outputs': ['code', 'tradedate', 'close']}),
        ('fund_250', q.execute_quote_api, {'subject': 'fund', 'args': {'codes': ['510300.SH'], 'count': 250}, 'outputs': ['code', 'tradedate', 'close']}),
        ('minute_20', minute.execute_intraday_quote_api, {'args': {'mode': 1, 'period': 60, 'count': 20, 'filter': 'code = 600519.SH'}, 'outputs': ['code', 'tradedate', 'bar_end_time', 'close']}),
        ('minute_multi', minute.execute_intraday_quote_api, {'args': {'mode': 1, 'period': 60, 'count': 20, 'filter': 'code in (600519.SH,300750.SZ)'}, 'outputs': ['code', 'tradedate', 'bar_end_time', 'close']}),
        ('business_segment', corp.execute_stock_corporate_api, {'dataview': 'business_segment', 'args': {'filter': 'code = 300750.SZ', 'limit': 5}, 'outputs': ['code', 'report_period', 'project_name', 'segment_sales']}),
    ]
    report = {'comparisons': [], 'note': 'Read-only; same SQL/index baseline; full ordered rows compared, not just row count.'}
    for name, fn, kwargs in cases:
        if args.cases and name not in args.cases:
            continue
        timings = {'0': [], '1': []}
        evidence = []
        equality = []
        baseline = None
        failures = []
        for repeat in range(args.repeats):
            for enabled in ('0', '1') if repeat % 2 == 0 else ('1', '0'):
                os.environ['FIN_AGENT_RECENT_SCAN_ENABLED'] = enabled
                start = time.monotonic()
                result = fn(**kwargs)
                elapsed = round((time.monotonic() - start) * 1000, 3)
                timings[enabled].append(elapsed)
                if result['status'] != 'ok':
                    failures.append({'enabled': enabled, 'status': result['status'], 'reason': result.get('reason')})
                    break
                rows = result.get('rows', [])
                if baseline is None:
                    baseline = rows
                equality.append(rows == baseline)
                if enabled == '1':
                    evidence.append(result.get('sql_shape', {}).get('recent_scan', {}))
            if failures:
                break
        entry = {'name': name, 'args': kwargs['args'], 'equal': all(equality) and not failures,
                 'rows': len(baseline or []), 'sample': (baseline or [])[:2], 'ms': timings,
                 'median_off_ms': statistics.median(timings['0']) if timings['0'] else None,
                 'median_on_ms': statistics.median(timings['1']) if timings['1'] else None,
                 'recent_scan': evidence, 'failures': failures}
        dates = [str(row[field]) for row in baseline or [] for field in ('tradedate', 'report_date', 'report_period') if row.get(field)]
        entry['date_range'] = [min(dates), max(dates)] if dates else []
        report['comparisons'].append(entry)
        print(json.dumps(entry, ensure_ascii=False, default=str), flush=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        if failures:
            break  # Do not turn repeated timeouts into a database load test.
    if not all(case['equal'] for case in report['comparisons']):
        raise SystemExit(2)


if __name__ == '__main__':
    main()
