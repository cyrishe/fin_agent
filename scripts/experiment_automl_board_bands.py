"""Isolated April-train/May-test replay of board-specific entry and target bands."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import joblib

from scripts.benchmark_automl_1440_inference import FEATURES, db_connection
from scripts.automl_st_status import load_st_intervals
from scripts.experiment_automl_mk_1m_st_policies import make_cohorts
from scripts.experiment_automl_second_high_four_class import models
from scripts.report_automl_tradable_st_new_exit_market import NEXT_CLOSES, sell


CLASSES = ("wrong", "neutral", "acceptable", "target")
LABELS = {"wrong": "错误", "neutral": "中性", "acceptable": "可接受",
          "target": "达标"}
SOURCES = {
    "2025": {
        "legacy": Path("outputs/stock_automl/mk_1m_2025_continuous/cohort/trainable_candidates.csv.gz"),
        "board": Path("outputs/stock_automl/board_band_2025/trainable_candidates.csv.gz"),
        "snapshots": Path("outputs/stock_automl/mk_1m_2025_continuous/cohort/minute_daily_snapshots.csv.gz"),
    },
    "2026": {
        "legacy": Path("outputs/stock_automl/mk_1m_jan_jul/trainable_candidates.csv.gz"),
        "board": Path("outputs/stock_automl/board_band_2026/trainable_candidates.csv.gz"),
        "snapshots": Path("outputs/stock_automl/mk_1m_jan_jul/minute_daily_snapshots.csv.gz"),
    },
}


def is_wide_board(symbols: pd.Series) -> pd.Series:
    code = symbols.astype(str).str.zfill(6)
    return code.str.startswith(("30", "68", "4", "8", "92"))


def outcome_class(returns: pd.Series, symbols: pd.Series,
                  scheme: str) -> pd.Series:
    """Use the same four semantic classes with scheme-specific thresholds."""
    if scheme not in ("legacy", "board"):
        raise ValueError(f"Unknown scheme: {scheme}")
    if scheme == "legacy":
        acceptable = pd.Series(.01, index=returns.index)
        target = pd.Series(.03, index=returns.index)
    else:
        wide = is_wide_board(symbols)
        acceptable = pd.Series(np.where(wide, .01, .005), index=returns.index)
        target = pd.Series(np.where(wide, .04, .02), index=returns.index)
    values = np.select([returns.lt(0), returns.lt(acceptable),
                        returns.lt(target)], CLASSES[:3], default=CLASSES[3])
    return pd.Series(values, index=returns.index)


def select_top(scored: pd.DataFrame) -> pd.DataFrame:
    eligible = scored[scored.predicted_class.isin(("target", "acceptable"))].copy()
    eligible["class_priority"] = eligible.predicted_class.map(
        {"target": 0, "acceptable": 1})
    eligible["within_class_score"] = np.where(
        eligible.predicted_class.eq("target"), eligible.p_target,
        eligible.p_target + eligible.p_acceptable)
    eligible = eligible.sort_values(
        ["signal_date", "class_priority", "within_class_score", "symbol6"],
        ascending=[True, True, False, True])
    eligible["selection_rank"] = eligible.groupby("signal_date").cumcount() + 1
    return eligible[eligible.selection_rank.le(2)].copy()


def run_one(year: str, scheme: str, intervals: pd.DataFrame,
            output: Path) -> dict:
    source = SOURCES[year][scheme]
    rows = pd.read_csv(source, dtype={"symbol6": str}, low_memory=False)
    rows = rows[rows.signal_date.str[:7].isin((f"{year}-04", f"{year}-05"))].copy()
    cohorts, _ = make_cohorts(rows, intervals)
    eligible = cohorts["只去掉14:40涨停ST"]
    train = eligible[eligible.signal_date.str[:7].eq(f"{year}-04") &
                     eligible.label_complete].copy()
    test = eligible[eligible.signal_date.str[:7].eq(f"{year}-05")].copy()
    calendar = sorted(pd.read_csv(SOURCES[year]["snapshots"],
                                  usecols=["signal_date"]).signal_date.unique())
    test_days = [day for day in calendar if day.startswith(f"{year}-05")]
    if not test_days or train.empty or test.empty or train.signal_date.nunique() < 18:
        raise ValueError(f"Incomplete April/May experiment: {year}, {scheme}")
    if train.next_date.gt(test_days[0]).any() or train[list(FEATURES)].isna().any().any():
        raise ValueError("Training labels or features are not mature at first test signal")
    training_returns = train.second_high / train.signal_price - 1
    train["target_class"] = outcome_class(training_returns, train.symbol6, scheme)
    if set(train.target_class) != set(CLASSES):
        raise ValueError("April training data lacks a target class")
    feature_columns = list(FEATURES)
    if scheme == "board":
        # The label thresholds depend on the board, so the model needs that
        # decision-time fact as an input. The legacy control stays seven-factor.
        train["wide_board"] = is_wide_board(train.symbol6).astype(int)
        test["wide_board"] = is_wide_board(test.symbol6).astype(int)
        feature_columns.append("wide_board")
    model = models()["small_boosted_tree"]
    model.fit(train[feature_columns], train.target_class)
    probabilities = model.predict_proba(test[feature_columns])
    test["predicted_class"] = np.asarray(model.classes_)[probabilities.argmax(axis=1)]
    for label in CLASSES:
        test[f"p_{label}"] = probabilities[:, list(model.classes_).index(label)]
    picks = select_top(test)
    if picks.entry_at_limit.any():
        raise ValueError("Selected an ST stock already at its 14:40 price cap")
    picks["actual_second_high_return"] = picks.second_high / picks.signal_price - 1
    picks["actual_0940_return"] = picks.close_40 / picks.signal_price - 1
    known = picks.second_high.notna()
    picks["actual_class"] = "未验证"
    picks.loc[known, "actual_class"] = outcome_class(
        picks.loc[known, "actual_second_high_return"],
        picks.loc[known, "symbol6"], scheme).map(LABELS)
    picks["sell_return"] = np.nan
    picks["sell_time"] = ""
    for index, pick in picks.loc[picks[NEXT_CLOSES].notna().all(axis=1)].iterrows():
        time, _, ret = sell(float(pick.signal_price),
                            pick[NEXT_CLOSES].astype(float).tolist())
        picks.loc[index, "sell_return"] = ret
        picks.loc[index, "sell_time"] = time
    output.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, output / "april_model.joblib")
    picks.to_csv(output / "top2.csv", index=False)
    daily = pd.DataFrame({"选股日": test_days})
    for rank in (1, 2):
        part = picks[picks.selection_rank.eq(rank)].copy()
        part = part.rename(columns={"signal_date": "选股日", "symbol6": f"Top{rank}代码",
                                    "name": f"Top{rank}股票", "actual_class": f"Top{rank}结果",
                                    "actual_0940_return": f"Top{rank}固定09:40收益",
                                    "sell_return": f"Top{rank}分段卖出收益"})
        daily = daily.merge(part[["选股日", f"Top{rank}代码", f"Top{rank}股票",
                                  f"Top{rank}结果", f"Top{rank}固定09:40收益",
                                  f"Top{rank}分段卖出收益"]], on="选股日", how="left",
                            validate="one_to_one")
    daily.to_csv(output / "daily.csv", index=False)
    def rank_summary(rank: int) -> dict:
        group = picks[picks.selection_rank.eq(rank)]
        return {"picks": len(group), "target": int(group.actual_class.eq("达标").sum()),
                "acceptable": int(group.actual_class.eq("可接受").sum()),
                "neutral": int(group.actual_class.eq("中性").sum()),
                "wrong": int(group.actual_class.eq("错误").sum()),
                "unknown": int(group.actual_class.eq("未验证").sum()),
                "mean_0940_pct": round(float(group.actual_0940_return.mean()*100), 4),
                "mean_staged_pct": round(float(group.sell_return.mean()*100), 4),
                "main_board": int((~is_wide_board(group.symbol6)).sum()),
                "wide_board": int(is_wide_board(group.symbol6).sum())}
    summary = {"year": year, "scheme": scheme,
               "train_month": f"{year}-04", "test_month": f"{year}-05",
               "train_days": int(train.signal_date.nunique()),
               "train_rows": len(train), "test_days": len(test_days),
               "test_candidates": len(test), "north_exchange_rows": 0,
               "features": feature_columns,
               "train_class_counts": train.target_class.value_counts().to_dict(),
               "top1": rank_summary(1), "top2": rank_summary(2)}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False,
                                                      indent=2) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path,
                        default=Path("docs/stock_automl_runs/20261011_board_band_april_may"))
    args = parser.parse_args()
    with db_connection(Path(".env")) as db:
        intervals = load_st_intervals(db)
    for year in ("2025", "2026"):
        for scheme in ("legacy", "board"):
            summary = run_one(year, scheme, intervals, args.output / year / scheme)
            print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
