from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pymysql

from src.utils.system_db_utils import connect_system_db


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = (
    ROOT / "docs/sql/create_aiia_scheduled_task.sql",
    ROOT / "docs/sql/create_aiia_scheduled_task_run.sql",
)
REQUIRED_COLUMNS = {
    "aiia_scheduled_task": {
        "schedule_id",
        "owner_user_id",
        "requirement_brief",
        "timezone",
        "cron_expr",
        "execution_plan_json",
        "enabled",
        "revision_no",
        "next_run_at",
        "idempotency_key",
    },
    "aiia_scheduled_task_run": {
        "run_id",
        "schedule_id",
        "owner_user_id",
        "schedule_revision_no",
        "execution_plan_json",
        "scheduled_for",
        "status",
        "lease_owner",
        "lease_until",
        "result_json",
        "error_text",
    },
}


def _read_statements() -> list[str]:
    statements: list[str] = []
    for path in MIGRATIONS:
        source = path.read_text(encoding="utf-8").strip()
        if not source:
            raise RuntimeError(f"迁移文件为空：{path}")
        statements.extend(
            statement.strip()
            for statement in source.split(";")
            if statement.strip()
        )
    return statements


def _schema_status(db: Any) -> dict[str, list[str]]:
    missing: dict[str, list[str]] = {}
    with db.cursor() as cursor:
        cursor.execute("SELECT DATABASE() AS database_name")
        database_name = str(cursor.fetchone()["database_name"] or "")
        for table_name, required in REQUIRED_COLUMNS.items():
            cursor.execute(
                """
                SELECT COLUMN_NAME
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s
                """,
                (database_name, table_name),
            )
            present = {str(row["COLUMN_NAME"]) for row in cursor.fetchall()}
            absent = sorted(required - present)
            if absent:
                missing[table_name] = absent
    return missing


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check or initialize Fin Agent scheduled-task tables.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="execute the two additive CREATE TABLE IF NOT EXISTS migrations",
    )
    args = parser.parse_args()

    db = connect_system_db(
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )
    try:
        if args.apply:
            with db.cursor() as cursor:
                for statement in _read_statements():
                    cursor.execute(statement)
            db.commit()
        missing = _schema_status(db)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    if missing:
        details = "; ".join(
            f"{table}: {', '.join(columns)}"
            for table, columns in missing.items()
        )
        raise SystemExit(
            f"scheduled-task schema is not ready ({details}); "
            "review the SQL files, then rerun with --apply"
        )
    action = "initialized and verified" if args.apply else "verified"
    print(f"scheduled-task schema {action}")


if __name__ == "__main__":
    main()
