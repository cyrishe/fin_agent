"""On-demand snapshot indicators using the existing stockHq source.

Latest raw quotes have a five-second shared local cache. Indicators are never
appended to a historical table. The bounded request budget applies to this new
indicator entry, not to unrelated collectors or existing quote clients.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, time
import fcntl
import json
import math
import os
from pathlib import Path
import re
import threading
import time as clock

from src.services.stock_indicator_minute import SHANGHAI
from src.services.stock_indicator_store import market_connection, ROOT

REALTIME_REVISION = "technical_snapshot_v1"
REALTIME_FEATURES = ("return_from_preclose", "return_from_open", "opening_gap", "intraday_amplitude",
                     "drawdown_from_high", "rebound_from_low", "range_position", "avg_price_bias")
CACHE_SECONDS = 5
REQUESTS_PER_MINUTE = 30
_LOCK = threading.Lock()


def _fetch(codes):
    from src.experiments.staged_data_protocol.phase2.realtime_quote_provider import load_realtime_rows
    rows, _ = load_realtime_rows({"codes": codes})
    lookup = {c.split(".")[0]: c for c in codes}
    return {lookup[r["code"]]: r for r in rows if r["code"] in lookup}


@contextmanager
def _cache_lock(path):
    if not _LOCK.acquire(timeout=2):
        raise TimeoutError("A realtime quote request is already in progress; retry shortly.")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as stream:
            deadline = clock.monotonic() + 2
            while True:
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if clock.monotonic() >= deadline:
                        raise TimeoutError("A realtime quote request is already in progress; retry shortly.")
                    clock.sleep(.02)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    finally:
        _LOCK.release()


def cached_quotes(codes, *, cache_root: Path | None = None, fetcher=None, epoch=None) -> tuple[dict, dict]:
    """Deduplicate overlapping requests across processes on the same host."""
    root = cache_root or Path(os.getenv("STOCK_INDICATOR_QUOTE_CACHE_ROOT", str(ROOT / "data/stock_indicator_artifacts/realtime")))
    fetcher = fetcher or _fetch
    timer = epoch or clock.time
    path = root / "quotes.json"
    with _cache_lock(root / "quotes.lock"):
        now = timer()
        try:
            state = json.loads(path.read_text()) if path.exists() else {"rows": {}, "calls": []}
        except (ValueError, OSError) as exc:
            # A broken budget ledger must not silently reset the request limit.
            raise RuntimeError("Realtime quote cache is unavailable; inspect its local file.") from exc
        rows = {k: v for k, v in state["rows"].items() if 0 <= now - v["fetched_at"] < CACHE_SECONDS}
        calls = [stamp for stamp in state["calls"] if now - stamp < 60]
        missing = [c for c in codes if c not in rows]
        note, requested = None, 0
        def save():
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"rows": rows, "calls": calls}, default=str, allow_nan=False))
            os.replace(temporary, path)
        if missing and len(calls) < REQUESTS_PER_MINUTE:
            calls.append(now)
            # Reserve before I/O: failed requests also consume their budget.
            save()
            requested = 1  # <=20 codes fit in the existing source's one batch.
            try:
                received = fetcher(missing)
                for code in missing:
                    rows[code] = {"fetched_at": timer(), "quote": received.get(code)}
            except Exception as exc:
                note = f"Realtime source request failed ({type(exc).__name__}); no stored minute price substituted."
                for code in missing:
                    rows[code] = {"fetched_at": timer(), "quote": None}
            save()
        elif missing:
            note = "Realtime indicator request budget exhausted; retry after the rolling minute window."
        return {c: rows[c]["quote"] for c in codes if c in rows and rows[c]["quote"] is not None}, {
            "external_requests": requested, "cache_hits": len(codes) - len(missing),
            "request_budget_per_minute": REQUESTS_PER_MINUTE, "fetch_note": note}


def market_reference(now: datetime, trade_date: date) -> datetime | None:
    local = now.astimezone(SHANGHAI)
    if trade_date == local.date() and local.time() < time(9, 30):
        return None
    close = datetime.combine(trade_date, time(15), SHANGHAI)
    if local >= close:
        return close
    if time(11, 30) <= local.time() < time(13):
        return datetime.combine(trade_date, time(11, 30), SHANGHAI)
    return local


def calculate_realtime_snapshot(quote: dict | None, *, code: str, now: datetime,
                                trade_date: date, max_lag_seconds: int = 60) -> dict:
    reference = market_reference(now, trade_date)
    output = {"code": code, "trade_date": trade_date.isoformat(), "formula_revision": REALTIME_REVISION,
              "price_basis": "unadjusted", "source": "upchina_stockHq", "data_as_of": None,
              "as_of": now.astimezone(SHANGHAI).isoformat(),
              "expected_as_of": reference.isoformat() if reference else None,
              "lag_seconds": None, "unavailable_reason": None, **dict.fromkeys(REALTIME_FEATURES)}
    if not quote:
        output["unavailable_reason"] = "The realtime source returned no quote for this security."
        return output
    stamp = datetime.fromisoformat(quote["snapshot_time"])
    stamp = stamp.replace(tzinfo=SHANGHAI) if stamp.tzinfo is None else stamp.astimezone(SHANGHAI)
    output["data_as_of"] = stamp.isoformat()
    if reference is None:
        output["unavailable_reason"] = "Continuous trading has not started; auction data is outside this calculation."
        return output
    output["lag_seconds"] = max(0, int((reference - stamp).total_seconds()))
    if (stamp > now.astimezone(SHANGHAI) or stamp.date() != trade_date
            or output["lag_seconds"] > max_lag_seconds):
        output["unavailable_reason"] = "Quote timestamp does not meet the requested trading date or freshness limit."
        return output
    def positive(field):
        value = quote.get(field)
        if value is None:
            return None
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    close, opening, high, low, previous, average = (positive(k) for k in ("close", "open", "high", "low", "preclose", "avg_price"))
    if close is None or (high is not None and close > high + 1e-8) or (low is not None and close < low - 1e-8):
        output["unavailable_reason"] = "Quote prices are missing or its high/low does not enclose the latest price."
        return output
    output.update(return_from_preclose=close / previous - 1 if previous else None,
                  return_from_open=close / opening - 1 if opening else None,
                  opening_gap=opening / previous - 1 if opening and previous else None,
                  intraday_amplitude=(high-low) / previous if high and low and previous else None,
                  drawdown_from_high=close / high - 1 if high else None,
                  rebound_from_low=close / low - 1 if low else None,
                  range_position=(close-low) / (high-low) if high and low and high > low else None,
                  avg_price_bias=close / average - 1 if average else None)
    return output


def read_realtime_indicators(*, codes, max_lag_seconds: int = 60, now=None,
                             connection_factory=market_connection, quote_loader=cached_quotes) -> dict:
    if isinstance(codes, str) or not 1 <= len(codes) <= 20:
        raise ValueError("provide 1..20 full stock codes")
    selected = sorted(set(codes))
    if any(not re.fullmatch(r"[0-9]{6}\.(SH|SZ|BJ)", c) for c in selected):
        raise ValueError("stock codes require an exchange suffix")
    if type(max_lag_seconds) is not int or max_lag_seconds < 0:
        raise ValueError("max_lag_seconds must be a nonnegative integer")
    current = now or datetime.now(SHANGHAI)
    if current.tzinfo is None:
        raise ValueError("now requires a timezone")
    current = current.astimezone(SHANGHAI)
    with connection_factory() as conn, conn.cursor() as cur:
        cur.execute("SELECT stk_code FROM kcrp_stock_baseinfo WHERE stk_code IN (" + ",".join(["%s"] * len(selected)) + ")", tuple(selected))
        if {r["stk_code"] for r in cur.fetchall()} != set(selected):
            raise ValueError("some codes are not present in the stock master")
        cur.execute("SELECT MAX(calendar_date) AS trade_date FROM aiia_trade_calendar WHERE market_code='CN_A' AND is_trade_day=1 AND calendar_date<=%s", (current.date(),))
        trade_date = cur.fetchone()["trade_date"]
        if trade_date is None:
            raise ValueError("no preceding trading date in calendar")
    quotes, evidence = quote_loader(selected)
    # Source time may advance while network I/O runs; evaluate against completion time.
    observed = datetime.now(SHANGHAI) if now is None else current
    records = [calculate_realtime_snapshot(quotes.get(c), code=c, now=observed,
                trade_date=trade_date, max_lag_seconds=max_lag_seconds) for c in selected]
    return {"rows": records, "row_count": len(records), "formula_revision": REALTIME_REVISION,
            "max_lag_seconds": max_lag_seconds, **evidence}
