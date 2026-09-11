"""Backfill completed test requests from saved API evidence. Preview unless --apply."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.eval_finance_mcp import load_run
from src.services.request_usage_service import TABLE, TZ, connect, total_tokens


def evidence_rows(folder):
    records, _ = load_run(folder)
    rows = []
    seen = set()
    for record in records:
        response = record.get('response') or {}
        rid = response.get('id', '')
        detail = response.get('detail') or {}
        amount = total_tokens(detail.get('usage'))
        if not rid.startswith('fq_') or rid in seen or amount is None or type(response.get('ok')) is not bool:
            raise ValueError('Each backfill needs a unique original request ID, known usage and completion outcome')
        if detail.get('total_tokens') is not None and detail['total_tokens'] != amount:
            raise ValueError('Usage fields disagree')
        finished = datetime.fromisoformat(response['created_at'].replace('Z', '+00:00'))
        if finished.tzinfo is None:
            raise ValueError('Completion timestamp must have timezone')
        rows.append((rid, 'test', finished.astimezone(TZ).replace(tzinfo=None), amount, int(response['ok'])))
        seen.add(rid)
    if not rows:
        raise ValueError('No completed requests to backfill')
    return rows


def apply_rows(rows, db_connect=connect):
    conn = db_connect()
    inserted = 0
    try:
        conn.begin()
        with conn.cursor() as cursor:
            for row in rows:
                cursor.execute(f'SELECT channel,finished_at,total_tokens,succeeded FROM {TABLE} WHERE request_id=%s FOR UPDATE', (row[0],))
                existing = cursor.fetchone()
                if existing:
                    if tuple(existing) != row[1:]:
                        raise ValueError('Existing request differs from saved test evidence; no records changed')
                    continue
                cursor.execute(f'INSERT INTO {TABLE} (request_id,channel,finished_at,total_tokens,succeeded) VALUES (%s,%s,%s,%s,%s)', row)
                inserted += 1
        conn.commit()
        return inserted
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir', type=Path)
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.env_file:
        from dotenv import load_dotenv
        load_dotenv(args.env_file, override=False)
    rows = evidence_rows(args.run_dir)
    result = {'requests': len(rows), 'total_tokens': sum(r[3] for r in rows),
              'completion_days': sorted({r[2].date().isoformat() for r in rows}), 'apply': args.apply}
    if args.apply:
        result['inserted'] = apply_rows(rows)
        result['already_recorded'] = len(rows) - result['inserted']
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
