"""MySQL connection used by the read-only structured report provider."""
from __future__ import annotations

import os
import re
from urllib.parse import unquote, urlparse

import pymysql
from pymysql.constants import FIELD_TYPE
from pymysql.converters import conversions


def _mysql_utf8mb4_kwargs():
    return {"charset": "utf8mb4", "use_unicode": True,
            "init_command": "SET NAMES utf8mb4 COLLATE utf8mb4_general_ci"}


class StockInfoDbUtils:
    """Connect using the existing financial database configuration."""

    def __init__(self,
                 host=None,
                 user=None,
                 password=None,
                 database="kingdomai",
                 connect_timeout=30,  # 延长连接超时时间
                 read_timeout=60 ,     # 延长查询超时时间
                 port=None):

        self.conv = conversions.copy()
        # 覆盖DECIMAL类型转换规则
        self.conv[FIELD_TYPE.DECIMAL] = float
        self.conv[FIELD_TYPE.NEWDECIMAL] = float
        configured_host = str(os.getenv("KINGDOMAI_DB_HOST") or "").strip()
        configured_user = str(os.getenv("KINGDOMAI_DB_USER") or "").strip()
        configured_password = str(os.getenv("KINGDOMAI_DB_PASSWORD") or "")
        configured_port = str(os.getenv("KINGDOMAI_DB_PORT") or "").strip()
        try:
            environment_port = int(configured_port) if configured_port else None
        except ValueError:
            environment_port = None
        resolved_host = host or configured_host or "127.0.0.1"
        resolved_user = user or configured_user
        resolved_password = password if password is not None else configured_password
        resolved_database = database
        resolved_port = port or environment_port or 3306
        credential_source_name = str(
            os.getenv("KINGDOMAI_DB_CREDENTIAL_SOURCE") or ""
        ).strip()
        credential_source_url = (
            str(os.getenv(credential_source_name) or "").strip()
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", credential_source_name)
            else ""
        )
        configured_database_url = str(os.getenv("KINGDOMAI_DB_URL") or "").strip()
        database_url = (
            configured_database_url
            or credential_source_url
            or str(os.getenv("BUSINESS_DB_URL") or "").strip()
        )
        if database_url:
            parsed = urlparse(database_url.replace("mysql+pymysql://", "mysql://", 1))
            url_database = (parsed.path or "/").lstrip("/")
            supplies_credentials_only = bool(
                credential_source_url
                and not configured_database_url
                and database_url == credential_source_url
            )
            if (
                parsed.scheme == "mysql"
                and parsed.hostname
                and (url_database == database or supplies_credentials_only)
            ):
                if host is None and not configured_host:
                    resolved_host = parsed.hostname
                if user is None and not configured_user:
                    resolved_user = unquote(parsed.username or resolved_user)
                if password is None and not configured_password:
                    resolved_password = unquote(parsed.password or "")
                resolved_database = database
                if port is None and environment_port is None:
                    resolved_port = parsed.port or 3306
        self.host = resolved_host
        self.user = resolved_user
        self.password = resolved_password
        self.database = resolved_database
        self.port = resolved_port
        self.conn = None
        self.connect_db()



    def connect_db(self):
        """
        建立数据库连接
        """
        self.conn = pymysql.connect(
            host=self.host,
            user=self.user,
            password=self.password,
            database=self.database,
            port=self.port,
            conv=self.conv,
            **_mysql_utf8mb4_kwargs(),
        )

    def close_db(self):
        """
        关闭数据库连接
        """
        if self.conn:
            self.conn.close()
            self.conn = None
