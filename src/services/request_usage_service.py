"""Completed-request accounting, separate from business outputs and agent turns."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import json
import logging
import threading
import time
from typing import Mapping
from zoneinfo import ZoneInfo

import pymysql

from src.utils.system_db_utils import system_db_connection_kwargs

TZ = ZoneInfo("Asia/Shanghai")
LOG = logging.getLogger(__name__)
TABLE = "aiia_request_usage"
DDL = f"""CREATE TABLE IF NOT EXISTS {TABLE} (
    request_id VARCHAR(96) NOT NULL PRIMARY KEY,
    channel VARCHAR(16) NOT NULL,
    finished_at DATETIME(6) NOT NULL,
    total_tokens BIGINT UNSIGNED NULL,
    succeeded TINYINT NOT NULL,
    KEY idx_usage_day_channel (finished_at, channel)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Completed external requests; NULL means unreported usage'"""


def connect():
    conn = pymysql.connect(**system_db_connection_kwargs(), connect_timeout=3,
                           read_timeout=5, write_timeout=5, autocommit=True)
    with conn.cursor() as c:
        c.execute("SET time_zone='+08:00'")
    return conn


def total_tokens(usage) -> int | None:
    """Do not add cache/reasoning subsets twice. Preserve missing vs reported zero."""
    if not isinstance(usage, Mapping) or not usage:
        return None
    def number(key):
        v = usage.get(key)
        if isinstance(v, bool) or v is None:
            return None
        try:
            return max(0, int(v))
        except (TypeError, ValueError):
            return None
    if "accounting_total_tokens" in usage:
        return number("accounting_total_tokens")
    # Harness inputTokens excludes cache reads, but cumulative context includes them.
    if "cumulative_context_tokens" in usage:
        prompt, output = number("cumulative_context_tokens"), number("completion_tokens")
        return prompt + output if prompt is not None and output is not None else None
    total = number("total_tokens")
    if total is not None:
        return total
    prompt = number("prompt_tokens")
    if prompt is None:
        prompt = number("input_tokens")
    output = number("completion_tokens")
    if output is None:
        output = number("output_tokens")
    if prompt is None or output is None:
        return None
    # Anthropic cache fields are additive; Codex cached_input_tokens is a subset.
    return prompt + output + (number("cache_read_input_tokens") or 0) + (number("cache_creation_input_tokens") or 0)


def record_request(*, request_id, channel, usage, succeeded, finished_at=None):
    """One ID per accepted execution; duplicate completion replaces, never increments."""
    if channel not in {"mcp", "http_api"}:
        raise ValueError("External request channel must be mcp or http_api")
    try:
        conn = connect()
        try:
            finished = (finished_at or datetime.now(TZ)).astimezone(TZ).replace(tzinfo=None)
            with conn.cursor() as c:
                c.execute(f"""INSERT INTO {TABLE} (request_id,channel,finished_at,total_tokens,succeeded)
                    VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE
                    total_tokens=VALUES(total_tokens), succeeded=VALUES(succeeded)""",
                    (request_id, channel, finished, total_tokens(usage), int(bool(succeeded))))
        finally:
            conn.close()
    except Exception:
        # Accounting failure must be visible operationally, not a failed user answer.
        LOG.exception("Request usage persistence failed: %s", request_id)


def summarize(rows, *, today: date, days: int):
    daily = {}
    for n in range(days):
        day = (today-timedelta(days=n)).isoformat()
        daily[day] = {"date": day, **{channel: {"requests": 0, "token_requests": 0,
            "total_tokens": 0, "unknown_usage_requests": 0, "average_tokens": None}
            for channel in ("chat", "mcp", "http_api")}}
    for day, channel, count, known, tokens in rows:
        item = daily.get(str(day))
        if item is None or channel not in ("chat", "mcp", "http_api"):
            continue
        count, known, tokens = int(count), int(known), int(tokens or 0)
        item[channel] = {"requests": count, "token_requests": known,
            "total_tokens": tokens if known or count == 0 else None,
            "unknown_usage_requests": count-known,
            "average_tokens": round(tokens/known, 1) if known else None}
    for item in daily.values():
        channels = [item[c] for c in ("chat", "mcp", "http_api")]
        item["known_total_tokens"] = sum(c["total_tokens"] or 0 for c in channels)
        item["unknown_usage_requests"] = sum(c["unknown_usage_requests"] for c in channels)
        item["total_tokens"] = item["known_total_tokens"] if not item["unknown_usage_requests"] else None
    return list(daily.values())


class DailyUsageService:
    def __init__(self, db_connect=connect):
        self.db_connect = db_connect
        self.cache = {}
        self.lock = threading.Lock()

    def daily(self, days=30):
        days = max(1, min(90, int(days)))
        now = datetime.now(TZ)
        with self.lock:
            cached = self.cache.get(days)
            if cached and time.monotonic()-cached[0] < 60 and cached[1]["today"] == now.date().isoformat():
                return cached[1]
            conn = self.db_connect()
            try:
                start = datetime.combine(now.date()-timedelta(days=days-1), datetime.min.time())
                end = datetime.combine(now.date()+timedelta(days=1), datetime.min.time())
                with conn.cursor() as c:
                    c.execute("SET SESSION MAX_EXECUTION_TIME=3000")
                    # Existing conversation records are authoritative: no user/profile
                    # cumulative counters and no second copy of a chat request.
                    c.execute("""SELECT DATE(finished_at),token_usage_json FROM aiia_runtime_turn
                        WHERE finished_at >= %s AND finished_at < %s""", (start,end))
                    grouped = {}
                    for day, raw in c.fetchall():
                        try:
                            usage = json.loads(raw) if isinstance(raw, str) else raw
                        except (ValueError, TypeError):
                            usage = None
                        value = total_tokens(usage)
                        # Legacy normalizer wrote all-zero objects for missing usage.
                        if value == 0 and "accounting_total_tokens" not in (usage or {}):
                            value = None
                        counters = grouped.setdefault(day, [0,0,0])
                        counters[0] += 1
                        counters[1] += int(value is not None)
                        counters[2] += value or 0
                    rows = [(d,"chat",*counts) for d,counts in grouped.items()]
                    c.execute(f"""SELECT DATE(finished_at),channel,COUNT(*),COUNT(total_tokens),SUM(total_tokens)
                        FROM {TABLE} WHERE finished_at >= %s AND finished_at < %s
                        GROUP BY DATE(finished_at),channel""", (start,end))
                    rows.extend(c.fetchall())
                    c.execute(f"SELECT MIN(finished_at) FROM {TABLE}")
                    first = c.fetchone()[0]
                    c.execute("SELECT CREATE_TIME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (TABLE,))
                    collected_since = c.fetchone()[0]
            finally:
                conn.close()
            result = {"today": now.date().isoformat(), "checked_at": now.isoformat(),
                "timezone": "Asia/Shanghai", "external_usage_since": str(first) if first else None,
                "collection_started_at": str(collected_since) if collected_since else None,
                "days": summarize(rows,today=now.date(),days=days),
                "note": "按完整请求完成日统计；内部轮次不计为请求。Token为已上报用量（缓存如有上报则包含），非费用。缺失用量不计零，均值只除以有用量的请求数；HTTP API单列，总量不重复相加。历史未接入的MCP/API不可回溯。"}
            for day in result["days"]:
                if not collected_since or day["date"] <= collected_since.date().isoformat():
                    day["total_tokens"] = None
                    day["external_history_incomplete"] = True
                    if not collected_since or day["date"] < collected_since.date().isoformat():
                        for channel in ("mcp", "http_api"):
                            day[channel] = {key: None for key in day[channel]}
            self.cache[days] = (time.monotonic(),result)
            return result
