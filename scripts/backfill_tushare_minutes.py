"""Backfill Tushare A-share minute bars by trading day with resumable batches.

The account-specific endpoint is provided in tushare.info. This script sends the
same Tushare JSON protocol over HTTPS and never records the token in outputs.
"""

from __future__ import annotations

import argparse
import csv
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import fcntl
import gzip
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "https://api.quicksync.cn"
OUTPUT_FIELDS = ("ts_code", "trade_time", "open", "high", "low", "close", "vol", "amount")
BATCH_SIZE = 33  # 33 full trading days of 241 bars = 7,953 < the 8,000-row limit.


class SlidingRateLimiter:
    def __init__(self, maximum: int) -> None:
        if not 1 <= maximum <= 400:
            raise ValueError("requests per minute must be between 1 and 400")
        self.maximum = maximum
        self.times: deque[float] = deque()
        self.condition = threading.Condition()

    def acquire(self) -> None:
        with self.condition:
            while True:
                now = time.monotonic()
                while self.times and now - self.times[0] >= 60.0:
                    self.times.popleft()
                if len(self.times) < self.maximum:
                    self.times.append(now)
                    return
                self.condition.wait(timeout=max(0.02, 60.0 - (now - self.times[0])))


class Tushare:
    def __init__(self, token: str, limiter: SlidingRateLimiter) -> None:
        self.token = token
        self.limiter = limiter

    def query(self, api_name: str, params: dict[str, object], fields: str = "") -> tuple[list[str], list[list[object]]]:
        payload = json.dumps({"api_name": api_name, "token": self.token, "params": params, "fields": fields}).encode()
        last_error: Exception | None = None
        for attempt in range(5):
            self.limiter.acquire()
            try:
                req = Request(ENDPOINT, data=payload, headers={"Content-Type": "application/json"})
                with urlopen(req, timeout=60) as response:
                    result = json.load(response)
                if result.get("code") != 0:
                    raise RuntimeError(f"{api_name}: {result.get('msg', 'unknown API error')}")
                data = result.get("data") or {}
                got_fields, rows = data.get("fields"), data.get("items")
                if not isinstance(got_fields, list) or not isinstance(rows, list):
                    raise RuntimeError(f"{api_name}: malformed response")
                return got_fields, rows
            except (HTTPError, URLError, HTTPException, TimeoutError, OSError, ValueError, RuntimeError) as exc:
                last_error = exc
                if attempt == 4:
                    break
                time.sleep(min(20, 2 ** attempt))
        raise RuntimeError(f"{api_name} failed after five attempts: {last_error}")


def token_from_file(path: Path) -> str:
    raw = path.read_text().strip()
    match = re.search(r"^\s*TUSHARE_TOKEN\s*=\s*['\"]?([^'\"\s]+)", raw, re.M)
    if not match:
        raise ValueError(f"No TUSHARE_TOKEN found in {path}")
    return match.group(1)


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.partial")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def row_dicts(fields: list[str], rows: list[list[object]]) -> list[dict[str, object]]:
    missing = set(OUTPUT_FIELDS) - set(fields)
    if missing:
        raise ValueError(f"minute response missing fields: {sorted(missing)}")
    if any(len(row) != len(fields) for row in rows):
        raise ValueError("minute response has rows with wrong width")
    return [dict(zip(fields, row)) for row in rows]


def validated_chunk(day: str, requested: list[str], fields: list[str], rows: list[list[object]]) -> dict:
    if len(rows) >= 8000:
        raise ValueError(f"{day}: batch reached the 8,000-row response cap")
    mapped = row_dicts(fields, rows)
    codes = set(requested)
    seen: set[tuple[str, str]] = set()
    for bar in mapped:
        code, stamp = bar["ts_code"], bar["trade_time"]
        if code not in codes or not isinstance(stamp, str) or stamp[:10] != f"{day[:4]}-{day[4:6]}-{day[6:]}":
            raise ValueError(f"{day}: unexpected code or trade_time")
        key = (code, stamp)
        if key in seen:
            raise ValueError(f"{day}: duplicate minute bar {key}")
        seen.add(key)
    returned = sorted({bar["ts_code"] for bar in mapped})
    return {
        "trade_date": day,
        "requested_codes": requested,
        "returned_codes": returned,
        "fields": fields,
        "items": rows,
    }


