"""Migrate the two approved active strategies; default is a read-only preview."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path

from scripts.readable_strategy_execution import IDENTITIES, CLOSE_OPEN_ID, identity, NEXT_0940_CLOSE
from scripts.strategy_execution_store import record_daily_run, record_selection, _json
from scripts.run_automl_1440_strategy import write_connection, init_db


def decoded(value):
    return json.loads(value) if isinstance(value, str) else (value or {})


def migrate(cursor, *, execute=False):
    summary = []
    for old_id in IDENTITIES:
        cursor.execute('SELECT * FROM automl_strategy_execution WHERE strategy_id=%s ORDER BY user_id,signal_date,selection_rank', (old_id,))
        executions = {(r['user_id'], str(r['signal_date']), r['selection_rank']): r for r in cursor.fetchall()}
        cursor.execute('SELECT * FROM automl_strategy_selection WHERE strategy_id=%s ORDER BY user_id,signal_date,selection_rank', (old_id,))
        selections = {(r['user_id'], str(r['signal_date']), r['selection_rank']): r for r in cursor.fetchall()}
        cursor.execute('SELECT * FROM automl_strategy_run WHERE strategy_id=%s', (old_id,))
        old_runs = {(r['user_id'], str(r['signal_date'])): r for r in cursor.fetchall()}
        groups = defaultdict(list)
        for key in sorted(executions.keys() | selections.keys()):
            groups[key[:2]].append(key)
        for key in old_runs:
            groups.setdefault(key, [])
        for (user, day), keys in groups.items():
            run = old_runs.get((user, day))
            sample = (executions.get(keys[0]) or selections.get(keys[0])) if keys else {}
            context = decoded(sample.get('context_json'))
            environment = decoded(sample.get('environment_json')) or {
                'source': 'legacy_selection_migration', 'quote_source': sample.get('quote_source'),
                **context}
            facts = dict(signal_at=sample.get('signal_at') or sample.get('signal_capture_at'),
                candidate_count=sample.get('candidate_count'), selection_count=len(keys),
                model_sha256=sample.get('model_sha256') or context.get('model_sha256'), environment=environment)
            if run:
                facts.update({k: run[k] for k in ('signal_at','candidate_count','selection_count','model_sha256')})
                facts['environment'] = decoded(run['environment_json'])
            if execute:
                record_daily_run(cursor, user_id=user, **identity(old_id), signal_date=day, **facts)
            for key in keys:
                row = executions.get(key)
                legacy = selections.get(key)
                source = row or legacy
                details = {**decoded(source.get('decision_json')), 'legacy_strategy_id': old_id,
                           'migrated_at': datetime.now().isoformat(), 'simulation_only': True}
                if legacy:
                    details['legacy_selection'] = {k: decoded(v) if k.endswith('_json') else v for k,v in legacy.items()}
                details['exit_rule'] = source.get('exit_rule') or (NEXT_0940_CLOSE if old_id == CLOSE_OPEN_ID else None)
                # Saved historical signals become explicitly labelled proxy transactions.
                actionable = source.get('actionable', 1)
                buy = source.get('entry_price') if row else source.get('signal_price') if actionable else None
                buy_at = source.get('entry_at') if row else source.get('signal_capture_at') if actionable else None
                basis = source.get('entry_price_basis') if row else '14:40_signal_quote_proxy' if actionable else None
                summary.append(dict(strategy_name=identity(old_id)['strategy_name'], model_version='v1.0',
                    user_id=user, signal_date=day, rank=key[2], stock_code=source['stock_code'],
                    actionable=actionable, buy_price=buy, sell_price=source.get('exit_price')))
                if not execute:
                    continue
                record_selection(cursor, user_id=user, **identity(old_id), signal_date=day,
                    selection_rank=key[2], stock_code=source['stock_code'], stock_name=source['stock_name'],
                    actionable=actionable, signal_at=source.get('signal_at') or source.get('signal_capture_at'),
                    signal_price=source.get('signal_price'), buy_at=buy_at, buy_price=buy,
                    buy_price_basis=basis, details=details)
                if row and row.get('exit_price') is not None:
                    cursor.execute('''UPDATE strategy_execution SET sell_at=%s,sell_price=%s,
                        sell_price_basis=%s,gross_return_pct=%s,net_return_pct=%s
                        WHERE user_id=%s AND strategy_name=%s AND model_version=%s
                        AND signal_date=%s AND selection_rank=%s AND sell_price IS NULL''',
                        (row['exit_at'],row['exit_price'],row['exit_price_basis'],row['gross_return_pct'],
                         decoded(row.get('decision_json')).get('net_return_pct'),user,
                         identity(old_id)['strategy_name'],identity(old_id)['model_version'],day,key[2]))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=Path('.env'))
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if args.execute:
        init_db(args.env_file)
    with write_connection(args.env_file) as db:
        try:
            with db.cursor() as cursor:
                if not args.execute:
                    cursor.execute('START TRANSACTION READ ONLY')
                result = migrate(cursor, execute=args.execute)
            if args.execute:
                db.commit()
            else:
                db.rollback()
            print(_json({'execute':args.execute,'rows':result}))
        except Exception:
            db.rollback()
            raise


if __name__ == '__main__':
    main()
