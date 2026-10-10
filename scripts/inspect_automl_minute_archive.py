"""Safely extract the supplied historical minute archive and record its members."""

from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
from pathlib import Path, PurePosixPath


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    members = []
    with tarfile.open(args.archive, mode="r|xz") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if (path.is_absolute() or ".." in path.parts or not path.parts
                    or path.parts[0] != "mk" or member.name in seen):
                raise ValueError(f"Unsafe or duplicate archive member: {member.name}")
            seen.add(member.name)
            target = root.joinpath(*path.parts)
            if not target.resolve().is_relative_to(root):
                raise ValueError(f"Member escapes extraction root: {member.name}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile() or target.suffix.lower() != ".csv":
                raise ValueError(f"Unexpected archive member type: {member.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"Cannot read member: {member.name}")
            digest = hashlib.sha256()
            actual = 0
            with source, target.open("wb") as destination:
                while chunk := source.read(4 * 1024 * 1024):
                    destination.write(chunk)
                    digest.update(chunk)
                    actual += len(chunk)
            if actual != member.size:
                raise ValueError(f"Size mismatch: {member.name}")
            members.append({"path": member.name, "bytes": actual,
                            "sha256": digest.hexdigest()})
            if len(members) % 10 == 0:
                print(f"Extracted {len(members)} CSV files", flush=True)
    (root / "archive_manifest.json").write_text(
        json.dumps(members, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Complete: {len(members)} CSV files, {sum(r['bytes'] for r in members)} bytes",
          flush=True)


if __name__ == "__main__":
    main()
