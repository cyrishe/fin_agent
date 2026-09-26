"""Explicit, additive storage repairs for the new kingdomai database. Dry run by default."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--calendar-sequence',action='store_true')
    p.add_argument('--daily-index',action='store_true')
    p.add_argument('--output-dir',type=Path,required=True)
    args=p.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT/'.env')
    from src.finance_api.data_status import MonitorDatabase
    from scripts.init_trade_calendar_2026 import ensure_sequence_schema,fill_trade_sequence
    d=MonitorDatabase()
    if (d.host,d.port,d.database)!=('47.94.1.2',3312,'kingdomai'):
        d.close_db();raise SystemExit('Refusing a different database target')
    args.output_dir.mkdir(parents=True,exist_ok=True)
    stamp=time.strftime('%Y%m%d_%H%M%S')
    try:
        with d.conn.cursor() as c:
            c.execute('SHOW CREATE TABLE aiia_trade_calendar');ddl=c.fetchone()[1]
            c.execute('SELECT * FROM aiia_trade_calendar ORDER BY market_code,calendar_date');calendar=c.fetchall()
            c.execute('SHOW INDEX FROM kcrp_stock_price');indexes=c.fetchall()
        backup=args.output_dir/f'storage_before_{stamp}.json'
        with backup.open('x') as f: json.dump({'calendar_ddl':ddl,'calendar_columns':[x[0] for x in _columns(d)],'calendar_rows':calendar,'daily_indexes':indexes},f,ensure_ascii=False,indent=2,default=str)
        print('Backup:',backup,flush=True)
        if args.calendar_sequence:
            with d.conn.cursor() as c: ensure_sequence_schema(c)
            d.conn.begin()
            try:
                with d.conn.cursor() as c: updated=fill_trade_sequence(c)
                d.conn.commit()
            except Exception:
                d.conn.rollback();raise
            print('Calendar sequence rows updated:',updated,flush=True)
        if args.daily_index:
            # A code-leading index is needed by per-security latest-N reads.
            columns={}
            for row in indexes: columns.setdefault(row[2],[]).append((row[3],row[4]))
            present=any([x[1] for x in sorted(v)][:2]==['stk_code','trade_date'] for v in columns.values())
            if present: print('Equivalent code/date index already exists',flush=True)
            else:
                # DDL cannot be rolled back. Online-only, fail instead of falling back to a blocking table copy.
                import pymysql
                d.close_db()
                d.conn=pymysql.connect(host=d.host,port=d.port,user=d.user,password=d.password,
                    database=d.database,charset='utf8mb4',connect_timeout=5,read_timeout=600,
                    write_timeout=5,autocommit=True)
                with d.conn.cursor() as c:
                    c.execute('SET SESSION lock_wait_timeout=5')
                    c.execute('ALTER TABLE kcrp_stock_price ADD INDEX idx_stk_code_trade_date (stk_code,trade_date), ALGORITHM=INPLACE, LOCK=NONE')
                print('Added idx_stk_code_trade_date online',flush=True)
    finally: d.close_db()

def _columns(db):
    with db.conn.cursor() as c:
        c.execute('SHOW COLUMNS FROM aiia_trade_calendar');return c.fetchall()

if __name__=='__main__': main()
