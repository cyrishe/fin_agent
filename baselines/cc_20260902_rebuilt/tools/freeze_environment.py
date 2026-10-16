#!/usr/bin/env python3
"""Record the tested CC dependency closure, not an invented September 2 lock.

Maintainer utility: run once when assembling this reference. Normal users install
the resulting lock; they do not regenerate it from their development environment.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import platform
import subprocess
import sys
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOTS = (
    "claude-agent-sdk", "mcp", "requests", "PyMySQL", "pandas",
    "beautifulsoup4", "openai", "fastjsonschema", "json-repair",
    "python-dotenv", "PyYAML", "Flask", "fastapi", "openpyxl",
    "reportlab", "python-docx", "packaging",
)


def capture(destination: Path) -> None:
    queue = [(name, frozenset()) for name in ROOTS]
    seen = set()
    versions = {}
    while queue:
        name, extras = queue.pop()
        key = (canonicalize_name(name), extras)
        if key in seen:
            continue
        seen.add(key)
        distribution = metadata.distribution(name)
        versions[canonicalize_name(distribution.metadata["Name"])] = distribution.version
        for text in distribution.requires or []:
            requirement = Requirement(text)
            if requirement.marker and not any(
                requirement.marker.evaluate({"extra": extra}) for extra in (extras or {""})
            ):
                continue
            installed = metadata.version(requirement.name)
            if requirement.specifier and installed not in requirement.specifier:
                raise RuntimeError(f"Installed dependency conflicts with {requirement.name}")
            queue.append((requirement.name, frozenset(requirement.extras)))

    cli = Path(metadata.distribution("claude-agent-sdk").locate_file("claude_agent_sdk/_bundled/claude"))
    record = {
        "meaning": "Reconstruction verification environment; not a recovered September 2 full lock",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "roots": list(ROOTS),
        "packages": dict(sorted(versions.items())),
        "claude_cli": {
            "version": subprocess.check_output([str(cli), "--version"], text=True).strip(),
            "sha256": hashlib.sha256(cli.read_bytes()).hexdigest(),
            "size_bytes": cli.stat().st_size,
        },
    }
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("requirements.lock.txt", "environment.json"):
        if (destination / name).exists():
            raise FileExistsError(f"Refusing to overwrite frozen {name}")
    (destination / "requirements.lock.txt").write_text(
        "# CC reconstruction runtime; Python 3.12; generated once from the tested environment.\n"
        + "# Original repository requirements remain unchanged inside source.tar.gz.\n"
        + "\n".join(f"{name}=={version}" for name, version in sorted(versions.items())) + "\n",
        encoding="utf-8",
    )
    (destination / "environment.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"package_count": len(versions), "python": record["python"], "claude_cli": record["claude_cli"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    capture(parser.parse_args().output.resolve())
