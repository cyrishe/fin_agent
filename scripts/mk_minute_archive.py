"""Locate lossless, per-day unadjusted MK minute bars."""
from __future__ import annotations

from pathlib import Path


COMPACT_ROOT = Path("outputs/stock_automl/mk_1m_compact")
COMPACT_ROOT_2025 = Path("outputs/stock_automl/mk_1m_compact_2025")
SUFFIXES = (".csv.zst", ".csv")


def trading_day(path: Path) -> str:
    for suffix in SUFFIXES:
        if path.name.endswith(suffix):
            return path.name[:-len(suffix)]
    raise ValueError(f"Unsupported minute-bar file: {path}")


def day_files(roots: list[Path] | tuple[Path, ...]) -> list[Path]:
    files = sorted(
        (path for root in roots for suffix in SUFFIXES
         for path in root.glob(f"20[0-9][0-9]-*{suffix}")),
        key=trading_day,
    )
    days = [trading_day(path) for path in files]
    if len(days) != len(set(days)):
        raise ValueError("Duplicate minute-bar trading day")
    return files


def day_file(root: Path, day: str) -> Path:
    for suffix in SUFFIXES:
        path = root / f"{day}{suffix}"
        if path.is_file():
            return path
    raise FileNotFoundError(f"No minute bars for {day} in {root}")
