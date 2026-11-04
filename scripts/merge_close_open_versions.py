"""Consolidate the accepted B1 historical replay into v1.0, preserving source records."""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

NAME = '收盘开盘'
DESCRIPTION = '20日滚动·七因子四分类'
TABLES = {'runs': 'strategy_daily_run', 'picks': 'strategy_execution'}


def key(row, pick=False):
    return (str(row['signal_date']), row['selection_rank']) if pick else str(row['signal_date'])


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def plan_merge(snapshot):
    """Replay wins historical dates; refuse any change to a recorded trade."""
    result, differences = {}, []
    old_picks = {key(r, True): r for r in snapshot['picks'] if r['model_version'] == 'v1.0'}
    source = [r for r in snapshot['picks'] if r['model_version'] == 'v1.1']
    new_picks = {key(r, True): r for r in source}
    replay_days = {key(r) for r in snapshot['runs'] if r['model_version'] == 'v1.1'}
    facts = ('stock_code', 'actionable', 'buy_at', 'buy_price', 'sell_at', 'sell_price', 'gross_return_pct', 'net_return_pct')
    for identity, old in old_picks.items():
        if identity[0] not in replay_days:
            continue
        new = new_picks.get(identity)
        # A missing or different executed record cannot be silently merged.
        if old['actionable'] or old['buy_price'] is not None or old['sell_price'] is not None:
            if new is None or any(old[f] != new[f] for f in facts):
                raise ValueError(f'实际交易不一致，停止合并: {identity}')
        if new:
            changed = {f: [old[f], new[f]] for f in (*facts, 'signal_price') if old[f] != new[f]}
            if changed:
                differences.append({'date_rank': identity, 'differences': changed})
    for group, json_field in [('runs', 'environment_json'), ('picks', 'details_json')]:
        is_pick = group == 'picks'
        existing = {key(r, is_pick): r for r in snapshot[group] if r['model_version'] == 'v1.0'}
        target = deepcopy(existing)
        for original in snapshot[group]:
            if original['model_version'] != 'v1.1':
                continue
            row = deepcopy(original)
            detail = json.loads(row[json_field]) if isinstance(row.get(json_field), str) else deepcopy(row.get(json_field) or {})
            audit = {'source_version': 'v1.1', 'source_description': row['version_desc'], 'reason': '同一B1策略历史重跑合并'}
            prior = existing.get(key(row, is_pick))
            if prior:
                audit['previous_record'] = prior
            if group == 'runs':
                audit['previous_selections'] = [p for p in snapshot['picks'] if p['model_version'] == 'v1.0' and key(p) == key(row)]
            detail['version_merge'] = audit
            row.update(model_version='v1.0', version_desc=DESCRIPTION)
            row[json_field] = encode(detail)
            target[key(row, is_pick)] = row
        # Source day is authoritative, including a day with fewer/no candidates.
        if is_pick:
            target = {k: v for k, v in target.items() if k[0] not in replay_days or k in new_picks}
        result[group] = list(target.values())
    return result, differences, replay_days


def load(cursor, user_id, lock=False):
    result = {}
    for name, table in TABLES.items():
        cursor.execute(f"SELECT * FROM {table} WHERE user_id=%s AND strategy_name=%s AND model_version IN ('v1.0','v1.1') ORDER BY signal_date,model_version" + (' FOR UPDATE' if lock else ''), (user_id, NAME))
        result[name] = cursor.fetchall()
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--env-file', type=Path, default=Path('.env'))
    p.add_argument('--user-id', default='imac')
    p.add_argument('--backup', required=True, type=Path)
    p.add_argument('--write-db', action='store_true')
    args = p.parse_args()
    from scripts.run_automl_1440_strategy import write_connection
    with write_connection(args.env_file) as db:
        try:
            with db.cursor() as cur:
                before = load(cur, args.user_id, lock=args.write_db)
                after, differences, days = plan_merge(before)
                receipt = dict(days=len(days), picks=len(after['picks']), runs=len(after['runs']), actionable=sum(bool(r['actionable']) for r in after['picks']), differences=differences)
                if args.write_db and days:
                    # An exclusive, private rollback snapshot must exist before changes.
                    args.backup.parent.mkdir(parents=True, exist_ok=True)
                    with args.backup.open('x') as f:
                        args.backup.chmod(0o600)
                        f.write(encode(before))
                    receipt['backup_sha256'] = hashlib.sha256(args.backup.read_bytes()).hexdigest()
                    marks = ','.join(['%s'] * len(days))
                    params = (args.user_id, NAME, *sorted(days))
                    for table in ('strategy_execution', 'strategy_daily_run'):
                        cur.execute(f"DELETE FROM {table} WHERE user_id=%s AND strategy_name=%s AND model_version IN ('v1.0','v1.1') AND signal_date IN ({marks})", params)
                    for group, table in TABLES.items():
                        for row in after[group]:
                            if key(row) not in days:
                                continue
                            columns = list(row)
                            cur.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join(['%s'] * len(columns))})", tuple(row[c] for c in columns))
                    actual = load(cur, args.user_id)
                    for group in TABLES:
                        # updated_at may be assigned by the database; compare all business facts.
                        normalize = lambda rows: sorted(encode({k: (json.loads(v) if k.endswith('_json') and isinstance(v, str) else v) for k,v in r.items() if k != 'updated_at'}) for r in rows)
                        if normalize(actual[group]) != normalize(after[group]):
                            raise ValueError(f'合并回读不一致: {group}')
            if args.write_db:
                db.commit()
            else:
                db.rollback()
        except Exception:
            db.rollback()
            raise
    print(encode(receipt))

if __name__ == '__main__':
    main()
