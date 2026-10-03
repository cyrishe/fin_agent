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


ADDITIVE_COLUMNS = {
    "aiia_scheduled_task": {
        "run_at": "datetime(6) DEFAULT NULL", "budget_json": "json DEFAULT NULL",
        "source_ref": "varchar(512) DEFAULT NULL", "initial_run_id": "varchar(64) DEFAULT NULL",
        "request_fingerprint": "varchar(64) DEFAULT NULL",
    },
    "aiia_scheduled_task_run": {
        "budget_json": "json DEFAULT NULL", "progress_json": "json DEFAULT NULL",
        "checkpoint_json": "json DEFAULT NULL", "lease_token": "varchar(64) DEFAULT NULL",
        "deadline_at": "datetime(6) DEFAULT NULL", "cancel_requested_at": "datetime(6) DEFAULT NULL",
        "request_key": "varchar(128) DEFAULT NULL",
    },
}
for _table, _columns in ADDITIVE_COLUMNS.items():
    REQUIRED_COLUMNS[_table].update(_columns)


def _upgrade_existing(db):
    missing = _schema_status(db)
    with db.cursor() as cursor:
        for table, columns in ADDITIVE_COLUMNS.items():
            for name, definition in columns.items():
                if name in missing.get(table, []):
                    cursor.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        if "cron_expr must allow NULL" in missing.get("aiia_scheduled_task", []):
            cursor.execute("ALTER TABLE aiia_scheduled_task MODIFY cron_expr varchar(128) DEFAULT NULL")
        cursor.execute("SHOW INDEX FROM aiia_scheduled_task_run WHERE Key_name='uk_task_run_request'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE aiia_scheduled_task_run ADD UNIQUE KEY uk_task_run_request (schedule_id,request_key)")


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
                SELECT COLUMN_NAME, IS_NULLABLE
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s
                """,
                (database_name, table_name),
            )
            columns = {str(row["COLUMN_NAME"]): row for row in cursor.fetchall()}
            absent = sorted(required - set(columns))
            if table_name == "aiia_scheduled_task" and "cron_expr" in columns and columns["cron_expr"]["IS_NULLABLE"] != "YES":
                absent.append("cron_expr must allow NULL")
            if table_name == "aiia_scheduled_task_run" and columns:
                cursor.execute("""SELECT COLUMN_NAME, NON_UNIQUE FROM INFORMATION_SCHEMA.STATISTICS
                    WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND INDEX_NAME='uk_task_run_request'
                    ORDER BY SEQ_IN_INDEX""", (database_name, table_name))
                index = cursor.fetchall()
                if [row["COLUMN_NAME"] for row in index] != ["schedule_id", "request_key"] or any(row["NON_UNIQUE"] for row in index):
                    absent.append("uk_task_run_request unique index")
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
        help="create missing tables and apply reviewed additive task-runtime columns (stop old workers first)",
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
            _upgrade_existing(db)
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
