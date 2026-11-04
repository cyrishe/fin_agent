"""Freeze and replay the independent Aug-Oct recent-20 stock experiment."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from scripts.audit_automl_mk_1m_entry_limit import mark_entry_limit
from scripts.automl_st_status import attach_st_status, load_st_intervals
from scripts.benchmark_automl_1440_inference import FEATURES, db_connection, query
from scripts.report_automl_aug_oct_single_benchmark import (
    OCT, PREPARED, RECOVERED_LABELS, SEPTEMBER_PATCH,
    attach_observed_morning, attach_recovered_labels, daily_monthly, replay,
)


OUT = Path("docs/stock_automl_runs/20261011_aug_oct_independent20")
SOURCE = OUT / "candidates.csv.gz"
MARKET = OUT / "market_context.csv"
SOURCE_MARKET = Path("docs/stock_automl_runs/20261011_aug_oct_single_benchmark/market_context.csv")
SNAPSHOT_COLUMNS = ["signal_date", "next_date", "symbol6", "name",
                    "signal_price", *FEATURES, "second_high", "close_40",
                    "st_type", "entry_at_limit"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_snapshot(env_file: Path, output: Path) -> dict:
    """Use only Aug-Oct candidates; post-event labels never select T-day rows."""
    base = pd.read_csv(PREPARED, dtype={"symbol6": str, "signal_date": str,
                                            "next_date": str})
    patch = pd.read_csv(SEPTEMBER_PATCH, dtype={"symbol6": str,
                                                "signal_date": str,
                                                "next_date": str})
    patch = patch[patch.feature_complete].copy()
    base = base[~base.signal_date.isin(patch.signal_date.unique())].copy()
    october = pd.read_csv(OCT, dtype={"symbol6": str, "signal_date": str,
                                      "next_date": str})
    october = october[october.feature_complete].copy()
    market = pd.read_csv(SOURCE_MARKET, dtype={"trade_date": str})
    market = market[market.trade_date.between("2026-08-03", "2026-10-09")].copy()
    with db_connection(env_file) as db:
        patch = attach_observed_morning(db, patch)
        october = attach_observed_morning(db, october)
        intervals = load_st_intervals(db)
        database_calendar = query(
            db, "SELECT DISTINCT trade_date FROM kcrp_stock_price "
                "WHERE trade_date BETWEEN %s AND %s ORDER BY trade_date",
            ("2026-08-03", "2026-10-09"))
    rows = pd.concat([base, patch, october], ignore_index=True)
    rows = attach_recovered_labels(
        rows, pd.read_csv(RECOVERED_LABELS, dtype={"symbol6": str}))
    rows = rows.drop(columns=[column for column in ("st_type", "ann_date")
                              if column in rows])
    rows["symbol6"] = rows.symbol6.astype(str).str.zfill(6)
    rows["preclose"] = rows.signal_price / (1 + rows.signal_return)
    rows = mark_entry_limit(attach_st_status(rows, intervals))
    rows["signal_date"] = rows.signal_date.dt.strftime("%Y-%m-%d")
    unknown = int((~rows.entry_status_known).sum())
    excluded = int((rows.st_type.isin(("S", "Y")) & rows.entry_at_limit).sum())
    if unknown:
        raise ValueError(f"Unknown signal-time ST status for {unknown} candidates")
    rows = rows[~(rows.st_type.isin(("S", "Y")) & rows.entry_at_limit)].copy()
    if (rows.duplicated(["signal_date", "symbol6"]).any() or
            rows[list(FEATURES)].isna().any().any() or
            not rows.signal_return.between(.03, .06, inclusive="both").all()):
        raise ValueError("Candidate keys, seven factors, or 3%-6% gate changed")
    database_days = pd.to_datetime(database_calendar.trade_date).dt.strftime("%Y-%m-%d").tolist()
    if (market.trade_date.tolist() != database_days or len(market) != 44 or
            market.trade_date.duplicated().any() or
            not set(rows.signal_date).issubset(set(market.trade_date))):
        raise ValueError("The frozen market calendar is incomplete")
    output.mkdir(parents=True, exist_ok=True)
    rows[SNAPSHOT_COLUMNS].sort_values(["signal_date", "symbol6"]).to_csv(
        output / SOURCE.name, index=False,
        compression={"method": "gzip", "mtime": 0})
    market.to_csv(output / MARKET.name, index=False)
    manifest = {"candidate_rows": len(rows),
                "candidate_days": int(rows.signal_date.nunique()),
                "calendar_days": len(market),
                "excluded_st_at_limit": excluded,
                "labeled_rows": int(rows.second_high.notna().sum()),
                "source_sha256": {str(path): sha256(path) for path in
                                  (PREPARED, SEPTEMBER_PATCH, OCT,
                                   RECOVERED_LABELS, SOURCE_MARKET)}}
    (output / "input_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def run(output: Path) -> None:
    rows = pd.read_csv(output / SOURCE.name,
                       dtype={"signal_date": str, "next_date": str, "symbol6": str})
    market = pd.read_csv(output / MARKET.name, dtype={"trade_date": str})
    picks, folds = replay(rows, market)
    calendar = market.trade_date.tolist()
    positions = {day: index for index, day in enumerate(calendar)}
    labeled_dates = set(rows.loc[rows.second_high.notna(), "signal_date"])
    windows = []
    for day in folds["选股日"]:
        previous = calendar[max(0, positions[day] - 20):positions[day]]
        missing = [date for date in previous if date not in labeled_dates]
        windows.append({"选股日": day, "窗口交易日数": len(previous),
                        "窗口起始日": previous[0] if previous else "",
                        "窗口截止日": previous[-1] if previous else "",
                        "缺失训练日数": len(missing),
                        "缺失训练日": "|".join(missing)})
    folds = folds.merge(pd.DataFrame(windows), on="选股日", validate="one_to_one")
    if not folds["训练日数"].eq(folds["窗口交易日数"] -
                              folds["缺失训练日数"]).all():
        raise ValueError("Training dates disagree with the exchange calendar")
    if (folds.loc[folds["入选数"].gt(0), "训练日数"].ne(20).any() or
            folds.loc[folds["训练日数"].lt(20), "入选数"].ne(0).any()):
        raise ValueError("A prediction crossed an incomplete training window")
    unresolved = picks[picks.actual_0940_return.isna() &
                       picks.signal_date.lt("2026-10-09")]
    if not unresolved.empty:
        raise ValueError("Observed test days have unresolved sale prices")
    daily, monthly = daily_monthly(picks, folds, market)
    daily.to_csv(output / "daily.csv", index=False, float_format="%.6f")
    monthly.to_csv(output / "monthly.csv", index=False, float_format="%.6f")
    folds.to_csv(output / "folds.csv", index=False)
    picks.to_csv(output / "top2_verified.csv", index=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-snapshot", action="store_true")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    if args.build_snapshot:
        print(json.dumps(build_snapshot(args.env_file, args.output),
                         ensure_ascii=False, indent=2))
    run(args.output)


if __name__ == "__main__":
    main()
