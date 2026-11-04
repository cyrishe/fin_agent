"""Losslessly compact verified daily MK CSVs into directly readable .csv.zst files."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import zstandard as zstd

from scripts.mk_minute_archive import COMPACT_ROOT, trading_day


SOURCE_ROOTS = (
    Path("outputs/stock_automl/mk_archive_jan_apr"),
    Path("outputs/stock_automl/mk_archive"),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def decompressed_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as compressed:
        with zstd.ZstdDecompressor().stream_reader(compressed) as reader:
            while chunk := reader.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


def compact(roots: tuple[Path, ...], output: Path) -> dict:
    entries = []
    for root in roots:
        manifest = json.loads((root / "archive_manifest.json").read_text())
        for record in manifest:
            source = root / record["path"]
            day = trading_day(source)
            entries.append((day, source, record))
    if len({day for day, _, _ in entries}) != len(entries):
        raise ValueError("The source archives contain duplicate trading days")
    output.mkdir(parents=True, exist_ok=True)
    result = []
    for index, (day, source, record) in enumerate(sorted(entries), start=1):
        target = output / f"{day}.csv.zst"
        if source.exists() and source.stat().st_size != record["bytes"]:
            raise ValueError(f"Original CSV size changed: {source}")
        if not target.exists():
            if not source.exists():
                raise FileNotFoundError(f"Neither original nor compressed day exists: {day}")
            temporary = output / f"{day}.csv.zst.partial"
            with source.open("rb") as raw, temporary.open("wb") as compressed:
                zstd.ZstdCompressor(level=10, threads=2).copy_stream(raw, compressed)
            if decompressed_sha256(temporary) != record["sha256"]:
                temporary.unlink()
                raise ValueError(f"Compressed content differs from original manifest: {day}")
            os.replace(temporary, target)
        elif decompressed_sha256(target) != record["sha256"]:
            raise ValueError(f"Existing compressed content differs from original: {day}")
        if source.exists():
            source.unlink()
        result.append({"day": day, "original_bytes": record["bytes"],
                       "original_sha256": record["sha256"],
                       "compressed_bytes": target.stat().st_size,
                       "compressed_sha256": sha256_file(target)})
        print(f"{index}/{len(entries)} {day}: "
              f"{record['bytes'] / 1e6:.1f} → {target.stat().st_size / 1e6:.1f} MB",
              flush=True)
    summary = {"format": "lossless per-trading-day CSV compressed with Zstandard level 10",
               "days": len(result),
               "first_day": result[0]["day"], "last_day": result[-1]["day"],
               "original_bytes": sum(item["original_bytes"] for item in result),
               "compressed_bytes": sum(item["compressed_bytes"] for item in result),
               "files": result}
    (output / "compressed_manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=COMPACT_ROOT)
    args = parser.parse_args()
    summary = compact(SOURCE_ROOTS, args.output)
    print(json.dumps({key: value for key, value in summary.items() if key != "files"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
