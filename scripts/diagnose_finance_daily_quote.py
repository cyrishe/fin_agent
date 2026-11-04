"""Read-only bounded SQL diagnostics; no provider or database changes."""
import argparse
import json
from pathlib import Path
import sys
import time


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--env-file', required=True)
    p.add_argument('--output', required=True)
    a = p.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from dotenv import load_dotenv
    load_dotenv(a.env_file, override=True)
    from src.utils.mysql_utils import StockInfoDbUtils
    from src.experiments.staged_data_protocol.phase2 import quote_provider as q
    import pymysql
    source = q.QUOTE_SOURCES['stock']
    args = {'codes': ['600519.SH'], 'mode': 0, 'count': 1}
    where, params = q._build_where(source=source, args=args)
    sql = q._build_per_entity_sql(source=source, fields=['code', 'name', 'tradedate', 'open'], where_sql=where, args=args)
    variants = {
        'provider_original': (sql, [*params, 1, 500001]),
        'same_single_security_without_window': (
            'SELECT q.stk_code AS code,b.stk_name AS name,q.trade_date AS tradedate,q.open '
            'FROM kcrp_stock_price q LEFT JOIN kcrp_stock_baseinfo b ON b.stk_code=q.stk_code '
            f'WHERE {where} ORDER BY q.trade_date DESC LIMIT 1', params),
        'price_only': (
            f'SELECT q.stk_code,q.trade_date,q.open FROM kcrp_stock_price q WHERE {where} '
            'ORDER BY q.trade_date DESC LIMIT 1', params),
    }
    started = time.monotonic()
    db = StockInfoDbUtils(database='kingdomai')
    report = {'connection_ms': round((time.monotonic()-started)*1000, 2), 'queries': {}}
    try:
        with db.conn.cursor(pymysql.cursors.DictCursor) as c:
            # Session-local read deadline, not a global DB configuration change.
            c.execute('SET SESSION MAX_EXECUTION_TIME=8000')
            c.execute('SELECT 1 AS alive')
            report['connectivity'] = c.fetchone()
            for table in ('kcrp_stock_price', 'kcrp_stock_baseinfo'):
                c.execute(f'SHOW INDEX FROM {table}')
                report[table + '_indexes'] = c.fetchall()
                c.execute(f'SHOW FULL COLUMNS FROM {table} WHERE Field IN ("stk_code", "trade_date")')
                report[table + '_column_types'] = c.fetchall()
            for name, (statement, bindings) in variants.items():
                info = {'sql': c.mogrify(statement, tuple(bindings))}
                report['queries'][name] = info
                c.execute('EXPLAIN ' + statement, tuple(bindings))
                info['explain'] = c.fetchall()
                started = time.monotonic()
                try:
                    c.execute(statement, tuple(bindings))
                    info['rows'] = c.fetchall()
                except pymysql.MySQLError as exc:
                    info['error'] = str(exc)
                info['elapsed_ms'] = round((time.monotonic()-started)*1000, 2)
                print(name, info['elapsed_ms'], info.get('error', 'ok'), flush=True)
    finally:
        db.close_db()
        Path(a.output).write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    main()
