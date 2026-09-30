"""Bounded same-host cache of public minute facts; source timestamps stay intact."""
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
import fcntl
import json
import os
from pathlib import Path
import threading
import time

from src.services.stock_indicator_store import ROOT

_LOCK = threading.Lock()
TTL_SECONDS = 5
MAX_ENTRIES = 512


@contextmanager
def thread_lock():
    if not _LOCK.acquire(timeout=10):
        raise TimeoutError("Minute data is being processed; retry shortly.")
    try:
        yield
    finally:
        _LOCK.release()


@contextmanager
def file_lock(path, timeout=10):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Minute data is being processed; retry shortly.")
                time.sleep(.02)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    def encode(value):
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, datetime):
            return value.isoformat()
        raise TypeError(f"Cannot encode {type(value).__name__}")
    try:
        temporary.write_text(json.dumps(value, default=encode, allow_nan=False, separators=(",", ":")))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def cached_source_rows(codes, day, loader, *, root=None, clock=time.time):
    """Fetch missing codes in one DB batch; callers still apply their as-of cutoff."""
    root = Path(root or os.getenv("STOCK_MINUTE_CACHE_ROOT", str(ROOT / "data/stock_indicator_artifacts/minute_cache")))
    with thread_lock(), file_lock(root / "source.lock"):
        result, missing = {}, []
        now = clock()
        for code in codes:
            path = root / f"{day.isoformat()}_{code}.json"
            try:
                value = json.loads(path.read_text())
                if not 0 <= now - value["fetched_at"] < TTL_SECONDS:
                    raise ValueError("expired")
                rows = value["rows"]
                for row in rows:
                    row["bar_end_time"] = datetime.fromisoformat(row["bar_end_time"])
                result[code] = rows
            except (OSError, ValueError, KeyError, TypeError):
                missing.append(code)
        if missing:
            fetched = loader(missing)
            for code in missing:
                rows = fetched[code]
                atomic_json(root / f"{day.isoformat()}_{code}.json", {"fetched_at": clock(), "rows": rows})
                result[code] = rows
            # Only disposable cache files are pruned, never market facts/events.
            files = sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            for path in files[MAX_ENTRIES:]:
                path.unlink(missing_ok=True)
        return result, len(codes) - len(missing)
