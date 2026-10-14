"""Independently check the full-year compact 2025 minute archive."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts.compact_mk_minute_archive import decompressed_sha256, sha256_file
from scripts.mk_minute_archive import COMPACT_ROOT_2025


def main() -> None:
    root = COMPACT_ROOT_2025
    manifest = json.loads((root / "compressed_manifest.json").read_text())
    records = manifest["files"]
    listed = {item["day"] for item in records}
    actual = {path.name.removesuffix(".csv.zst") for path in root.glob("2025-*.csv.zst")}
    months = Path("outputs/stock_automl/limit_rebound_2y_r1/price_months")
    market = set()
    for path in months.glob("price_2025-*.csv.gz"):
        market.update(pd.read_csv(path, usecols=["trade_date"]).trade_date.astype(str))
    if listed != actual or listed != market or len(records) != len(listed):
        raise ValueError(f"Day coverage mismatch: manifest={len(listed)} files={len(actual)} market={len(market)}")
    original_bytes = 0
    compressed_bytes = 0
    for n, item in enumerate(records, 1):
        path = root / f'{item["day"]}.csv.zst'
        if item["kind"] != "none" or item["compressed_bytes"] != path.stat().st_size:
            raise ValueError(f"Incorrect kind or size: {path}")
        if sha256_file(path) != item["compressed_sha256"]:
            raise ValueError(f"Compressed SHA mismatch: {path}")
        if decompressed_sha256(path) != item["original_sha256"]:
            raise ValueError(f"Raw SHA mismatch: {path}")
        original_bytes += item["original_bytes"]
        compressed_bytes += item["compressed_bytes"]
        if n % 25 == 0:
            print(f"verified {n}/{len(records)}", flush=True)
    if (original_bytes != manifest["original_bytes"] or
            compressed_bytes != manifest["compressed_bytes"]):
        raise ValueError("Manifest size total mismatch")
    print(json.dumps({"days": len(records), "first": min(listed), "last": max(listed),
                      "original_bytes": original_bytes, "compressed_bytes": compressed_bytes},
                     ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