def fetch_chunk(api: Tushare, day: str, index: int, codes: list[str], staging: Path) -> tuple[int, list[str]]:
    path = staging / f"chunk_{index:04d}.json.gz"
    if path.exists():
        try:
            chunk = json.loads(gzip.decompress(path.read_bytes()))
            if chunk["trade_date"] == day and chunk["requested_codes"] == codes:
                validated_chunk(day, codes, chunk["fields"], chunk["items"])
                return index, chunk["returned_codes"]
        except (OSError, ValueError, KeyError):
            path.unlink()
    fields, rows = api.query("stk_mins", {
        "ts_code": ",".join(codes),
        "freq": "1min",
        "start_date": f"{day[:4]}-{day[4:6]}-{day[6:]} 09:30:00",
        "end_date": f"{day[:4]}-{day[4:6]}-{day[6:]} 15:00:00",
        "limit": 8000,
    })
    chunk = validated_chunk(day, codes, fields, rows)
    atomic_bytes(path, gzip.compress(json_bytes(chunk), compresslevel=3, mtime=0))
    return index, chunk["returned_codes"]


def active_codes(api: Tushare, day: str, staging: Path) -> list[str]:
    path = staging / "active_codes.json"
    if path.exists():
        codes = load_json(path)["codes"]
        if codes:
            return codes
    fields, rows = api.query("daily", {"trade_date": day}, "ts_code")
    if "ts_code" not in fields or len(rows) >= 6000:
        raise ValueError(f"{day}: daily stock universe may be incomplete")
    position = fields.index("ts_code")
    codes = sorted({row[position] for row in rows if isinstance(row[position], str)})
    if not codes:
        raise ValueError(f"{day}: empty daily stock universe")
    atomic_bytes(path, json_bytes({"trade_date": day, "source": "daily", "codes": codes}))
    return codes


def complete_day(api: Tushare, day: str, output: Path, workers: int) -> dict:
    year_dir = output / day[:4]
    final_path = year_dir / f"{day}.csv.gz"
    manifest_path = year_dir / f"{day}.json"
    if final_path.exists() and manifest_path.exists():
        manifest = load_json(manifest_path)
        if hashlib.sha256(final_path.read_bytes()).hexdigest() == manifest.get("sha256"):
            print(f"{day} already complete: {manifest['bars']:,} bars", flush=True)
            return manifest

    staging = output / "staging" / day
    staging.mkdir(parents=True, exist_ok=True)
    codes = active_codes(api, day, staging)
    groups = [codes[i:i + BATCH_SIZE] for i in range(0, len(codes), BATCH_SIZE)]
    started = time.monotonic()
    returned_codes: set[str] = set()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch_chunk, api, day, i, group, staging) for i, group in enumerate(groups)]
        for completed, future in enumerate(as_completed(futures), 1):
            _, returned = future.result()
            returned_codes.update(returned)
            if completed % 40 == 0 or completed == len(groups):
                print(f"{day}: {completed}/{len(groups)} batches, {time.monotonic() - started:.0f}s", flush=True)

    missing_codes = sorted(set(codes) - returned_codes)
    temporary = final_path.with_name(final_path.name + f".{os.getpid()}.partial")
    final_path.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with gzip.open(temporary, "wt", encoding="utf-8", newline="", compresslevel=3) as stream:
            writer = csv.writer(stream)
            writer.writerow(OUTPUT_FIELDS)
            for index in range(len(groups)):
                chunk = json.loads(gzip.decompress((staging / f"chunk_{index:04d}.json.gz").read_bytes()))
                for row in sorted(row_dicts(chunk["fields"], chunk["items"]), key=lambda r: (r["ts_code"], r["trade_time"])):
                    writer.writerow([row[field] for field in OUTPUT_FIELDS])
                    total += 1
        os.replace(temporary, final_path)
    finally:
        temporary.unlink(missing_ok=True)
    compressed = final_path.read_bytes()
    digest = hashlib.sha256(compressed).hexdigest()
    manifest = {
        "trade_date": day,
        "source": "Tushare stk_mins via api.quicksync.cn",
        "universe_source": "Tushare daily(trade_date)",
        "frequency": "1min",
        "columns": OUTPUT_FIELDS,
        "active_codes": len(codes),
        "codes_with_bars": len(returned_codes),
        "listed_codes_without_minute_bars": missing_codes,
        "batch_size": BATCH_SIZE,
        "batches": len(groups),
        "bars": total,
        "bytes": len(compressed),
        "sha256": digest,
        "completed_at": datetime.now().astimezone().isoformat(),
    }
    atomic_bytes(manifest_path, json_bytes(manifest))
    for path in staging.glob("chunk_*.json.gz"):
        path.unlink()
    (staging / "active_codes.json").unlink(missing_ok=True)
    staging.rmdir()
    print(f"{day} complete: {len(returned_codes):,}/{len(codes):,} codes, {total:,} bars, {len(compressed):,} bytes; missing {missing_codes}", flush=True)
    return manifest


