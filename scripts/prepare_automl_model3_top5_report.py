"""Prepare one fixed model's daily top five for the Chinese Excel review."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from scripts.experiment_automl_tail_three_class import COMPACT_FEATURES, read_samples
from scripts.run_automl_tail_today import fitted_model


TRAIN_START, TRAIN_END = "2026-08-25", "2026-09-21"
FEATURES = ("signal_return", "volume_ratio", "turnover_so_far_pct",
            "float_mv_100m_cny", "volume_4of5_increasing", "ma_bull_5_10_20",
            "price_above_all_ma", "all_intraday_lows_above_ma")


def prepare(original_path: Path, expanded_path: Path, sep30_path: Path,
            today_path: Path, candidates_path: Path, sep30_candidates_path: Path,
            output_path: Path) -> dict:
    original = read_samples(original_path)
    training = original[original.signal_date.between(TRAIN_START, TRAIN_END)].copy()
    if len(training) != 7488 or training.signal_date.nunique() != 20:
        raise ValueError("The original third training window has changed")
    model = fitted_model(training)
    history = pd.concat([read_samples(expanded_path), read_samples(sep30_path)],
                        ignore_index=True)
    if len(history) != 13172 or history.signal_date.nunique() != 37:
        raise ValueError("Expected 37 complete historical signal dates")
    if history.duplicated(["signal_date", "symbol6"]).any():
        raise ValueError("Duplicate historical stock-day")
    pool_counts = history.groupby("signal_date").size().to_dict()
    history["score"] = model.predict_proba(history[list(COMPACT_FEATURES)])[:, 1]
    history = history.sort_values(["signal_date", "score", "symbol6"],
                                  ascending=[True, False, True])
    history["rank"] = history.groupby("signal_date").cumcount().add(1)
    history = history[history["rank"].le(5)].copy()
    audits = pd.concat([
        pd.read_csv(candidates_path, dtype={"symbol6": str},
                    parse_dates=["signal_date"]),
        pd.read_csv(sep30_candidates_path, dtype={"symbol6": str},
                    parse_dates=["signal_date"]),
    ], ignore_index=True)
    history = history.merge(audits[["signal_date", "symbol6", "entry_1450",
                                    "entry_not_near_limit_proxy", "t_final_limit_flag"]],
                            on=["signal_date", "symbol6"], how="left",
                            validate="one_to_one")
    if history[["entry_1450", "t_final_limit_flag"]].isna().any().any():
        raise ValueError("Missing historical entry or limit audit")
    history["stage"] = "训练期内"
    history.loc[history.signal_date.lt(TRAIN_START), "stage"] = "训练期外·8月"
    history.loc[history.signal_date.gt(TRAIN_END), "stage"] = "训练期外·9月"
    history["critical"] = history["class"].eq(-1).astype(int)

    today = pd.read_csv(today_path, dtype={"symbol6": str})
    if len(today) != 178 or today.duplicated("symbol6").any():
        raise ValueError("Expected the saved 178 eligible stocks for 2026-10-08")
    today["score"] = model.predict_proba(today[list(COMPACT_FEATURES)])[:, 1]
    today = today.sort_values(["score", "symbol6"], ascending=[False, True]).head(5).copy()
    today["rank"] = range(1, 6)
    today["signal_date"] = pd.Timestamp("2026-10-08")
    today["next_date"] = pd.NaT
    today["stage"] = "今日·待验证"
    today["class"] = pd.NA
    today["critical"] = pd.NA
    today["target_next_open_return"] = pd.NA
    today["target_next_high5_return"] = pd.NA
    today["target_next_high10_return"] = pd.NA
    today["t_final_limit_flag"] = today["is_limit_price"]
    today["entry_not_near_limit_proxy"] = pd.NA

    combined = pd.concat([history, today], ignore_index=True, sort=False)
    combined = combined.sort_values(["signal_date", "rank"]).reset_index(drop=True)
    if len(combined) != 190 or combined.signal_date.nunique() != 38:
        raise ValueError("Expected 38 dates with five stocks each")
    rows = []
    for _, row in combined.iterrows():
        def number(column):
            return None if pd.isna(row[column]) else float(row[column])

        def integer(column):
            return None if pd.isna(row[column]) else int(row[column])

        rows.append({
            "signal_date": row.signal_date.date().isoformat(),
            "next_date": None if pd.isna(row.next_date) else row.next_date.date().isoformat(),
            "stage": row.stage, "rank": int(row["rank"]),
            "symbol6": row.symbol6, "name": row["name"], "score": number("score"),
            **{name: number(name) for name in FEATURES},
            "entry_1450": number("entry_1450"),
            "next_open_return": number("target_next_open_return"),
            "next_high5_return": number("target_next_high5_return"),
            "next_high10_return": number("target_next_high10_return"),
            "actual_class": integer("class"), "critical": integer("critical"),
            "final_limit": integer("t_final_limit_flag"),
            "entry_not_near_limit_proxy": (
                None if pd.isna(row.entry_not_near_limit_proxy)
                else int(bool(row.entry_not_near_limit_proxy))),
        })
    daily = []
    for day, group in combined.groupby("signal_date"):
        daily.append({
            "signal_date": day.date().isoformat(),
            "stage": group.stage.iloc[0],
            "candidate_pool": 178 if day == pd.Timestamp("2026-10-08") else int(
                pool_counts[day]),
            "top5_strong": None if day == pd.Timestamp("2026-10-08") else int(
                group["class"].eq(1).sum()),
            "top5_neutral": None if day == pd.Timestamp("2026-10-08") else int(
                group["class"].eq(0).sum()),
            "top5_critical": None if day == pd.Timestamp("2026-10-08") else int(
                group["class"].eq(-1).sum()),
            "top5_min_score": float(group.score.min()),
        })
    report = {
        "model": "original third fold, fit once on 2026-08-25..2026-09-21",
        "training_rows": len(training), "history_days": history.signal_date.nunique(),
        "today": "2026-10-08", "rows": rows, "daily": daily,
        "input_sha256": {
            "original": hashlib.sha256(original_path.read_bytes()).hexdigest(),
            "expanded": hashlib.sha256(expanded_path.read_bytes()).hexdigest(),
            "sep30": hashlib.sha256(sep30_path.read_bytes()).hexdigest(),
            "today": hashlib.sha256(today_path.read_bytes()).hexdigest(),
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"history_days": report["history_days"],
                      "total_dates": combined.signal_date.nunique(),
                      "total_top5_rows": len(combined),
                      "today_top5": [r for r in rows if r["signal_date"] == "2026-10-08"]},
                     ensure_ascii=False))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    base = Path("outputs/stock_automl/tail_expanded_august")
    parser.add_argument("--original", type=Path, default=Path(
        "outputs/stock_automl/tail_standard/trainable_1440.csv"))
    parser.add_argument("--expanded", type=Path, default=base / "trainable_1440.csv")
    parser.add_argument("--sep30", type=Path, default=Path(
        "outputs/stock_automl/tail_sep30_check/trainable_1440.csv"))
    parser.add_argument("--today", type=Path, default=Path(
        "outputs/stock_automl/tail_live_review/2026-10-08_all_scored.csv"))
    parser.add_argument("--candidates", type=Path, default=base / "candidates_1440.csv")
    parser.add_argument("--sep30-candidates", type=Path, default=Path(
        "outputs/stock_automl/tail_sep30_check/candidates_1440.csv"))
    parser.add_argument("--output", type=Path, default=Path(
        "outputs/01a11438-ac83-7200-9974-4779891e238b/model3_top5_data.json"))
    args = parser.parse_args()
    prepare(args.original, args.expanded, args.sep30, args.today,
            args.candidates, args.sep30_candidates, args.output)
