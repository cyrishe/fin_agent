"""Shared decision and next-day price records for trading strategies.

The table stores market price observations, not claims of broker execution.
Each strategy supplies its own exit rule; this module implements the current
14:40 strategy's next-trading-day 09:40 minute-close rule.
"""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal


NEXT_0940_CLOSE = "next_trade_day_0940_close"


def record_run(cursor, *, user_id: str, strategy_id: str, strategy_name: str,
               signal_date: str, signal_at: str | None, candidate_count: int | None,
               selection_count: int, model_sha256: str | None,
               environment: dict) -> None:
    """Persist successful daily inference, including days with zero picks."""
    cursor.execute("""SELECT candidate_count,selection_count,model_sha256
        FROM automl_strategy_run WHERE user_id=%s AND strategy_id=%s
          AND signal_date=%s""", (user_id, strategy_id, signal_date))
    existing = cursor.fetchone()
    if existing:
        if (existing["candidate_count"] != candidate_count or
                int(existing["selection_count"]) != selection_count or
                (existing["model_sha256"] is not None and model_sha256 is not None and
                 existing["model_sha256"] != model_sha256)):
            raise ValueError("同一策略和日期的运行事实已冻结，不能覆盖")
        return
    cursor.execute("""INSERT INTO automl_strategy_run (
        user_id,strategy_id,signal_date,strategy_name,signal_at,candidate_count,
        selection_count,model_sha256,environment_json)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (user_id, strategy_id, signal_date, strategy_name, signal_at,
         candidate_count, selection_count, model_sha256,
         json.dumps(environment, ensure_ascii=False, sort_keys=True)))


def record_decisions(cursor, *, user_id: str, strategy_id: str, strategy_name: str,
                     signal_date: str, picks: list[dict], signal_at: str | None,
                     model_sha256: str | None, environment: dict,
                     exit_rule: str = NEXT_0940_CLOSE) -> None:
    """Insert immutable pick identities; omitted actionable means eligible."""
    for pick in picks:
        rank = int(pick["selection_rank"])
        code = str(pick["symbol6"]).zfill(6)
        cursor.execute("""SELECT stock_code,actionable,signal_price,model_sha256
            FROM automl_strategy_execution
            WHERE user_id=%s AND strategy_id=%s AND signal_date=%s
              AND selection_rank=%s""", (user_id, strategy_id, signal_date, rank))
        existing = cursor.fetchone()
        if existing:
            if (existing["stock_code"] != code or
                    int(existing["actionable"]) != int(bool(pick.get("actionable", True))) or
                    (existing["model_sha256"] is not None and model_sha256 is not None and
                     existing["model_sha256"] != model_sha256) or
                    (existing["signal_price"] is not None and pick.get("signal_price") is not None and
                     Decimal(str(existing["signal_price"])) != Decimal(str(pick["signal_price"])))):
                raise ValueError("同一策略、日期和排名的执行决定已冻结，不能覆盖")
            continue
        actionable = int(bool(pick.get("actionable", True)))
        signal_price = pick.get("signal_price")
        entry_price = signal_price if actionable else None
        cursor.execute("""INSERT INTO automl_strategy_execution (
            user_id,strategy_id,signal_date,selection_rank,strategy_name,
            stock_code,stock_name,actionable,signal_at,signal_price,
            entry_at,entry_price,entry_price_basis,exit_rule,model_sha256,
            environment_json,decision_json)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (user_id, strategy_id, signal_date, rank, strategy_name, code,
             pick["name"], actionable, signal_at, signal_price,
             signal_at if actionable else None, entry_price,
             "14:40_signal_quote_proxy" if actionable else None,
             exit_rule if actionable else None, model_sha256,
             json.dumps(environment, ensure_ascii=False, sort_keys=True),
             json.dumps({"predicted_class": pick.get("predicted_class"),
                         "turnover_gate_pass": pick.get("turnover_gate_pass"),
                         "p_lt0": pick.get("p_lt0"), "p_0to1": pick.get("p_0to1"),
                         "p_1to3": pick.get("p_1to3"), "p_ge3": pick.get("p_ge3")},
                        ensure_ascii=False, default=str)))


def pending_next_0940(cursor, *, user_id: str, strategy_id: str,
                      as_of_date: str) -> list[dict]:
    cursor.execute("""SELECT e.signal_date,e.selection_rank,e.stock_code,e.entry_price,
            (SELECT MIN(c.calendar_date) FROM aiia_trade_calendar AS c
             WHERE c.market_code='CN_A' AND c.is_trade_day=1
               AND c.calendar_date>e.signal_date) AS exit_date
        FROM automl_strategy_execution AS e
        WHERE e.user_id=%s AND e.strategy_id=%s AND e.actionable=1
          AND e.exit_price IS NULL AND e.entry_price IS NOT NULL
          AND e.exit_rule=%s AND e.signal_date<%s
        ORDER BY e.signal_date,e.selection_rank""",
        (user_id, strategy_id, NEXT_0940_CLOSE, as_of_date))
    return cursor.fetchall()


def verified_0940_close(cursor, exit_date: str, stock_code: str) -> Decimal | None:
    timestamp = f"{exit_date} 09:40:00"
    cursor.execute("""SELECT latest_price FROM aiia_stock_realtime_minute_snapshot_full
        WHERE trade_date=%s AND LEFT(stk_code,6)=%s AND kline_type='1m'
          AND period_minutes=1 AND bar_end_time=%s AND is_finalized=1
          AND is_fallback=0 AND source_snapshot_time=bar_end_time""",
        (exit_date, stock_code, timestamp))
    bars = cursor.fetchall()
    if len(bars) != 1:
        return None
    price = Decimal(str(bars[0]["latest_price"]))
    return price if price > 0 else None


def settle_previous(cursor, *, user_id: str, strategy_id: str,
                    as_of_date: str) -> list[dict]:
    """Fill matured picks; missing or non-final bars remain pending for retry."""
    completed = []
    for row in pending_next_0940(cursor, user_id=user_id, strategy_id=strategy_id,
                                 as_of_date=as_of_date):
        exit_date = row["exit_date"]
        if exit_date is None or str(exit_date) > as_of_date:
            continue
        date_text = exit_date.isoformat() if hasattr(exit_date, "isoformat") else str(exit_date)
        price = verified_0940_close(cursor, date_text, row["stock_code"])
        if price is None:
            continue
        entry = Decimal(str(row["entry_price"]))
        if entry <= 0:
            raise ValueError("可执行记录的买入代理价必须大于零")
        gross_pct = (price / entry - 1) * 100
        cursor.execute("""UPDATE automl_strategy_execution SET
            exit_at=%s,exit_price=%s,exit_price_basis=%s,gross_return_pct=%s
            WHERE user_id=%s AND strategy_id=%s AND signal_date=%s
              AND selection_rank=%s AND exit_price IS NULL""",
            (datetime.fromisoformat(f"{date_text} 09:40:00"), price,
             "09:40_finalized_minute_close_proxy", gross_pct,
             user_id, strategy_id, row["signal_date"], row["selection_rank"]))
        if cursor.rowcount != 1:
            raise ValueError("待回填交易被并发修改")
        completed.append({"signal_date": str(row["signal_date"]),
                          "selection_rank": row["selection_rank"],
                          "stock_code": row["stock_code"],
                          "exit_date": date_text, "entry_price": str(entry),
                          "exit_price": str(price), "gross_return_pct": str(gross_pct)})
    return completed
