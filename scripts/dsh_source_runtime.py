#!/usr/bin/env python3
"""Launch the adjacent DeepSeek Harness checkout through its supported dsh CLI."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def main() -> None:
    root = Path(
        os.environ.get("FINANCE_DSH_SOURCE_ROOT")
        or Path(__file__).resolve().parents[2] / "deepseek-harness"
    ).resolve()
    entry = root / "apps" / "cli" / "src" / "bin.ts"
    if not entry.is_file():
        raise SystemExit(f"DeepSeek Harness source entry not found: {entry}")
    node = os.environ.get("FINANCE_DSH_NODE_BIN") or shutil.which("node")
    if not node:
        raise SystemExit("DeepSeek Harness source runtime requires Node.js >=22.19")
    os.chdir(root)
    argv = [node, "--import", "tsx/esm", str(entry), *os.sys.argv[1:]]
    os.execvpe(node, argv, os.environ)


if __name__ == "__main__":
    main()
