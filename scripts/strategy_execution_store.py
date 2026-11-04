"""Small write contract for the readable strategy run and execution tables.

The strategy supplies its own version, price basis and detail payload. This
module only preserves identity and price facts; it never decides what to buy.
"""
from __future__ import annotations

import json
from decimal import Decimal
import re


def _json(value):
    return None if value is None else json.dumps(value, ensure_ascii=False,
                                                  sort_keys=True, default=str)


def record_daily_run(cursor, *, user_id: str, strategy_name: str,
                     model_version: str, version_desc: str, signal_date: str,
                     signal_at: str | None, candidate_count: int | None,
                     selection_count: int, model_sha256: str | None = None,
                     environment: dict | None = None) -> None:
    """Record a successful daily run, including zero-selection days."""
    if not all((user_id, strategy_name, model_version, version_desc, signal_date)):
        raise ValueError("策略、版本、说明和信号日期不能为空")
    cursor.execute("""SELECT version_desc FROM strategy_daily_run
        WHERE user_id=%s AND strategy_name=%s AND model_version=%s
        LIMIT 1""", (user_id, strategy_name, model_version))
    prior_version = cursor.fetchone()
    if prior_version and prior_version["version_desc"] != version_desc:
        raise ValueError("同一策略的同一版本不能换用另一份版本说明")
    key = (user_id, strategy_name, model_version, signal_date)
    cursor.execute("""SELECT version_desc,candidate_count,selection_count,model_sha256
        FROM strategy_daily_run WHERE user_id=%s AND strategy_name=%s
          AND model_version=%s AND signal_date=%s""", key)
    existing = cursor.fetchone()
    if existing:
        if (existing["version_desc"] != version_desc or
                existing["candidate_count"] != candidate_count or
                int(existing["selection_count"]) != selection_count or
                (existing["model_sha256"] and model_sha256 and
                 existing["model_sha256"] != model_sha256)):
            raise ValueError("同一策略版本和日期的运行事实已存在且不一致")
        return
    cursor.execute("""INSERT INTO strategy_daily_run
        (user_id,strategy_name,model_version,version_desc,signal_date,
         signal_at,candidate_count,selection_count,model_sha256,environment_json)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (*key[:3], version_desc, signal_date, signal_at,
         candidate_count, selection_count, model_sha256, _json(environment)))


def record_selection(cursor, *, user_id: str, strategy_name: str,
                     model_version: str, version_desc: str, signal_date: str,
                     selection_rank: int, stock_code: str, stock_name: str,
                     actionable: bool = True, signal_at: str | None = None,
                     signal_price=None, buy_at: str | None = None,
                     buy_price=None, buy_price_basis: str | None = None,
                     details: dict | None = None) -> None:
    """Insert a pick once; a later trade update does not rewrite the pick."""
    if not all((user_id, strategy_name, model_version, version_desc,
                signal_date, stock_name)) or not re.fullmatch(r"[0-9]{6}", stock_code):
        raise ValueError("选股身份不完整或股票代码不是六位数字")
    key = (user_id, strategy_name, model_version, signal_date, selection_rank)
    cursor.execute("""SELECT version_desc,stock_code,stock_name,signal_price
        FROM strategy_execution WHERE user_id=%s AND strategy_name=%s
          AND model_version=%s AND signal_date=%s AND selection_rank=%s""", key)
    existing = cursor.fetchone()
    if existing:
        if (existing["version_desc"] != version_desc or
                existing["stock_code"] != stock_code or
                existing["stock_name"] != stock_name or
                (existing["signal_price"] is not None and signal_price is not None
                 and Decimal(str(existing["signal_price"])) != Decimal(str(signal_price)))):
            raise ValueError("同一策略版本、日期和排名的选股身份已存在且不一致")
        return
    cursor.execute("""INSERT INTO strategy_execution
        (user_id,strategy_name,model_version,version_desc,signal_date,
         selection_rank,stock_code,stock_name,actionable,signal_at,signal_price,
         buy_at,buy_price,buy_price_basis,details_json)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (user_id, strategy_name, model_version, version_desc, signal_date,
         selection_rank, stock_code, stock_name, int(actionable), signal_at,
         signal_price, buy_at, buy_price, buy_price_basis, _json(details)))
