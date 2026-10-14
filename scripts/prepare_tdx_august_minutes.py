"""Convert the audited TDX August 3–7 archive to the existing daily minute format."""
from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import Counter
from pathlib import Path
from zipfile import ZipFile

import zstandard as zstd

from scripts.audit_tdx_august_zip import EXPECTED, NAME


ZIP = Path("/Users/imac/Downloads/分钟K线.zip")
OUT = Path("outputs/stock_automl/mk_1m_compact")
DAYS = [f"2026-08-0{day}" for day in range(3, 8)]


def main() -> dict:
    audit = json.loads(Path("outputs/stock_automl/tdx_august_20261014/audit.json").read_text())
    if audit["invalid_count"] or audit["incomplete_by_day"]:
        raise ValueError("TDX audit failed")
    if hashlib.sha256(ZIP.read_bytes()).hexdigest() != audit["zip_sha256"]:
        raise ValueError("Archive changed since audit")
    OUT.mkdir(parents=True, exist_ok=True)
    temp = {day: OUT / f"{day}.csv.zst.tmp" for day in DAYS}
    for day in DAYS:
        if (OUT / f"{day}.csv.zst").exists():
            raise FileExistsError(day)
    files = {day: path.open("wb") for day, path in temp.items()}
    compressors = {day: zstd.ZstdCompressor(level=3).stream_writer(files[day]) for day in DAYS}
    texts = {day: io.TextIOWrapper(compressors[day], encoding="utf-8", newline="") for day in DAYS}
    writers = {day: csv.writer(texts[day], lineterminator="\n") for day in DAYS}
    counts = Counter()
    try:
        for writer in writers.values():
            writer.writerow(["time", "symbol", "open", "high", "low", "close", "volume", "turnover"])
        with ZipFile(ZIP) as archive:
            for member in archive.infolist():
                match = NAME.fullmatch(member.filename)
                if not match:
                    continue
                suffix = ".SH" if match.group(1) == "SH" else ".SZ"
                symbol = match.group(2) + suffix
                lines = archive.read(member).decode("gbk").splitlines()
                for line in lines[2:]:
                    if not line.startswith("2026-"):
                        continue
                    day, clock, o, h, low, close, volume, amount = line.split("\t")
                    if day not in writers or clock not in EXPECTED:
                        raise ValueError(f"Unexpected date/clock {member.filename}: {day} {clock}")
                    time = f"{day} {clock[:2]}:{clock[2:]}:00"
                    writers[day].writerow([time, symbol, o, h, low, close, volume, amount])
                    counts[day] += 1
    finally:
        for text in texts.values():
            text.flush()
            text.close()
        for stream in compressors.values():
            stream.close()
        for file in files.values():
            file.close()
    if dict(counts) != audit["bars_by_day"]:
        raise ValueError("Prepared row totals disagree with audit")
    result = {"zip_sha256": audit["zip_sha256"], "days": {}}
    for day, source in temp.items():
        target = OUT / f"{day}.csv.zst"
        source.replace(target)
        result["days"][day] = {"rows": counts[day],
                                "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
    (Path("outputs/stock_automl/tdx_august_20261014") / "prepared_manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
