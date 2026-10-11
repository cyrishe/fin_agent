"""Market DB access for shared stock indicators; schema creation is explicit."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile

import pymysql

from src.services.technical_indicator_calculator import STANDARD_TECHNICAL_FEATURES, STANDARD_TECHNICAL_REVISION
from src.utils.mysql_utils import StockInfoDbUtils

BATCH_TABLE = "aiia_stock_indicator_daily_batch"
DAILY_TABLE = "aiia_stock_indicator_daily"
SCHEMA_TABLES = (BATCH_TABLE, DAILY_TABLE, "aiia_stock_lhb_event", "aiia_stock_lhb_seat")
ROOT = Path(__file__).resolve().parents[2]


class IndicatorConnection(StockInfoDbUtils):
    def connect_db(self):
        self.conn = pymysql.connect(
            host=self.host, port=self.port, user=self.user, password=self.password,
            database=self.database, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=8, read_timeout=90, write_timeout=90, autocommit=False,
        )


@contextmanager
def market_connection():
    db = IndicatorConnection(database="kingdomai")
    try:
        with db.conn.cursor() as cursor:
            cursor.execute("SET SESSION time_zone='+00:00'")
            cursor.execute("SET SESSION MAX_EXECUTION_TIME=60000")
        yield db.conn
    finally:
        db.close_db()


def apply_schema(connection, *, expected_server_uuid: str) -> dict:
    """Create only the four owned tables on the explicitly selected instance."""
    with connection.cursor() as cur:
        cur.execute("SELECT DATABASE() AS db, @@server_uuid AS server_uuid")
        target = cur.fetchone()
        if target["db"] != "kingdomai" or target["server_uuid"] != expected_server_uuid:
            raise ValueError("schema target does not match the verified kingdomai instance")
        sql = (ROOT / "docs/sql/create_aiia_stock_indicator_and_lhb.sql").read_text()
        statements = [s.strip() for s in sql.split(";") if s.strip()]
        for statement in statements:
            cur.execute(statement)
        for table in SCHEMA_TABLES:
            cur.execute(f"SHOW COLUMNS FROM {table}")
            fields = {row["Field"] for row in cur.fetchall()}
            if table == DAILY_TABLE and not set(STANDARD_TECHNICAL_FEATURES).issubset(fields):
                raise ValueError("existing daily indicator table has an incompatible schema")
    return {"database": target["db"], "server_uuid": target["server_uuid"], "tables": list(SCHEMA_TABLES)}


def save_input_object(root: Path, payload) -> str:
    """Content-addressed, compressed input chunks; unchanged history is shared."""
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    digest = hashlib.sha256(raw).hexdigest()
    relative = f"objects/{digest[:2]}/{digest}.json.gz"
    target = root / relative
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".indicator-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(gzip.compress(raw, mtime=0))
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return relative


def publish_daily_batch(connection, *, batch: dict, rows: list[dict]) -> dict:
    """One transaction publishes a computed cross-section; retries reuse its ID."""
    if not rows or len(rows) > batch["requested_count"]:
        raise ValueError("cannot publish an empty or oversized batch")
    batch = {"started_at": datetime.now(timezone.utc).replace(tzinfo=None), **batch}
    columns = ("batch_id", "stk_code", "input_start_time", "input_end_time",
               "input_fingerprint", "bar_count", *STANDARD_TECHNICAL_FEATURES)
    try:
        connection.begin()
        with connection.cursor() as cur:
            cur.execute(f"SELECT published_at,written_count FROM {BATCH_TABLE} WHERE batch_id=%s FOR UPDATE", (batch["batch_id"],))
            existing = cur.fetchone()
            if existing and existing["published_at"] is not None:
                connection.rollback()
                return {"batch_id": batch["batch_id"], "written_count": existing["written_count"], "reused": True}
            # Atomic transactions leave no partial rows; a prior unpublished draft
            # may be retried with the same deterministic input identity.
            cur.execute(f"""INSERT INTO {BATCH_TABLE}
                (batch_id,universe_key,trade_date,formula_revision,price_basis,source_name,
                 source_watermark,input_manifest_ref,requested_count,written_count,started_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s)
                ON DUPLICATE KEY UPDATE batch_id=VALUES(batch_id)""",
                tuple(batch[key] for key in ("batch_id", "universe_key", "trade_date", "formula_revision",
                    "price_basis", "source_name", "source_watermark", "input_manifest_ref", "requested_count", "started_at")))
            cur.execute(f"DELETE FROM {DAILY_TABLE} WHERE batch_id=%s", (batch["batch_id"],))
            sql = f"INSERT INTO {DAILY_TABLE} ({','.join(columns)}) VALUES ({','.join(['%s'] * len(columns))})"
            for offset in range(0, len(rows), 500):
                cur.executemany(sql, [tuple(batch["batch_id"] if key == "batch_id" else row[key] for key in columns)
                                     for row in rows[offset:offset + 500]])
            cur.execute(f"UPDATE {BATCH_TABLE} SET written_count=%s,published_at=UTC_TIMESTAMP(6) WHERE batch_id=%s", (len(rows), batch["batch_id"]))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return {"batch_id": batch["batch_id"], "written_count": len(rows), "reused": False}


def read_daily_indicators(connection, *, as_of: date, codes=(), count: int = 1,
                          universe_key: str = "cn_a_daily_source", limit: int = 10000) -> dict:
    """Latest published revision per date, a bounded date window, then securities."""
    if not 1 <= count <= 252 or not 1 <= limit <= 100000:
        raise ValueError("count must be 1..252 and limit 1..100000")
    params = [universe_key, as_of, STANDARD_TECHNICAL_REVISION, "hfq", count]
    code_sql = ""
    if codes:
        code_sql = " AND d.stk_code IN (" + ",".join(["%s"] * len(codes)) + ")"
        params.extend(codes)
    params.append(limit + 1)
    with connection.cursor() as cur:
        cur.execute(f"""WITH ranked AS (
            SELECT b.*,ROW_NUMBER() OVER (PARTITION BY trade_date ORDER BY published_at DESC,batch_id DESC) AS rn
            FROM {BATCH_TABLE} b WHERE universe_key=%s AND trade_date<=%s
              AND formula_revision=%s AND price_basis=%s AND published_at IS NOT NULL
        ), chosen AS (SELECT * FROM ranked WHERE rn=1 ORDER BY trade_date DESC LIMIT %s)
        SELECT b.trade_date,b.formula_revision,b.price_basis,b.published_at,b.requested_count,b.written_count,
               d.* FROM chosen b JOIN {DAILY_TABLE} d ON d.batch_id=b.batch_id
        WHERE 1=1 {code_sql} ORDER BY b.trade_date,d.stk_code LIMIT %s""", tuple(params))
        rows = list(cur.fetchall())
    if len(rows) > limit:
        raise ValueError("result exceeds limit; narrow the securities or date count")
    return {"rows": rows, "row_count": len(rows), "universe_key": universe_key,
            "formula_revision": STANDARD_TECHNICAL_REVISION, "price_basis": "hfq"}
