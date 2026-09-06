"""Copy the configured system DB to the authorized server; source is read-only.

Default/--prepare: read-only preflight for the existing stock_agent schema.
--apply --writers-stopped: copy and verify after stopping all application writers.
No source DDL/DML; unrelated destination tables are preserved, never overwritten.
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
from src.finance_api.data_status import MonitorHotDatabase
from src.services.request_usage_service import DDL as USAGE_DDL
from scripts.manage_scheduled_task_schema import _read_statements

TARGET_SCHEMA = "stock_agent"
ADDITIONAL_TABLES = {"aiia_request_usage", "aiia_scheduled_task", "aiia_scheduled_task_run"}


def target_table_name(name):
    if name == "leader_risk_state":
        return "aiia_legacy_leader_risk_state"
    if not name.startswith("aiia_"):
        raise RuntimeError(f"Unreviewed source system table: {name}")
    return name


def prepare_destination(cursor, source_tables, constraint_names=()):
    """Check our table namespace only; never modify unrelated shared-schema data."""
    cursor.execute("SELECT DATABASE()")
    if cursor.fetchone()[0] != TARGET_SCHEMA:
        raise RuntimeError("Destination is not the authorized stock_agent schema")
    cursor.execute("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE()")
    targets = {target_table_name(name) for name in source_tables} | ADDITIONAL_TABLES
    conflicts = {row[0] for row in cursor.fetchall()} & targets
    if conflicts:
        raise RuntimeError("Destination contains migration tables; refusing overwrite: " + ", ".join(sorted(conflicts)))
    cursor.execute("SELECT CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_TYPE='FOREIGN KEY'")
    if {row[0] for row in cursor.fetchall()} & set(constraint_names):
        raise RuntimeError("Destination foreign-key names conflict")


def renamed_ddl(ddl, source_table):
    old = f"CREATE TABLE `{source_table}`"
    if not ddl.startswith(old):
        raise RuntimeError(f"Unexpected SHOW CREATE TABLE: {source_table}")
    return f"CREATE TABLE `{target_table_name(source_table)}`" + ddl[len(old):]


def digest_row(row):
    return repr(tuple(row)).encode("utf-8") + b"\n"


def migrate(*, prepare_only=True):
    load_dotenv(Path(__file__).resolve().parents[1]/".env")
    source_kwargs = system_db_connection_kwargs()
    if (source_kwargs["host"],source_kwargs["port"],source_kwargs["database"]) != ("47.112.132.214",3306,"aiia_system"):
        raise RuntimeError("Source is not the verified old aiia_system")
    source = pymysql.connect(**source_kwargs,connect_timeout=5,read_timeout=60)
    target_wrapper = MonitorHotDatabase()
    target = target_wrapper.conn
    if (target_wrapper.host,target_wrapper.port,target_wrapper.database) != ("47.94.1.2",3312,TARGET_SCHEMA):
        raise RuntimeError("Destination is not the authorized MySQL service")
    target._read_timeout = 60
    try:
        with source.cursor() as c:
            c.execute("SET time_zone='+08:00'")
            c.execute("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            c.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            c.execute("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME")
            tables = [r[0] for r in c.fetchall()]
            if not tables:
                raise RuntimeError("Source contains no system tables")
            c.execute("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND (ENGINE <> 'InnoDB' OR TABLE_TYPE <> 'BASE TABLE')")
            if c.fetchone()[0]:
                raise RuntimeError("Only audited InnoDB tables are supported")
            for kind in ("TRIGGERS", "ROUTINES", "EVENTS"):
                field = {"TRIGGERS": "TRIGGER_SCHEMA", "ROUTINES": "ROUTINE_SCHEMA", "EVENTS": "EVENT_SCHEMA"}[kind]
                c.execute(f"SELECT COUNT(*) FROM information_schema.{kind} WHERE {field}=DATABASE()")
                if c.fetchone()[0]:
                    raise RuntimeError(f"Source {kind} require a separate migration review")
            c.execute("SELECT CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_TYPE='FOREIGN KEY'")
            constraints = [r[0] for r in c.fetchall()] + ["fk_scheduled_run_schedule"]
            # Audit every table before any destination writes.
            definitions = {}
            for table in tables:
                c.execute(f"SHOW CREATE TABLE `{table}`")
                ddl = renamed_ddl(c.fetchone()[1], table)
                c.execute(f"SHOW COLUMNS FROM `{table}`")
                columns = [r[0] for r in c.fetchall()]
                c.execute(f"SHOW INDEX FROM `{table}` WHERE Key_name='PRIMARY'")
                primary = [r[4] for r in sorted(c.fetchall(),key=lambda r:r[3])]
                if not primary:
                    raise RuntimeError(f"No primary key for deterministic verification: {table}")
                definitions[table] = (ddl,columns,primary)
        with target.cursor() as c:
            prepare_destination(c,tables,constraints)
            if prepare_only:
                print(json.dumps({"preflight_ok":True,"destination":f"47.94.1.2:3312/{TARGET_SCHEMA}","source_tables":len(tables),
                    "renames":{t:target_table_name(t) for t in tables if t!=target_table_name(t)},
                    "new_tables":sorted(ADDITIONAL_TABLES),"copied":False,"connections_changed":False,
                    "credentials_source":"configured stock_agent account"}),flush=True)
                return
            c.execute("SET time_zone='+08:00'")
            c.execute("SET FOREIGN_KEY_CHECKS=0")
        for table in tables:
            with target.cursor() as c:
                c.execute(definitions[table][0])
        receipts = []
        for table in tables:
            _,columns,primary = definitions[table]
            target_table = target_table_name(table)
            order = ",".join(f"`{v}`" for v in primary)
            fields = ",".join(f"`{v}`" for v in columns)
            query = f"SELECT {fields} FROM `{table}` ORDER BY {order}"
            target_query = f"SELECT {fields} FROM `{target_table}` ORDER BY {order}"
            insert = f"INSERT INTO `{target_table}` ({fields}) VALUES ({','.join(['%s']*len(columns))})"
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
                c.execute(target_query)
                for row in c:
                    actual.update(digest_row(row))
                    verified += 1
            if count != verified or expected.digest() != actual.digest():
                raise RuntimeError(f"Read-back verification failed: {table}")
            receipts.append({"source_table":table,"table":target_table,"rows":count,"sha256":actual.hexdigest()})
            print(json.dumps(receipts[-1]),flush=True)
        with target.cursor() as c:
            c.execute("SET FOREIGN_KEY_CHECKS=1")
            # Current application tables absent from the old schema are added
            # only in the explicitly selected destination.
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
    modes.add_argument("--prepare",action="store_true",help="read-only preflight; default")
    p.add_argument("--writers-stopped",action="store_true",help="confirm all source application writers are stopped")
    args=p.parse_args()
    if args.apply and not args.writers_stopped:
        p.error("--apply requires --writers-stopped; stop all source writers first")
    migrate(prepare_only=not args.apply)
