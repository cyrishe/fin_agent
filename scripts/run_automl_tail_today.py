"""Score one completed 14:40 session with the existing eight-factor model.

The model is fitted on already labeled signal dates strictly before the target
date. Today's 14:50 price and final limit flag are joined only after ranking.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import dotenv_values
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from scripts.build_automl_tail_standard import (
    assemble, entry_bar, intraday_to_1440, load_daily, prior_profile, signal_bar,
)
from scripts.experiment_automl_1450_grid import frame, read_only_db
from scripts.experiment_automl_tail_three_class import BINARY, COMPACT_FEATURES, read_samples


def fitted_model(samples: pd.DataFrame):
    numeric = [name for name in COMPACT_FEATURES if name not in BINARY]
    binary = [name for name in COMPACT_FEATURES if name in BINARY]
    model = make_pipeline(ColumnTransformer([
        ("numeric", StandardScaler(), numeric),
        ("binary", "passthrough", binary),
    ]), LogisticRegression(C=0.2, max_iter=2000))
    model.fit(samples[list(COMPACT_FEATURES)], samples["class"].ne(-1).astype(int))
    return model


def score(day: str, history_start: str, samples_path: Path, env_path: Path,
          output_dir: Path):
    cfg = dotenv_values(env_path)
    url = cfg.get("SIMPLE_BI_PLATFORM_DB_URL") or cfg.get("PLATFORM_DB_URL")
    if not url:
        raise ValueError("Missing authorized database URL")
    os.environ["SIMPLE_BI_PLATFORM_DB_URL"] = url
    training = read_samples(samples_path)
    if training.signal_date.max() >= pd.Timestamp(day):
        raise ValueError("Training labels must precede the signal date")
    model = fitted_model(training)

    with read_only_db() as conn:
        daily, value = load_daily(conn, history_start=history_start, end=day)
        profile, calendar = prior_profile(daily, value)
        current = pd.Timestamp(day)
        earlier = [date for date in calendar if date < current]
        if not earlier or current not in calendar:
            raise ValueError("Signal date or prior trading date absent from daily source")
        prior = max(earlier)
        today_preclose = daily[daily.date.eq(current)][
            ["date", "symbol6", "preclose"]].rename(columns={
                "date": "signal_date", "preclose": "t_reference_preclose"})
        bars = signal_bar(conn, day)
        exact = pd.to_datetime(bars.signal_source_time).eq(
            pd.Timestamp(f"{day} 14:40:00"))
        bars = bars[exact & bars.signal_fallback.eq(0) & bars.signal_finalized.eq(1)]
        signal = bars.merge(profile[profile.signal_date.eq(current)],
                            on=["signal_date", "symbol6"], how="left",
                            validate="one_to_one")
        signal = signal.merge(today_preclose, on=["signal_date", "symbol6"],
                              how="left", validate="one_to_one")
        signal["signal_price"] = pd.to_numeric(signal.signal_price, errors="coerce")
        signal["t_reference_preclose"] = pd.to_numeric(
            signal.t_reference_preclose, errors="coerce")
        signal["signal_return"] = signal.signal_price / signal.t_reference_preclose - 1
        signal = signal[signal.signal_return.between(0.03, 0.06, inclusive="both")].copy()
        if signal.empty:
            raise ValueError("No 14:40 candidates in the 3%-6% range")
        signal["next_date"] = pd.NaT  # Placeholder; no T+1 data read.
        minute = intraday_to_1440(conn, day, sorted(signal.symbol6.tolist()))

        # assemble() owns the production feature calculations. Empty result-side
        # inputs prevent T 14:50 or T+1 values from entering the score path.
        entry = signal[["signal_date", "symbol6"]].copy()
        entry["entry_1450"] = np.nan
        entry["entry_fallback"] = 1
        entry["entry_finalized"] = 0
        morning = pd.DataFrame({"next_date": pd.Series(dtype="datetime64[ns]"),
                                "symbol6": pd.Series(dtype=str)})
        for column in ("next_open", "next_high5", "next_high10", "bars5",
                       "bars10", "volume5", "morning_fallback", "morning_finalized"):
            morning[column] = pd.Series(dtype=float)
        rows = assemble(signal, minute, entry, morning)
        prior_ok = rows.prior_date.eq(prior) & rows.value_date.eq(prior)
        eligible = rows[rows.feature_complete.fillna(False) & prior_ok &
                        rows.limit_buffer.notna() &
                        rows.signal_return.lt(rows.limit_buffer)].copy()
        if eligible.empty:
            raise ValueError("No complete, tradable 14:40 feature rows")
        for name in BINARY:
            eligible[name] = eligible[name].astype(int)
        eligible["binary_pass_score"] = model.predict_proba(
            eligible[list(COMPACT_FEATURES)])[:, 1]
        eligible = eligible.sort_values(["binary_pass_score", "symbol6"],
                                         ascending=[False, True]).reset_index(drop=True)
        eligible["rank"] = eligible.index + 1

        # These after-14:40 fields are audit only; they cannot change ranking.
        entry_audit = entry_bar(conn, day)[["signal_date", "symbol6", "entry_1450",
                                             "entry_fallback", "entry_finalized"]]
        final_audit = daily[daily.date.eq(current)][["symbol6", "is_limit_price"]]
        eligible = eligible.drop(columns=["entry_1450", "entry_fallback",
                                           "entry_finalized"]).merge(
                                               entry_audit, on=["signal_date", "symbol6"],
                                               how="left", validate="one_to_one").merge(
                                                   final_audit, on="symbol6", how="left",
                                                   validate="one_to_one")

    numeric = ("signal_return", "volume_ratio", "turnover_so_far_pct",
               "float_mv_100m_cny", "binary_pass_score", "entry_1450")
    for name in numeric:
        eligible[name] = pd.to_numeric(eligible[name], errors="coerce")
    selected = eligible.head(5)
    columns = ["rank", "symbol6", "name", "binary_pass_score", "signal_return",
               "volume_ratio", "turnover_so_far_pct", "float_mv_100m_cny",
               *BINARY, "signal_price", "t_reference_preclose", "entry_1450",
               "entry_fallback", "entry_finalized", "is_limit_price"]
    output_dir.mkdir(parents=True, exist_ok=True)
    eligible[columns].to_csv(output_dir / f"{day}_all_scored.csv", index=False)
    selected[columns].to_csv(output_dir / f"{day}_top5.csv", index=False)
    result = {
        "signal_date": day, "asof": f"{day} 14:40:00",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "prior_date_for_features": prior.date().isoformat(),
        "training_signal_period": [training.signal_date.min().date().isoformat(),
                                   training.signal_date.max().date().isoformat()],
        "training_rows": len(training),
        "training_pass_rate": float(training["class"].ne(-1).mean()),
        "signal_bars_exact": len(bars), "raw_3_to_6pct": len(signal),
        "feature_complete": int(rows.feature_complete.fillna(False).sum()),
        "eligible": len(eligible),
        "scores_are_calibrated": False,
        "training_sha256": hashlib.sha256(samples_path.read_bytes()).hexdigest(),
        "top5": selected[columns].replace({np.nan: None}).to_dict(orient="records"),
    }
    out = output_dir / f"{day}_summary.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    print(json.dumps({k: v for k, v in result.items() if k != "top5"},
                     ensure_ascii=False, default=str))
    print(selected[columns].to_string(index=False))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signal-date", required=True)
    parser.add_argument("--history-start", default="2026-08-24")
    parser.add_argument("--samples", type=Path, default=Path(
        "outputs/stock_automl/tail_standard/trainable_1440.csv"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output-dir", type=Path, default=Path(
        "outputs/stock_automl/tail_live_review"))
    args = parser.parse_args()
    score(args.signal_date, args.history_start, args.samples,
          args.env_file, args.output_dir)
