"""Shared EOD computation from existing adjusted daily prices, without quote APIs."""
from __future__ import annotations

from datetime import date, datetime, time as day_time
import hashlib
import json
from pathlib import Path
import re
from time import monotonic
from zoneinfo import ZoneInfo

import pandas as pd

from src.services.stock_indicator_store import market_connection, publish_daily_batch, save_input_object
from src.services.technical_indicator_calculator import STANDARD_TECHNICAL_REVISION, calculate_standard_technical_features

HISTORY_ANCHOR = date(2018, 1, 2)
UNIVERSE_KEY = "cn_a_daily_source"
LOCK_NAME = "fin_agent:stock_indicator_daily"
PRICE_COLUMNS = ("close", "high", "low", "volume")


def compute_security(rows: list[dict], *, code: str, trade_date: date, artifact_root: Path) -> tuple[dict, dict]:
    if not rows:
        raise ValueError("security has no input history")
    frame = pd.DataFrame(rows)
    frame.index = pd.DatetimeIndex(pd.to_datetime(frame.pop("trade_date")))
    frame = frame.loc[:, PRICE_COLUMNS].apply(pd.to_numeric).astype(float)
    if frame.empty or frame.index[-1].date() != trade_date:
        raise ValueError("security has no bar at the publication date")
    # Historical zero prices are missing source values, never a usable HFQ price.
    for name in ("close", "high", "low"):
        frame[name] = frame[name].where(frame[name] > 0)
    values = calculate_standard_technical_features(frame).iloc[-1]
    if pd.isna(frame.close.iloc[-1]):
        raise ValueError("security has no valid publication-date adjusted close")
    chunks = []
    for offset in range(0, len(frame), 256):
        part = frame.iloc[offset:offset + 256]
        payload = {"code": code, "price_basis": "hfq", "columns": ["trade_date", *PRICE_COLUMNS],
                   "rows": [[stamp.date().isoformat(), *[None if pd.isna(v) else float(v) for v in row]]
                            for stamp, row in zip(part.index, part.to_numpy())]}
        chunks.append(save_input_object(artifact_root, payload))
    evidence = {"code": code, "input_start": frame.index[0].date().isoformat(),
                "input_end": trade_date.isoformat(), "bar_count": len(frame), "chunks": chunks}
    fingerprint = hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()
    output = {"stk_code": code, "input_start_time": frame.index[0].to_pydatetime(),
              "input_end_time": frame.index[-1].to_pydatetime(), "input_fingerprint": fingerprint,
              "bar_count": len(frame), **{k: None if pd.isna(v) else float(v) for k, v in values.items()}}
    return output, evidence


