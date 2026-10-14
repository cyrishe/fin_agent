"""Stream unadjusted 2025 MK bars from tar.xz into verified daily .csv.zst files."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tarfile
from pathlib import Path

import zstandard as zstd

from scripts.compact_mk_minute_archive import decompressed_sha256, sha256_file
from scripts.mk_minute_archive import COMPACT_ROOT_2025


ARCHIVES = (
    (Path.home() / "Downloads/mk202501_202506.tar.xz", "2025-01-01", "2025-06-30"),
    (Path.home() / "Downloads/mk202507_202511.tar.xz", "2025-07-01", "2025-11-30"),
    (Path.home() / "Downloads/mk202512.tar.xz", "2025-12-01", "2025-12-31"),
)
MEMBER = re.compile(r"(?:\./)?mk/(none|post)/(2025-\d{2}-\d{2})\.csv\Z")
HEADER = b"time,symbol,open,high,low,close,volume,turnover\n"


def write_manifest(output: Path, files: dict[tuple[str, str], dict]) -> dict:
    ordered = [files[key] for key in sorted(files)]
    days = sorted({item["day"] for item in ordered})
    summary = {
        "format": "lossless per-trading-day CSV compressed with Zstandard level 10",
        "days": len(days),
        "files_count": len(ordered),
        "first_day": days[0] if days else None,
        "last_day": days[-1] if days else None,
        "original_bytes": sum(item["original_bytes"] for item in ordered),
        "compressed_bytes": sum(item["compressed_bytes"] for item in ordered),
        "files": ordered,
    }
    temporary = output / "compressed_manifest.json.partial"
    temporary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    os.replace(temporary, output / "compressed_manifest.json")
    return summary


def compact_tar_archives(archives: tuple[tuple[Path, str, str], ...], output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "compressed_manifest.json"
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    saved = {(item.get("kind", "none"), item["day"]): item
             for item in previous.get("files", []) if item.get("kind", "none") == "none"}
    seen: set[tuple[str, str]] = set()
    files: dict[tuple[str, str], dict] = {}
    for archive, first, last in archives:
        with tarfile.open(archive, mode="r|xz") as stream:
            for member in stream:
                if member.isdir():
                    continue
                match = MEMBER.fullmatch(member.name)
                if not member.isfile() or match is None:
                    raise ValueError(f"Unexpected tar member: {archive.name}:{member.name}")
                kind, day = match.groups()
                if kind == "post":
                    continue
                key = kind, day
                if not first <= day <= last or key in seen:
                    raise ValueError(f"Unexpected or duplicate trading day: {kind}/{day}")
                seen.add(key)
                target = output / f"{day}.csv.zst"
                prior = saved.get(key)
                if target.exists() and prior and (prior["original_bytes"] == member.size
                                                  and prior["compressed_bytes"] == target.stat().st_size
                                                  and prior["compressed_sha256"] == sha256_file(target)
                                                  and prior["original_sha256"] == decompressed_sha256(target)):
                    files[key] = {**prior, "kind": kind}
                    print(f"{len(seen)} {kind}/{day}: verified existing", flush=True)
                    continue
                if target.exists():
                    raise ValueError(f"Unverified existing day: {target}")
                source = stream.extractfile(member)
                if source is None:
                    raise ValueError(f"Unreadable tar member: {member.name}")
                temporary = output / f"{day}.csv.zst.partial"
                digest = hashlib.sha256()
                size = 0
                first_chunk = True
                with source, temporary.open("wb") as compressed:
                    with zstd.ZstdCompressor(level=10, threads=2).stream_writer(compressed) as writer:
                        while chunk := source.read(1024 * 1024):
                            if first_chunk and not chunk.startswith(HEADER):
                                raise ValueError(f"Unexpected CSV header: {member.name}")
                            first_chunk = False
                            digest.update(chunk)
                            size += len(chunk)
                            writer.write(chunk)
                if size != member.size or decompressed_sha256(temporary) != digest.hexdigest():
                    temporary.unlink(missing_ok=True)
                    raise ValueError(f"Content validation failed: {member.name}")
                os.replace(temporary, target)
                record = {"kind": kind, "day": day, "source_archive": archive.name,
                          "source_member": member.name,
                          "original_bytes": size, "original_sha256": digest.hexdigest(),
                          "compressed_bytes": target.stat().st_size,
                          "compressed_sha256": sha256_file(target)}
                files[key] = record
                write_manifest(output, files | {key: value for key, value in saved.items()
                                                if key not in seen})
                print(f"{len(seen)} {kind}/{day}: {size / 1e6:.1f} → "
                      f"{record['compressed_bytes'] / 1e6:.1f} MB", flush=True)
    if len(files) != len(seen):
        raise ValueError("Missing processed trading day")
    return write_manifest(output, files)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=COMPACT_ROOT_2025)
    args = parser.parse_args()
    summary = compact_tar_archives(ARCHIVES, args.output)
    print(json.dumps({key: value for key, value in summary.items() if key != "files"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
