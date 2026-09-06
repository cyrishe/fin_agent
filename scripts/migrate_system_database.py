"""Copy the configured system DB to the authorized server; source is read-only.

--prepare creates only an empty destination schema, without changing connections.
--apply copies into a NEW or EMPTY schema; run while application writers are stopped.
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
from src.services.request_usage_service import DDL as USAGE_DDL
from scripts.manage_scheduled_task_schema import _read_statements

TARGET_SCHEMA = "aiia_system"


def prepare_destination(cursor):
    """An existing empty schema is safe; no destination data is overwritten."""
    cursor.execute("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s", (TARGET_SCHEMA,))
    exists = cursor.fetchone() is not None
    if exists:
        cursor.execute("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s", (TARGET_SCHEMA,))
        if cursor.fetchone()[0]:
            raise RuntimeError("Destination contains tables; refusing overwrite")
    else:
        cursor.execute("CREATE DATABASE aiia_system CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci")
    cursor.execute("USE aiia_system")


def digest_row(row):
    return repr(tuple(row)).encode("utf-8") + b"\n"


def migrate(*, prepare_only=False):
    load_dotenv(Path(__file__).resolve().parents[1]/".env")
    source_kwargs = system_db_connection_kwargs()
    if (source_kwargs["host"],source_kwargs["port"],source_kwargs["database"]) != ("47.112.132.214",3306,"aiia_system"):
        raise RuntimeError("Source is not the verified old aiia_system")
    source = pymysql.connect(**source_kwargs,connect_timeout=5,read_timeout=60)
    target_wrapper = MonitorDatabase()
    target = target_wrapper.conn
    if (target_wrapper.host,target_wrapper.port) != ("47.94.1.2",3312):
        raise RuntimeError("Destination is not the authorized MySQL service")
    target._read_timeout = 60
    try:
        with source.cursor() as c:
            c.execute("SET time_zone='+08:00'")
            c.execute("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            c.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            c.execute("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME")
            tables = [r[0] for r in c.fetchall()]
            c.execute("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND (ENGINE <> 'InnoDB' OR TABLE_TYPE <> 'BASE TABLE')")
            if c.fetchone()[0]:
                raise RuntimeError("Only audited InnoDB tables are supported")
            for kind in ("TRIGGERS", "ROUTINES", "EVENTS"):
                field = {"TRIGGERS": "TRIGGER_SCHEMA", "ROUTINES": "ROUTINE_SCHEMA", "EVENTS": "EVENT_SCHEMA"}[kind]
                c.execute(f"SELECT COUNT(*) FROM information_schema.{kind} WHERE {field}=DATABASE()")
                if c.fetchone()[0]:
                    raise RuntimeError(f"Source {kind} require a separate migration review")
        with target.cursor() as c:
            prepare_destination(c)
            if prepare_only:
                print(json.dumps({"prepared_schema":TARGET_SCHEMA,"source_tables":len(tables),
                    "copied":False,"connections_changed":False,"credentials_source":"configured kingdomai account"}),flush=True)
                return
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
        with target.cursor() as c:
            c.execute("SET FOREIGN_KEY_CHECKS=1")
            # Current application tables absent from the old schema are added
            # only in the isolated destination, never in either business DB.
            c.execute(USAGE_DDL)
            for statement in _read_statements():
                c.execute(statement)
        print(json.dumps({"verified_tables":len(receipts),"total_rows":sum(r['rows'] for r in receipts),
            "source_unchanged":True,"current_schema_additions":["aiia_request_usage","aiia_scheduled_task","aiia_scheduled_task_run"]}),flush=True)
    finally:
        source.rollback()
        source.close()
        target_wrapper.close_db()


if __name__ == "__main__":
    p=argparse.ArgumentParser(description=__doc__)
    modes=p.add_mutually_exclusive_group()
    modes.add_argument("--apply",action="store_true")
    modes.add_argument("--prepare",action="store_true",help="create only an empty isolated schema; safe before stopping writers")
    args=p.parse_args()
    if args.apply or args.prepare:
        migrate(prepare_only=args.prepare)
    else:
        print("Dry run: source 47.112.132.214/aiia_system -> NEW 47.94.1.2:3312/aiia_system. Stop writers before --apply.")