def run_daily(*, trade_date: date, artifact_root: Path, codes=(), chunk_size: int = 50,
              now: datetime | None = None, connection_factory=market_connection, progress=None) -> dict:
    current = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    if current.tzinfo is None:
        raise ValueError("now must include a timezone")
    current = current.astimezone(ZoneInfo("Asia/Shanghai"))
    if trade_date > current.date() or (trade_date == current.date() and current.time() < day_time(16, 30)):
        raise ValueError("daily publication requires a completed EOD date (after 16:30 Shanghai)")
    if not 1 <= chunk_size <= 200:
        raise ValueError("chunk_size must be 1..200")
    if any(not re.fullmatch(r"[0-9]{6}\.(SH|SZ|BJ)", code) for code in codes):
        raise ValueError("codes must include their SH/SZ/BJ exchange")
    selected = sorted(set(codes))
    started = monotonic()
    scope = UNIVERSE_KEY if not selected else "subset_" + hashlib.sha256(",".join(selected).encode()).hexdigest()[:24]
    with connection_factory() as writer:
        with writer.cursor() as cur:
            cur.execute("SELECT GET_LOCK(%s,0) AS acquired", (LOCK_NAME,))
            if cur.fetchone()["acquired"] != 1:
                return {"skipped": "another daily computation holds the shared lock"}
        try:
            with connection_factory() as reader:
                with reader.cursor() as cur:
                    cur.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
                    cur.execute("SELECT is_trade_day FROM aiia_trade_calendar WHERE market_code='CN_A' AND calendar_date=%s", (trade_date,))
                    calendar = cur.fetchone()
                    if calendar is None:
                        raise ValueError("trade calendar has no entry for requested date")
                    if not calendar["is_trade_day"]:
                        return {"skipped": "non-trading date", "trade_date": trade_date.isoformat()}
                    cur.execute("SELECT stk_code,update_time FROM kcrp_stock_price WHERE trade_date=%s AND stk_code REGEXP '^[0-9]{6}\\.(SH|SZ|BJ)$' ORDER BY stk_code", (trade_date,))
                    universe = list(cur.fetchall())
                    available = {r["stk_code"] for r in universe}
                    if selected and not set(selected).issubset(available):
                        raise ValueError("some requested securities have no source bar on this date")
                    selected = selected or sorted(available)
                    if not selected:
                        raise ValueError("daily source not ready: no securities for requested date")
                    watermark = max((r["update_time"] for r in universe if r["update_time"] is not None), default=None)
                    records, evidence, skipped = [], [], []
                    for offset in range(0, len(selected), chunk_size):
                        chunk = selected[offset:offset + chunk_size]
                        placeholders = ",".join(["%s"] * len(chunk))
                        cur.execute(f"""SELECT stk_code,trade_date,adjclose AS close,adjhigh AS high,adjlow AS low,volume
                            FROM kcrp_stock_price WHERE stk_code IN ({placeholders}) AND trade_date BETWEEN %s AND %s
                            ORDER BY stk_code,trade_date""", (*chunk, HISTORY_ANCHOR, trade_date))
                        grouped = {code: [] for code in chunk}
                        for row in cur.fetchall():
                            grouped[row.pop("stk_code")].append(row)
                        for code in chunk:
                            try:
                                output, input_evidence = compute_security(grouped[code], code=code,
                                    trade_date=trade_date, artifact_root=artifact_root)
                            except ValueError as exc:
                                skipped.append({"code": code, "reason": str(exc)})
                                continue
                            records.append(output)
                            evidence.append(input_evidence)
                        if progress:
                            progress({"processed": min(offset + chunk_size, len(selected)),
                                      "requested": len(selected), "computed": len(records), "skipped": len(skipped),
                                      "elapsed_seconds": round(monotonic() - started, 3)})
                reader.rollback()
            manifest = {"trade_date": trade_date.isoformat(), "universe_key": scope,
                        "formula_revision": STANDARD_TECHNICAL_REVISION, "price_basis": "hfq",
                        "source": "kingdomai.kcrp_stock_price", "history_anchor": HISTORY_ANCHOR.isoformat(),
                        "requested_codes": selected, "securities": evidence, "skipped": skipped}
            reference = save_input_object(artifact_root, manifest)
            batch_id = Path(reference).name.split(".")[0]
            batch = {"batch_id": batch_id, "universe_key": scope, "trade_date": trade_date,
                     "formula_revision": STANDARD_TECHNICAL_REVISION, "price_basis": "hfq",
                     "source_name": "kcrp_stock_price", "source_watermark": watermark,
                     "started_at": current.astimezone(ZoneInfo("UTC")).replace(tzinfo=None),
                     "input_manifest_ref": reference, "requested_count": len(selected)}
            published = publish_daily_batch(writer, batch=batch, rows=records)
            return {**published, "trade_date": trade_date.isoformat(), "universe_key": scope,
                    "requested_count": len(selected), "skipped_count": len(skipped), "input_manifest_ref": reference,
                    "elapsed_seconds": round(monotonic() - started, 3)}
        finally:
            writer.rollback()
            with writer.cursor() as cur:
                cur.execute("SELECT RELEASE_LOCK(%s)", (LOCK_NAME,))
