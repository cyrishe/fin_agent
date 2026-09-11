"""Seed verified SSE 2026 dates; default dry run, --apply writes missing rows only."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SOURCE_URL = "https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml"
# SSE announcement 2025 No.45, checked against the annual closures page 2026-09-06.
CLOSURES = (
    ("2026-01-01", "2026-01-03"),
    ("2026-02-15", "2026-02-23"),
    ("2026-04-04", "2026-04-06"),
    ("2026-05-01", "2026-05-05"),
    ("2026-06-19", "2026-06-21"),
    ("2026-09-25", "2026-09-27"),
    ("2026-10-01", "2026-10-07"),
)
DDL = """
CREATE TABLE IF NOT EXISTS aiia_trade_calendar (
    market_code VARCHAR(16) NOT NULL COMMENT 'Existing application market identifier: CN_A',
    calendar_date DATE NOT NULL,
    is_trade_day TINYINT UNSIGNED NOT NULL COMMENT '1=open, 0=weekend or exchange holiday',
    trade_seq INT UNSIGNED NULL COMMENT 'Chronological trading-day sequence per market; closed days are NULL',
    source_url VARCHAR(512) NULL COMMENT 'Official annual schedule',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (market_code, calendar_date),
    KEY idx_market_open_date (market_code, is_trade_day, calendar_date),
    KEY idx_market_open_seq (market_code, is_trade_day, trade_seq)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Exchange trading calendar; only verified years'
"""


def calendar_rows():
    closures = [(date.fromisoformat(a), date.fromisoformat(b)) for a,b in CLOSURES]
    start = date(2026, 1, 1)
    return [
        ("CN_A", day, int(day.weekday() < 5 and not any(a <= day <= b for a,b in closures)), SOURCE_URL)
        for day in (start + timedelta(days=n) for n in range(365))
    ]


def seed(db):
    rows = calendar_rows()
    with db.conn.cursor() as cursor:
        cursor.execute(DDL)
        ensure_sequence_schema(cursor)
    # DDL commits in MySQL. Inserts and read-back verification form one transaction.
    db.conn.begin()
    try:
        with db.conn.cursor() as cursor:
            cursor.execute("SELECT calendar_date,is_trade_day FROM aiia_trade_calendar WHERE market_code=%s AND calendar_date BETWEEN %s AND %s FOR UPDATE", ("CN_A", rows[0][1], rows[-1][1]))
            existing = dict(cursor.fetchall())
            if any(day in existing and existing[day] != opened for _,day,opened,_ in rows):
                raise ValueError("Existing calendar differs from verified schedule; no rows overwritten")
            missing = [r for r in rows if r[1] not in existing]
            if missing:
                cursor.executemany("INSERT INTO aiia_trade_calendar (market_code,calendar_date,is_trade_day,source_url) VALUES (%s,%s,%s,%s)", missing)
            cursor.execute("SELECT calendar_date,is_trade_day FROM aiia_trade_calendar WHERE market_code=%s AND calendar_date BETWEEN %s AND %s ORDER BY calendar_date", ("CN_A", rows[0][1], rows[-1][1]))
            if list(cursor.fetchall()) != [(r[1],r[2]) for r in rows]:
                raise ValueError("Calendar read-back verification failed")
            fill_trade_sequence(cursor)
        db.conn.commit()
    except Exception:
        db.conn.rollback()
        raise
    return len(missing)


def ensure_sequence_schema(cursor):
    """Add the existing KD-provider contract to calendars created by older seeders."""
    cursor.execute("SHOW COLUMNS FROM aiia_trade_calendar LIKE 'trade_seq'")
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE aiia_trade_calendar ADD COLUMN trade_seq INT UNSIGNED NULL COMMENT 'Chronological trading-day sequence per market; closed days are NULL'")
    cursor.execute("SHOW INDEX FROM aiia_trade_calendar WHERE Key_name='idx_market_open_seq'")
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE aiia_trade_calendar ADD INDEX idx_market_open_seq (market_code,is_trade_day,trade_seq)")


def sequence_updates(rows):
    counters = {}
    updates = []
    for market, day, opened, existing in sorted(rows, key=lambda r: (r[0], r[1])):
        if opened:
            counters[market] = counters.get(market, 0) + 1
            expected = counters[market]
        else:
            expected = None
        if expected != existing:
            updates.append((expected, market, day))
    return updates


def fill_trade_sequence(cursor):
    cursor.execute("SELECT market_code,calendar_date,is_trade_day,trade_seq FROM aiia_trade_calendar ORDER BY market_code,calendar_date FOR UPDATE")
    updates = sequence_updates(cursor.fetchall())
    if updates:
        cursor.executemany("UPDATE aiia_trade_calendar SET trade_seq=%s WHERE market_code=%s AND calendar_date=%s", updates)
    cursor.execute("SELECT market_code,calendar_date,is_trade_day,trade_seq FROM aiia_trade_calendar ORDER BY market_code,calendar_date")
    if sequence_updates(cursor.fetchall()):
        raise ValueError("Trading-day sequence verification failed")
    return len(updates)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    rows = calendar_rows()
    print(f"2026: {len(rows)} dates, {sum(r[2] for r in rows)} open days")
    if not args.apply:
        print("Dry run; use --apply to insert into the configured new kingdomai database.")
        return
    from dotenv import load_dotenv
    from src.finance_api.data_status import MonitorDatabase
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    db = MonitorDatabase()
    try:
        if (db.host, db.port, db.database) != ("47.94.1.2", 3312, "kingdomai"):
            raise ValueError("Not the authorized new kingdomai database; refusing write")
        print(f"Verified target {db.host}:{db.port}/{db.database}; inserted {seed(db)} rows")
    finally:
        db.close_db()


if __name__ == "__main__":
    main()
