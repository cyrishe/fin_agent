"""Read-only, anchored daily calculations for bounded conversational analysis.

Published market-wide screening remains in stock_indicator_store. This path
computes a few requested securities from source history, without publishing or
backfilling batches. The cutoff is applied before any calculation.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
import hashlib
import re
from zoneinfo import ZoneInfo

import pandas as pd

from src.services.stock_indicator_daily_job import HISTORY_ANCHOR
from src.services.stock_indicator_store import market_connection
from src.services.technical_indicator_calculator import (
    STANDARD_TECHNICAL_REVISION, calculate_standard_technical_features,
)


def completed_cutoff(as_of: date | None, *, now: datetime | None = None) -> date:
    current = (now or datetime.now(ZoneInfo("Asia/Shanghai"))).astimezone(ZoneInfo("Asia/Shanghai"))
    latest = current.date() if current.time() >= time(16, 30) else current.date() - timedelta(days=1)
    return min(as_of or latest, latest)


def read_source_frame(cursor, code, cutoff):
    """One anchored source contract for numerical data and visual inspection."""
    cursor.execute("""SELECT trade_date, adjopen AS open, adjhigh AS high,
        adjlow AS low, adjclose AS close, close AS raw_close, volume, turn_ratio
        FROM kcrp_stock_price WHERE stk_code=%s AND trade_date>=%s AND trade_date<=%s
        ORDER BY trade_date""", (code, HISTORY_ANCHOR, cutoff))
    source = cursor.fetchall()
    if not source:
        return pd.DataFrame(), {"code": code, "input_rows": 0, "reason": "截止日前无源日线"}
    frame = pd.DataFrame(source)
    frame.index = pd.DatetimeIndex(pd.to_datetime(frame.pop("trade_date")))
    frame = frame.apply(pd.to_numeric).astype(float)
    for name in ("open", "high", "low", "close", "raw_close"):
        frame[name] = frame[name].where(frame[name] > 0)
    meta = {"code": code, "input_start": str(frame.index[0].date()),
        "data_as_of": str(frame.index[-1].date()), "input_rows": len(frame),
        "input_sha256": hashlib.sha256(frame.to_json(date_format="iso", double_precision=15).encode()).hexdigest()}
    return frame, meta


def read_history(codes, *, as_of=None, count=20, patterns=False, connection_factory=market_connection):
    if not isinstance(codes, (list, tuple)) or not 1 <= len(codes) <= 5 or any(
        not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}\.(SH|SZ|BJ)", code) for code in codes
    ):
        raise ValueError("codes must contain 1..5 full exchange-suffixed stock codes")
    maximum = 60 if patterns else 252
    if type(count) is not int or not 1 <= count <= maximum:
        raise ValueError(f"count must be 1..{maximum}")
    cutoff = completed_cutoff(as_of)
    rows, sources = [], []
    with connection_factory() as connection:
        with connection.cursor() as cursor:
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            for code in dict.fromkeys(codes):
                frame, meta = read_source_frame(cursor, code, cutoff)
                if frame.empty:
                    sources.append(meta)
                    continue
                if patterns:
                    part, scan_evidence = pattern_rows(frame, code=code, count=count)
                    meta.update(scan_evidence)
                else:
                    features = calculate_standard_technical_features(frame)
                    result = frame.drop(columns=["raw_close", "turn_ratio"]).join(features).tail(count)
                    result.insert(0, "trade_date", result.index.strftime("%Y-%m-%d"))
                    result.insert(0, "code", code)
                    part = result.astype(object).where(result.notna(), None).to_dict("records")
                rows.extend(part)
                sources.append(meta)
    return {"rows": rows, "evidence": {"source": "kingdomai.kcrp_stock_price",
        "as_of": cutoff.isoformat(), "requested_as_of": as_of.isoformat() if as_of else None,
        "price_basis": "hfq scaled to cutoff raw close" if patterns else "hfq",
        "volume_unit": "股", "formula_revision": STANDARD_TECHNICAL_REVISION,
        "history_anchor": HISTORY_ANCHOR.isoformat(), "sources": sources,
        "scope": "当前源数据修订的已完成日线；不代表历史时点当时已知版本。"}}


def pattern_rows(frame, *, code, count):
    # Pure numerical parts of the existing library; no rendering/model requests.
    from src.experiments.kline_patterns.analysis import scan, context_brief, PATTERNS
    from src.experiments.kline_patterns.catalog import CATALOG
    from src.experiments.kline_patterns.engine import prepare, REVISION
    from src.experiments.kline_patterns.strength import profile, brief, price_boundaries
    last = frame.iloc[-1]
    if pd.isna(last.raw_close) or pd.isna(last.close):
        return [], {"reason": "截止日缺少有效复权与原始收盘，未执行形态扫描"}
    bars = frame.copy()
    bars[["open", "high", "low", "close"]] *= last.raw_close / last.close
    features = prepare(bars)
    candidates, unknown = scan(features, code, count, CATALOG)
    rows = []
    for candidate in candidates:
        definition = PATTERNS[candidate["pattern_id"]]
        strength = profile(definition, candidate, features)
        target = candidate["facts"]["target"]
        at = candidate["at"]
        def price(value):
            return "未知" if pd.isna(value) else f"{value:.4f}"
        levels = [f"同一复权口径缩放至{features.index[-1]:%Y-%m-%d}原始收盘："
            f"信号日收盘{price(features.c.iloc[at])}，截止日收盘{price(features.c.iloc[-1])}。"]
        for name, line in price_boundaries(definition, candidate, features):
            levels.append(f"{name}：信号日{price(line.iloc[at])}，截止日{price(line.iloc[-1])}。")
        rows.append({"code": code, "pattern_id": candidate["pattern_id"],
            "name": definition["name"], "signal_date": candidate["facts"]["signal_date"],
            "start_date": target["start_date"], "end_date": target["end_date"],
            "definition": definition["description"], "formula": definition["formula"],
            "evidence": "".join(levels) + brief(strength), "evolution": strength["evolution"]})
    rows.sort(key=lambda row: (row["signal_date"], row["pattern_id"]))
    return rows, {"pattern_revision": REVISION, "pattern_count": len(CATALOG),
        "candidate_count": len(rows), "unknown_evaluations": unknown,
        "summary": context_brief(features, count),
        "scope": "仅数值定义命中，不是视觉确认、胜率或投资结论；未知检查不计为未命中。"}