def trading_days(api: Tushare, start: str, end: str, output: Path) -> list[str]:
    path = output / f"calendar_{start}_{end}.json"
    if path.exists():
        return load_json(path)["dates"]
    fields, rows = api.query("trade_cal", {"exchange": "SSE", "start_date": start, "end_date": end, "is_open": 1})
    if "cal_date" not in fields:
        raise ValueError("trade_cal response lacks cal_date")
    position = fields.index("cal_date")
    dates = sorted({row[position] for row in rows})
    if not dates:
        raise ValueError("No open trading days in requested interval")
    atomic_bytes(path, json_bytes({"source": "trade_cal SSE", "dates": dates}))
    return dates


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="20240101", help="YYYYMMDD")
    parser.add_argument("--end", default="20241231", help="YYYYMMDD")
    parser.add_argument("--years", help="Comma-separated years in download order, e.g. 2024,2023,2022,2021")
    parser.add_argument("--output", type=Path, default=Path("/Volumes/ext/tushare_market_data"))
    parser.add_argument("--token-file", type=Path, default=ROOT / ".tushare")
    parser.add_argument("--requests-per-minute", type=int, default=360)
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args()
    if args.workers < 1 or not re.fullmatch(r"\d{8}", args.start) or not re.fullmatch(r"\d{8}", args.end):
        parser.error("workers must be positive and dates must be YYYYMMDD")
    if args.years:
        years = args.years.split(",")
        if not years or any(not re.fullmatch(r"20\d{2}", year) for year in years) or len(set(years)) != len(years):
            parser.error("--years must contain distinct years such as 2024,2023,2022,2021")
        intervals = [(year + "0101", year + "1231") for year in years]
    else:
        intervals = [(args.start, args.end)]
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / ".backfill.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error(f"another backfill process holds {args.output / '.backfill.lock'}")
        lock.write(str(os.getpid()) + "\n")
        lock.flush()
        api = Tushare(token_from_file(args.token_file), SlidingRateLimiter(args.requests_per_minute))
        dates = [day for start, end in intervals for day in reversed(trading_days(api, start, end, args.output))]
        print(f"{len(dates)} trading days, {dates[0]} to {dates[-1]} in requested order, limit {args.requests_per_minute}/60s", flush=True)
        for index, day in enumerate(dates, 1):
            for attempt in range(5):
                try:
                    complete_day(api, day, args.output, args.workers)
                    break
                except Exception as exc:
                    if attempt == 4:
                        raise
                    delay = 90 * (attempt + 1)
                    print(f"{day}: retrying after {delay}s because {exc}", flush=True)
                    time.sleep(delay)
            print(f"progress: {index}/{len(dates)} trading days", flush=True)


if __name__ == "__main__":
    main()
