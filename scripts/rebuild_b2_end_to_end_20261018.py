"""Freshly rebuild the historical B2 extension, four-K choices and account replay."""
from __future__ import annotations

import argparse
from pathlib import Path

from scripts import audit_b2_end_to_end_20261018 as audit
from scripts import extend_b2_fourk_2024_minutes as fourk
from scripts import extend_limit_rebound_2024_minutes as daily
from scripts import report_b2_continuous_20261018 as continuous


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True,
                        help="New empty directory for this reproducible replay")
    args = parser.parse_args()
    root = args.output
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"Replay output must be empty: {root}")
    root.mkdir(parents=True, exist_ok=True)

    daily.OUT = root / "daily_extension"
    daily.OUT.mkdir()
    daily.run()

    fourk.SOURCE = daily.OUT
    fourk.OUT = root / "fourk"
    fourk.main()

    continuous.NEW = fourk.OUT
    continuous.OUT = root / "continuous"
    continuous.main()

    audit.ROOT = root
    audit.OUT = root / "audit"
    audit.main()


if __name__ == "__main__":
    main()
