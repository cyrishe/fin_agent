"""Copy the missing shareholder table only; preserve the old DB and existing tables.

Default is a read-only preflight. --apply copies a consistent snapshot into a
staging table, verifies every row, then atomically publishes the missing table.
Credentials are read from .env; never printed. Interrupted staging data is kept.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import pymysql
from dotenv import load_dotenv

TABLE = "kcrp_stock_holdertop1"


def connection(variable, expected, database=None):
    url = urlsplit(os.environ[variable])
    destination = (url.hostname, url.port or 3306, database or url.path.strip("/"))
    if destination != expected:
        raise RuntimeError(f"Unexpected destination for {variable}")
    return pymysql.connect(
        host=destination[0], port=destination[1], database=destination[2],
        user=unquote(url.username), password=unquote(url.password),
        charset="utf8mb4", connect_timeout=10, read_timeout=180, write_timeout=180,
    )


def run(apply=False):
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    source = connection("BUSINESS_DB_URL", ("47.113.122.220", 3306, "kingdomai"))
    target = connection("PLATFORM_DB_URL", ("47.94.1.2", 3312, "kingdomai"), "kingdomai")
    stage = f"{TABLE}_sync_{uuid4().hex[:10]}"
    try:
        with source.cursor() as c:
            c.execute("SET time_zone='+08:00'")
            c.execute("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            c.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            c.execute(f"SHOW CREATE TABLE `{TABLE}`")
            ddl = c.fetchone()[1]
            if not ddl.startswith(f"CREATE TABLE `{TABLE}`") or "ENGINE=InnoDB" not in ddl:
                raise RuntimeError("Unexpected source schema")
            c.execute(f"SHOW COLUMNS FROM `{TABLE}`")
            columns = [row[0] for row in c.fetchall()]
            c.execute(f"SHOW INDEX FROM `{TABLE}` WHERE Key_name='PRIMARY'")
            primary = [r[4] for r in sorted(c.fetchall(), key=lambda r: r[3])]
            if not primary:
                raise RuntimeError("Primary key required for read-back verification")
            c.execute(f"SELECT COUNT(*) FROM `{TABLE}`")
            expected_count = c.fetchone()[0]
        with target.cursor() as c:
            c.execute("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (TABLE,))
            if c.fetchone()[0]:
                raise RuntimeError("Destination table already exists; refusing overwrite")
            c.execute("SET time_zone='+08:00'")
            print(json.dumps({"table": TABLE, "source_rows": expected_count, "apply": apply}), flush=True)
            if not apply:
                return
            c.execute(ddl.replace(f"CREATE TABLE `{TABLE}`", f"CREATE TABLE `{stage}`", 1))
        fields = ",".join(f"`{name}`" for name in columns)
        order = ",".join(f"`{name}`" for name in primary)
        insert = f"INSERT INTO `{stage}` ({fields}) VALUES ({','.join(['%s'] * len(columns))})"
        expected_hash = hashlib.sha256()
        copied = 0
        with source.cursor(pymysql.cursors.SSCursor) as src, target.cursor() as dst:
            src.execute(f"SELECT {fields} FROM `{TABLE}` ORDER BY {order}")
            while rows := src.fetchmany(2000):
                for row in rows:
                    expected_hash.update(repr(tuple(row)).encode("utf-8") + b"\n")
                dst.executemany(insert, rows)
                target.commit()
                copied += len(rows)
                if copied % 100000 == 0:
                    print(json.dumps({"copied": copied}), flush=True)
        actual_hash = hashlib.sha256()
        verified = 0
        with target.cursor(pymysql.cursors.SSCursor) as c:
            c.execute(f"SELECT {fields} FROM `{stage}` ORDER BY {order}")
            for row in c:
                actual_hash.update(repr(tuple(row)).encode("utf-8") + b"\n")
                verified += 1
        if expected_count != copied or copied != verified or expected_hash.digest() != actual_hash.digest():
            raise RuntimeError(f"Verification failed; staging table retained: {stage}")
        with target.cursor() as c:
            # RENAME fails safely if a concurrent writer has created TABLE.
            c.execute(f"RENAME TABLE `{stage}` TO `{TABLE}`")
        print(json.dumps({"table": TABLE, "verified_rows": verified,
                          "sha256": actual_hash.hexdigest(), "published": True,
                          "source_unchanged": True}), flush=True)
    finally:
        source.rollback()
        source.close()
        target.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    run(parser.parse_args().apply)
