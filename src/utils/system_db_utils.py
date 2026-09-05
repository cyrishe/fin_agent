from __future__ import annotations

import os
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urlparse

import pymysql


def system_db_connection_kwargs(*, raw_url: Optional[str] = None) -> dict[str, Any]:
    """Parse the isolated system database URL without a business-DB fallback."""

    value = str(raw_url if raw_url is not None else os.getenv("SYSTEM_DB_URL") or "").strip()
    if not value:
        raise RuntimeError("SYSTEM_DB_URL is required for system storage")
    parsed = urlparse(value.replace("mysql+pymysql://", "mysql://", 1))
    database = (parsed.path or "/").lstrip("/")
    if parsed.scheme != "mysql" or not parsed.hostname or not database:
        raise RuntimeError("SYSTEM_DB_URL must be a MySQL URL with a database name")
    query = parse_qs(parsed.query)
    return {
        "host": parsed.hostname,
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "database": database,
        "port": parsed.port or 3306,
        "charset": str((query.get("charset") or ["utf8mb4"])[0] or "utf8mb4"),
        "use_unicode": True,
        "init_command": "SET NAMES utf8mb4 COLLATE utf8mb4_general_ci",
    }


def connect_system_db(
    *,
    raw_url: Optional[str] = None,
    cursorclass: Optional[type[pymysql.cursors.Cursor]] = None,
    autocommit: bool = False,
) -> Any:
    kwargs = system_db_connection_kwargs(raw_url=raw_url)
    if cursorclass is not None:
        kwargs["cursorclass"] = cursorclass
    kwargs["autocommit"] = bool(autocommit)
    return pymysql.connect(**kwargs)


class SystemDbUtils:
    """Compatibility wrapper for fin_agent system-state services."""

    def __init__(self, *, raw_url: Optional[str] = None) -> None:
        self.raw_url = raw_url
        self.conn = connect_system_db(raw_url=raw_url)

    def close_db(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None

    def reconnect(self) -> None:
        self.close_db()
        self.conn = connect_system_db(raw_url=self.raw_url)
