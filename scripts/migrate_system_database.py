"""Copy the configured system DB to the authorized server; source is read-only.

--apply creates a NEW schema only; run while application writers are stopped.
No source DDL/DML, and no deletion or overwrite of an existing destination.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pymysql
from dotenv import load_dotenv
from src.utils.system_db_utils import system_db_connection_kwargs
from src.finance_api.data_status import MonitorDatabase


def digest_row(row):
    return repr(tuple(row)).encode("utf-8") + b"\n"


def migrate():
    load_dotenv(Path(__file__).resolve().parents[1]/".env")
    source_kwargs = system_db_connection_kwargs()
    if (source_kwargs["host"],source_kwargs["database"]) != ("47.112.132.214","aiia_system"):
        raise RuntimeError("Source is not the verified old aiia_system")
    source = pymysql.connect(**source_kwargs,connect_timeout=5,read_timeout=60)
    target_wrapper = MonitorDatabase()
    target = target_wrapper.conn
    if (target_wrapper.host,target_wrapper.port) != ("47.94.1.2",3312):
        raise RuntimeError("Destination is not the authorized MySQL service")
    target._read_timeout = 60
    try:
        with source.cursor() as c:
            c.execute("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            c.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            c.execute("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME")
            tables = [r[0] for r in c.fetchall()]
            c.execute("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND (ENGINE <> 'InnoDB' OR TABLE_TYPE <> 'BASE TABLE')")
            if c.fetchone()[0]:
                raise RuntimeError("Only audited InnoDB tables are supported")
        with target.cursor() as c:
            c.execute("SHOW DATABASES LIKE 'aiia_system'")
            if c.fetchone():
                raise RuntimeError("Destination already exists; refusing overwrite")
            c.execute("CREATE DATABASE aiia_system CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci")
            c.execute("USE aiia_system")
            c.execute("SET time_zone='+08:00'")
            c.execute("SET FOREIGN_KEY_CHECKS=0")
        for table in tables:
            with source.cursor() as c:
                c.execute(f"SHOW CREATE TABLE `{table}`")
                ddl = c.fetchone()[1]
            with target.cursor() as c:
                c.execute(ddl)
        receipts = []
        for table in tables:
            with source.cursor() as c:
                c.execute(f"SHOW COLUMNS FROM `{table}`")
                columns = [r[0] for r in c.fetchall()]
                c.execute(f"SHOW INDEX FROM `{table}` WHERE Key_name='PRIMARY'")
                primary = [r[4] for r in sorted(c.fetchall(),key=lambda r:r[3])]
            if not primary:
                raise RuntimeError(f"No primary key for deterministic verification: {table}")
            order = ",".join(f"`{v}`" for v in primary)
            fields = ",".join(f"`{v}`" for v in columns)
            query = f"SELECT {fields} FROM `{table}` ORDER BY {order}"
            insert = f"INSERT INTO `{table}` ({fields}) VALUES ({','.join(['%s']*len(columns))})"
            expected = hashlib.sha256()
            count = 0
            target.begin()
            with source.cursor(pymysql.cursors.SSCursor) as src, target.cursor() as dst:
                src.execute(query)
                while True:
                    rows = src.fetchmany(20)
                    if not rows:
                        break
                    for row in rows:
                        expected.update(digest_row(row))
                    dst.executemany(insert,rows)
                    count += len(rows)
            target.commit()
            actual = hashlib.sha256()
            verified = 0
            with target.cursor(pymysql.cursors.SSCursor) as c:
                c.execute(query)
                for row in c:
                    actual.update(digest_row(row))
                    verified += 1
            if count != verified or expected.digest() != actual.digest():
                raise RuntimeError(f"Read-back verification failed: {table}")
            receipts.append({"table":table,"rows":count,"sha256":actual.hexdigest()})
            print(json.dumps(receipts[-1]),flush=True)
        print(json.dumps({"verified_tables":len(receipts),"total_rows":sum(r['rows'] for r in receipts),"source_unchanged":True}),flush=True)
    finally:
        source.rollback()
        source.close()
        target.close()


if __name__ == "__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply",action="store_true")
    args=p.parse_args()
    if args.apply:
        migrate()
    else:
        print("Dry run: source 47.112.132.214/aiia_system -> NEW 47.94.1.2:3312/aiia_system. Stop writers before --apply.")
