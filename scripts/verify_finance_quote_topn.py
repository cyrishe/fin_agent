"""Compare bounded quote SQL to ROW_NUMBER reference on the same DB snapshot.

No model calls. Uses existing benchmark securities plus current bond/plate keys;
checks count-per-code, ranges, OR, global ordering and limit across all subjects.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def reference_sql(source, fields, where, args):
    order = str(args.get('order') or 'code asc').split()
    expression = source.fields[order[0]]
    direction = 'ASC' if len(order) > 1 and order[1].lower().startswith('asc') else 'DESC'
    return f"""SELECT {','.join('`'+f+'`' for f in fields)} FROM (
      SELECT {','.join(source.fields[f]+' AS `'+f+'`' for f in fields)},
        ROW_NUMBER() OVER(PARTITION BY {source.fields['code']} ORDER BY {source.fields['tradedate']} DESC) AS n,
        {expression} AS ordering, {source.fields['tradedate']} AS day_order
      FROM {source.table} q LEFT JOIN {source.base_table} b ON {source.join_on}
      WHERE {where}
    ) ranked WHERE n<=%s ORDER BY ordering {direction},day_order DESC LIMIT %s"""


def main(args):
    import pymysql
    from dotenv import load_dotenv
    from scripts.sync_shareholder_table import connection
    from src.experiments.staged_data_protocol.phase2 import quote_provider as q
    load_dotenv(ROOT / '.env')
    report = {'note': 'Full ordered results compared in one consistent read-only snapshot. SQL time includes network, no LLM.', 'cases': []}
    with connection('PLATFORM_DB_URL', ('47.94.1.2', 3312, 'kingdomai'), 'kingdomai') as db:
        with db.cursor(pymysql.cursors.DictCursor) as c:
            c.execute('SET SESSION MAX_EXECUTION_TIME=20000')
            c.execute('START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY')
            for subject, source in q.QUOTE_SOURCES.items():
                if args.subjects and subject not in args.subjects:
                    continue
                known = {'stock': ['300750.SZ', '600519.SH'], 'index': ['000300.SH', '000001.SH'], 'fund': ['510300.SH', '510500.SH']}
                if subject in known:
                    codes = known[subject]
                else:
                    c.execute(f"SELECT {source.fields['code']} AS code FROM {source.table} q WHERE q.trade_date=(SELECT MAX(trade_date) FROM {source.table}) ORDER BY {source.fields['code']} LIMIT 2")
                    codes = [row['code'] for row in c.fetchall()]
                if len(codes) < 2:
                    raise RuntimeError(f'Need two real securities for {subject}')
                c.execute(f'SELECT MAX(trade_date) AS day FROM {source.table}')
                latest = str(c.fetchone()['day'])
                cases = [
                    ('single20', {'codes': codes[:1], 'count': 20}),
                    ('multi20', {'codes': codes, 'count': 20}),
                    ('history250', {'codes': codes[:1], 'count': 250}),
                    ('explicit_history', {'codes': codes[:1], 'count': 20, 'start': '2025-01-01', 'end': '2025-01-31'}),
                    ('or_codes', {'filter': f'code = {codes[0]} or code = {codes[1]}', 'count': 2}),
                    ('order_pct', {'codes': codes, 'count': 20, 'order': 'pct desc'}),
                    ('global_limit', {'codes': codes, 'count': 20, 'limit': 5}),
                    ('market_day', {'date': latest, 'count': 1, 'limit': 30}),
                ]
                for name, params in cases:
                    fields = ['code', 'tradedate', 'close', 'pct']
                    where, bindings = q._build_where(source=source, args=params)
                    limit = q._base_query_limit(params, count_per_code=params['count'])['fetch_limit']
                    tail = [params['count'], limit]
                    sql = q._build_per_entity_sql(source=source, fields=fields, where_sql=where, args=params)
                    _, identity = q._count_identity_scope(source=source, args=params)
                    entry = {'subject': subject, 'case': name, 'args': params}
                    results = {}
                    try:
                        for label, statement, values in [('reference', reference_sql(source, fields, where, params), [*bindings, *tail]),
                                                        ('bounded', sql, [*identity, *bindings, *tail])]:
                            start = time.monotonic()
                            c.execute(statement, values)
                            rows = c.fetchall()
                            entry[label + '_ms'] = round((time.monotonic()-start)*1000, 3)
                            entry[label + '_sha256'] = hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()
                            results[label] = rows
                        entry['equal'] = results['reference'] == results['bounded']
                        entry['rows'] = len(results['bounded'])
                        entry['sample'] = results['bounded'][:2]
                    except Exception as exc:
                        entry.update(equal=False, error=str(exc))
                    report['cases'].append(entry)
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str)+'\n')
                    print(json.dumps(entry, ensure_ascii=False, default=str), flush=True)
                    if not entry['equal']:
                        raise SystemExit(2)
        db.rollback()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--subjects', nargs='*')
    main(parser.parse_args())
