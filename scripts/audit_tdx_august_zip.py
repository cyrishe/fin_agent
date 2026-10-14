"""Audit the user supplied TDX unadjusted one-minute August archive."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from zipfile import ZipFile


EXPECTED = {f"{h:02d}{m:02d}" for h, lo, hi in ((9, 31, 59), (10, 0, 59),
                                                   (11, 0, 30), (13, 1, 59),
                                                   (14, 0, 59), (15, 0, 0))
            for m in range(lo, hi + 1)}
NAME = re.compile(r"^(SH|SZ)#([0-9]{6})\.txt$")


def main() -> dict:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", type=Path, default=Path("/Users/imac/Downloads/分钟K线.zip"))
    parser.add_argument("--out", type=Path, default=Path(
        "outputs/stock_automl/tdx_august_20261014/audit.json"))
    args = parser.parse_args()
    days = Counter()
    complete = Counter()
    incomplete = Counter()
    empty = 0
    invalid = []
    by_day = defaultdict(list)
    with ZipFile(args.zip) as archive:
        for member in archive.infolist():
            matched = NAME.fullmatch(member.filename)
            if not matched:
                invalid.append((member.filename, "unexpected_filename"))
                continue
            symbol = matched.group(2)
            try:
                text = archive.read(member).decode("gbk")
            except UnicodeDecodeError:
                invalid.append((member.filename, "invalid_gbk"))
                continue
            lines = text.splitlines()
            if len(lines) <= 3:
                empty += 1
                continue
            if "不复权" not in lines[0] or not lines[0].startswith(symbol):
                invalid.append((member.filename, "header_mismatch"))
                continue
            slots = defaultdict(set)
            counts = Counter()
            bad = False
            for line in lines[2:]:
                if not line.startswith("2026-"):
                    continue
                fields = next(csv.reader([line], delimiter="\t"))
                if len(fields) != 8:
                    bad = True
                    break
                date, clock = fields[:2]
                if date not in {"2026-08-03", "2026-08-04", "2026-08-05",
                                "2026-08-06", "2026-08-07"} or clock not in EXPECTED:
                    bad = True
                    break
                o, h, l, c, vol, amount = map(float, fields[2:])
                if min(o, h, l, c) <= 0 or h < max(o, c) or l > min(o, c) or vol < 0 or amount < 0:
                    bad = True
                    break
                slots[date].add(clock)
                counts[date] += 1
            if bad:
                invalid.append((member.filename, "bad_bar"))
                continue
            if not counts:
                empty += 1
            for date, n in counts.items():
                days[date] += n
                by_day[date].append(symbol)
                if n == 240 and slots[date] == EXPECTED:
                    complete[date] += 1
                else:
                    incomplete[date] += 1
    result = {"zip_sha256": hashlib.sha256(args.zip.read_bytes()).hexdigest(),
              "files": len(archive.infolist()), "empty_files": empty,
              "bars_by_day": dict(days), "stocks_by_day": {d: len(v) for d, v in by_day.items()},
              "complete_240_by_day": dict(complete), "incomplete_by_day": dict(incomplete),
              "invalid_count": len(invalid), "invalid_examples": invalid[:20]}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
