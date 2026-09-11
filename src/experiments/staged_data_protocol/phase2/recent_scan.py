"""Three-month probe for provably complete chronological top-N reads.

This is a physical read strategy, never a change to the user's date scope.
An insufficient probe is discarded; at most one full-scope read follows.
"""
from calendar import monthrange
from collections import Counter
from datetime import date
import os
import re


DATE_KEYS = {
    'date', 'tradedate', 'trade_date', 'start', 'end', 'start_date', 'end_date',
    'as_of', 'asof', 'report_date', 'report_period', 'ann_date', 'publish_at',
    'year', 'forecast_year',
}


def recent_predicate(*, args, date_expression, date_fields=(), today=None, enabled_by_default=False):
    """Only called by top-N planners, not aggregates or calendar windows."""
    if os.getenv('FIN_AGENT_RECENT_SCAN_ENABLED', '1' if enabled_by_default else '0').lower() in {'0', 'false', 'off'}:
        return None
    fields = DATE_KEYS | set(date_fields)
    if any(args.get(key) not in (None, '') for key in fields):
        return None
    # Conservative detection only: leave parsing/validation to the existing DSL.
    if any(re.search(r'\b' + re.escape(key) + r'\b', str(args.get('filter') or ''), re.I) for key in fields):
        return None
    if str(args.get('limit')) == '-1':
        return None
    anchor = today or date.today()
    offset = anchor.year * 12 + anchor.month - 1 - 3
    year, month = offset // 12, offset % 12 + 1
    cutoff = date(year, month, min(anchor.day, monthrange(year, month)[1]))
    # Expression is provider-owned; date is computed, never model-supplied SQL.
    return f"{date_expression} >= '{cutoff.isoformat()}'"


def chronological_recent_where(*, args, where_sql, order_sql, date_expression, date_fields=(), enabled_by_default=False):
    if order_sql.split(',')[0].strip().upper() != f'{date_expression} DESC'.upper():
        return None
    predicate = recent_predicate(args=args, date_expression=date_expression, date_fields=date_fields,
                                  enabled_by_default=enabled_by_default)
    return f'({where_sql}) AND {predicate}' if predicate else None


def fetch_recent_first(cursor, *, sql, params, recent_sql=None, recent_params=None,
                       required_rows=0, required_codes=(), per_code=0, evidence=None):
    """No merging and no catch/retry of DB failures; return one complete read."""
    if recent_sql:
        cursor.execute(recent_sql, tuple(params if recent_params is None else recent_params))
        rows = list(cursor.fetchall())
        if evidence is not None:
            evidence.update(recent_months=3, sql_reads=1)
        if required_codes:
            counts = Counter(str(row.get('code')) for row in rows)
            complete = all(counts[str(code)] >= per_code for code in required_codes)
        else:
            complete = required_rows > 0 and len(rows) >= required_rows
        if complete:
            return rows
        if evidence is not None:
            evidence['sql_reads'] = 2
    cursor.execute(sql, tuple(params))
    return list(cursor.fetchall())
