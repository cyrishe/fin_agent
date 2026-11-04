"""Cross-check historical 14:40 ST limit signals against the daily K table."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.automl_st_status import load_st_intervals
from scripts.audit_automl_mk_1m_entry_limit import mark_entry_limit
from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.experiment_automl_mk_1m_st_policies import SOURCE
from scripts.prepare_automl_mk_1m_nonst import annotate


REPORT = Path("docs/stock_automl_runs/20261011_mk_1m_three_st_policies")
SEED = 20261011


def fetch_daily(db, rows: pd.DataFrame) -> pd.DataFrame:
    codes = sorted(rows.symbol6.unique())
    parts = []
    for offset in range(0, len(codes), 80):
        chunk = codes[offset:offset + 80]
        marks = ",".join(["%s"] * len(chunk))
        parts.append(query(
            db, "SELECT trade_date AS signal_date, LEFT(stk_code,6) AS symbol6, "
            "preclose AS daily_preclose, open AS daily_open, "
            "high AS daily_high, low AS daily_low, close AS daily_close, "
            "volume AS daily_volume, is_limit_price "
            "FROM kcrp_stock_price WHERE trade_date BETWEEN %s AND %s "
            f"AND LEFT(stk_code,6) IN ({marks})",
            (rows.signal_date.min(), rows.signal_date.max(), *chunk)))
    daily = pd.concat(parts, ignore_index=True)
    daily["signal_date"] = pd.to_datetime(daily.signal_date).dt.strftime("%Y-%m-%d")
    daily["symbol6"] = daily.symbol6.astype(str).str.zfill(6)
    if daily.duplicated(["signal_date", "symbol6"]).any():
        raise ValueError("Daily K has duplicate stock-date keys")
    return daily


def run(source: Path, output: Path, env_file: Path) -> dict:
    source_rows = pd.read_csv(source, dtype={"symbol6": str}, low_memory=False)
    with db_connection(env_file) as db:
        intervals = load_st_intervals(db)
        marked = mark_entry_limit(annotate(source_rows, intervals).rename(
            columns={"t_reference_preclose": "preclose"}))
        signals = marked[marked.historical_st & marked.entry_at_limit].copy()
        daily = fetch_daily(db, signals)
    checked = signals.merge(daily, on=["signal_date", "symbol6"], how="left",
                            validate="one_to_one")
    for field in ("preclose", "signal_price", "limit_price", "daily_preclose",
                  "daily_high", "daily_close", "daily_volume", "is_limit_price"):
        checked[field] = pd.to_numeric(checked[field], errors="coerce")
    if checked.daily_preclose.isna().any():
        raise ValueError("Some limit signals have no daily K")
    tolerance = .001
    checks = {
        "daily_preclose_equals_candidate_preclose":
            checked.daily_preclose.sub(checked.preclose).abs().le(tolerance),
        "signal_price_equals_calculated_limit":
            checked.signal_price.sub(checked.limit_price).abs().le(tolerance),
        "daily_high_equals_calculated_limit":
            checked.daily_high.sub(checked.limit_price).abs().le(tolerance),
        "daily_volume_positive": checked.daily_volume.gt(0),
    }
    counts = {name: int(valid.sum()) for name, valid in checks.items()}
    close_at_limit = checked.daily_close.sub(checked.limit_price).abs().le(tolerance)
    summary = {
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "random_seed": SEED,
        "limit_signals": len(checked),
        "unique_symbols": checked.symbol6.nunique(),
        "checks_passed": counts,
        "daily_close_at_limit": int(close_at_limit.sum()),
        "daily_close_below_limit": int((checked.daily_close.lt(
            checked.limit_price - tolerance)).sum()),
        "daily_is_limit_price_values": {
            str(key): int(value) for key, value in
            checked.is_limit_price.value_counts(dropna=False).items()},
    }
    if any(value != len(checked) for value in counts.values()):
        raise ValueError("A 14:40 limit signal disagrees with its daily K")
    rng = np.random.default_rng(SEED)
    samples = pd.concat([
        group.iloc[rng.choice(len(group), size=min(2, len(group)), replace=False)]
        for _, group in checked.groupby(checked.signal_date.str[:7])
    ], ignore_index=True).sort_values(["signal_date", "symbol6"])
    columns = ["signal_date", "symbol6", "name", "st_type", "ann_date",
               "daily_preclose", "signal_price", "limit_price", "daily_high",
               "daily_close", "is_limit_price", "daily_volume"]
    output.mkdir(parents=True, exist_ok=True)
    samples[columns].to_csv(output / "st_limit_daily_random_sample.csv", index=False)
    (output / "st_limit_daily_audit.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=REPORT)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    print(json.dumps(run(args.source, args.output, args.env_file),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
