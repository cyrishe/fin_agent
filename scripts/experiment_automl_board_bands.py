"""Isolated April-train/May-test replay of board-specific entry and target bands."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import joblib

from scripts.benchmark_automl_1440_inference import FEATURES, db_connection, query
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
        "board_window": Path("outputs/stock_automl/main_3to6_wide_4to8_2025/trainable_candidates.csv.gz"),
        "snapshots": Path("outputs/stock_automl/mk_1m_2025_continuous/cohort/minute_daily_snapshots.csv.gz"),
    },
    "2026": {
        "legacy": Path("outputs/stock_automl/mk_1m_jan_jul/trainable_candidates.csv.gz"),
        "board": Path("outputs/stock_automl/board_band_2026/trainable_candidates.csv.gz"),
        "board_window": Path("outputs/stock_automl/main_3to6_wide_4to8_2026/trainable_candidates.csv.gz"),
        "snapshots": Path("outputs/stock_automl/mk_1m_jan_jul/minute_daily_snapshots.csv.gz"),
    },
}


def is_wide_board(symbols: pd.Series) -> pd.Series:
    code = symbols.astype(str).str.zfill(6)
    return code.str.startswith(("30", "68", "4", "8", "92"))


def outcome_class(returns: pd.Series, symbols: pd.Series,
                  scheme: str) -> pd.Series:
    """Use the same four semantic classes with scheme-specific thresholds."""
    if scheme not in ("legacy", "board", "board_window"):
        raise ValueError(f"Unknown scheme: {scheme}")
    if scheme == "legacy":
        acceptable = pd.Series(.01, index=returns.index)
        target = pd.Series(.03, index=returns.index)
    elif scheme == "board_window":
        wide = is_wide_board(symbols)
        acceptable = pd.Series(.01, index=returns.index)
        target = pd.Series(np.where(wide, .04, .03), index=returns.index)
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


def adjust_outcome_prices(rows: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """Put T+1 raw minute prices on the T price basis for labels/backtests only."""
    daily = daily.copy()
    daily["date"] = pd.to_datetime(daily.date).dt.strftime("%Y-%m-%d")
    daily["symbol6"] = daily.symbol6.astype(str).str.zfill(6)
    daily["close"] = pd.to_numeric(daily.close)
    daily["preclose"] = pd.to_numeric(daily.preclose)
    daily["volume"] = pd.to_numeric(daily.volume)
    t_close = daily[["date", "symbol6", "close"]].rename(
        columns={"date": "signal_date", "close": "t_final_close"})
    next_preclose = daily[["date", "symbol6", "preclose", "volume"]].rename(
        columns={"date": "next_date", "preclose": "next_reference_preclose",
                 "volume": "next_daily_volume"})
    result = rows.merge(t_close, on=["signal_date", "symbol6"], how="left",
                        validate="many_to_one")
    result = result.merge(next_preclose, on=["next_date", "symbol6"], how="left",
                          validate="many_to_one")
    result["next_suspended"] = (result.second_high.isna() & result.close_40.isna() &
                                result.next_daily_volume.eq(0))
    result["label_complete"] = result.label_complete | result.next_suspended
    result["outcome_factor"] = result.t_final_close / result.next_reference_preclose
    if (result.loc[result.label_complete, "outcome_factor"].isna().any() or
            not result.loc[result.label_complete, "outcome_factor"].gt(0).all()):
        raise ValueError("A mature next-morning label lacks its price-basis adjustment")
    result["adjusted_second_high"] = result.second_high * result.outcome_factor
    result["adjusted_close_40"] = result.close_40 * result.outcome_factor
    for column in ("adjusted_second_high", "adjusted_close_40"):
        result.loc[result.next_suspended, column] = (
            result.loc[result.next_suspended, "signal_price"])
    for column in NEXT_CLOSES:
        result[f"adjusted_{column}"] = result[column] * result.outcome_factor
    return result


def attach_outcome_adjustment(rows: pd.DataFrame) -> pd.DataFrame:
    with db_connection(Path(".env")) as db:
        daily = query(db, "SELECT trade_date AS date,LEFT(stk_code,6) AS symbol6,"
                      "close,preclose,volume FROM kcrp_stock_price "
                      "WHERE trade_date BETWEEN %s AND %s",
                      (rows.signal_date.min(), rows.next_date.dropna().max()))
    return adjust_outcome_prices(rows, daily)


def run_one(year: str, scheme: str, intervals: pd.DataFrame,
            output: Path, test_through_month: int = 5,
            rolling20: bool = False) -> dict:
    source = SOURCES[year][scheme]
    rows = pd.read_csv(source, dtype={"symbol6": str}, low_memory=False)
    test_months = tuple(f"{year}-{month:02d}" for month in range(5, test_through_month + 1))
    rows = rows[rows.signal_date.str[:7].isin((f"{year}-04", *test_months))].copy()
    rows = attach_outcome_adjustment(rows)
    cohorts, _ = make_cohorts(rows, intervals)
    eligible = cohorts["只去掉14:40涨停ST"]
    calendar = sorted(pd.read_csv(SOURCES[year]["snapshots"],
                                  usecols=["signal_date"]).signal_date.unique())
    test_days = [day for day in calendar if day[:7] in test_months and day < calendar[-1]]
    test = eligible[eligible.signal_date.isin(test_days)].copy()
    if not test_days or test.empty:
        raise ValueError(f"Incomplete April-to-test experiment: {year}, {scheme}")
    feature_columns = list(FEATURES)
    if scheme != "legacy":
        # The label thresholds depend on the board, so the model needs that
        # decision-time fact as an input. The legacy control stays seven-factor.
        eligible["wide_board"] = is_wide_board(eligible.symbol6).astype(int)
        test["wide_board"] = is_wide_board(test.symbol6).astype(int)
        feature_columns.append("wide_board")
    eligible["target_class"] = pd.NA
    mature = eligible.label_complete
    eligible.loc[mature, "target_class"] = outcome_class(
        eligible.loc[mature, "adjusted_second_high"] /
        eligible.loc[mature, "signal_price"] - 1,
        eligible.loc[mature, "symbol6"], scheme)

    def fit_and_select(training: pd.DataFrame, current: pd.DataFrame,
                       day: str) -> tuple[pd.DataFrame, object]:
        if (training.empty or training[list(FEATURES)].isna().any().any()
                or training.signal_date.nunique() < (20 if rolling20 else 18)
                or training.next_date.gt(day).any()
                or set(training.target_class) != set(CLASSES)):
            raise ValueError(f"Incomplete or immature training fold: {year}, {scheme}, {day}")
        model = models()["small_boosted_tree"]
        model.fit(training[feature_columns], training.target_class.astype(str))
        # Only decision-time fields cross into scoring and ranking. Outcomes
        # are attached after the selected keys have already been fixed.
        scored = current[["signal_date", "symbol6", "name", "signal_price",
                          *feature_columns]].copy()
        if scored.empty:
            scored["predicted_class"] = pd.Series(dtype=str)
            for label in CLASSES:
                scored[f"p_{label}"] = pd.Series(dtype=float)
            return select_top(scored), model
        probabilities = model.predict_proba(scored[feature_columns])
        scored["predicted_class"] = np.asarray(model.classes_)[probabilities.argmax(axis=1)]
        for label in CLASSES:
            scored[f"p_{label}"] = probabilities[:, list(model.classes_).index(label)]
        return select_top(scored), model

    folds = []
    if rolling20:
        selections = []
        for day in test_days:
            position = calendar.index(day)
            training_days = calendar[position-20:position]
            training = eligible[eligible.signal_date.isin(training_days) &
                                eligible.label_complete].copy()
            if len(training_days) != 20 or training.signal_date.nunique() != 20:
                raise ValueError(f"Nonconsecutive 20-day training window: {day}")
            current = test[test.signal_date.eq(day)]
            selected, _ = fit_and_select(training, current, day)
            selections.append(selected)
            folds.append({"day": day, "train_start": training_days[0],
                          "train_end": training_days[-1], "train_rows": len(training),
                          "test_candidates": len(current), "picks": len(selected)})
            print(f"{year} {scheme} {day}: {len(training)} train, "
                  f"{len(current)} candidates, {len(selected)} picks", flush=True)
        picks = pd.concat(selections, ignore_index=True)
        train = None
    else:
        train = eligible[eligible.signal_date.str[:7].eq(f"{year}-04") &
                         eligible.label_complete].copy()
        picks, model = fit_and_select(train, test, test_days[0])
    audit_columns = ["signal_date", "symbol6", "next_date", "preclose",
                     "second_high", "close_40", *NEXT_CLOSES,
                     "adjusted_second_high", "adjusted_close_40",
                     *(f"adjusted_{column}" for column in NEXT_CLOSES),
                     "t_final_close", "next_reference_preclose", "outcome_factor",
                     "next_suspended", "entry_at_limit", "historical_st"]
    picks = picks.merge(test[audit_columns], on=["signal_date", "symbol6"],
                        validate="one_to_one")
    if picks.entry_at_limit.any():
        raise ValueError("Selected an ST stock already at its 14:40 price cap")
    picks["raw_0940_return"] = picks.close_40 / picks.signal_price - 1
    picks["actual_second_high_return"] = (
        picks.adjusted_second_high / picks.signal_price - 1)
    picks["actual_0940_return"] = picks.adjusted_close_40 / picks.signal_price - 1
    known = picks.second_high.notna()
    picks["actual_class"] = "未验证"
    picks.loc[known, "actual_class"] = outcome_class(
        picks.loc[known, "actual_second_high_return"],
        picks.loc[known, "symbol6"], scheme).map(LABELS)
    picks["sell_return"] = np.nan
    picks["sell_time"] = ""
    adjusted_closes = [f"adjusted_{column}" for column in NEXT_CLOSES]
    for index, pick in picks.loc[picks[adjusted_closes].notna().all(axis=1)].iterrows():
        time, _, ret = sell(float(pick.signal_price),
                            pick[adjusted_closes].astype(float).tolist())
        picks.loc[index, "sell_return"] = ret
        picks.loc[index, "sell_time"] = time
    picks["suspension_zero"] = picks.next_suspended
    stopped = picks.suspension_zero
    picks.loc[stopped, "actual_0940_return"] = 0.0
    picks.loc[stopped, "actual_second_high_return"] = 0.0
    picks.loc[stopped, "actual_class"] = "中性"
    picks.loc[stopped, "sell_return"] = 0.0
    picks.loc[stopped, "sell_time"] = "次晨停牌"
    output.mkdir(parents=True, exist_ok=True)
    if rolling20:
        pd.DataFrame(folds).to_csv(output / "folds.csv", index=False)
    else:
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
    def rank_summary(rank: int, frame: pd.DataFrame | None = None) -> dict:
        group = (picks if frame is None else frame)
        group = group[group.selection_rank.eq(rank)]
        return {"picks": len(group), "target": int(group.actual_class.eq("达标").sum()),
                "acceptable": int(group.actual_class.eq("可接受").sum()),
                "neutral": int(group.actual_class.eq("中性").sum()),
                "wrong": int(group.actual_class.eq("错误").sum()),
                "unknown": int(group.actual_class.eq("未验证").sum()),
                "mean_0940_pct": round(float(group.actual_0940_return.mean()*100), 4)
                if group.actual_0940_return.notna().any() else None,
                "mean_staged_pct": round(float(group.sell_return.mean()*100), 4)
                if group.sell_return.notna().any() else None,
                "main_board": int((~is_wide_board(group.symbol6)).sum()),
                "wide_board": int(is_wide_board(group.symbol6).sum())}
    summary = {"year": year, "scheme": scheme,
               "training_mode": "rolling20" if rolling20 else "fixed_april",
               "test_months": list(test_months), "test_days": len(test_days),
               "test_candidates": len(test), "north_exchange_rows": 0,
               "outcome_basis": "T+1 raw minute price times T final close / T+1 reference preclose",
               "features": feature_columns,
               "top1": rank_summary(1), "top2": rank_summary(2),
               "monthly": {month: {"test_days": sum(day.startswith(month) for day in test_days),
                                  "top1": rank_summary(1, picks[picks.signal_date.str[:7].eq(month)]),
                                  "top2": rank_summary(2, picks[picks.signal_date.str[:7].eq(month)])}
                           for month in test_months}}
    if rolling20:
        summary["train_rows_mean"] = round(float(pd.DataFrame(folds).train_rows.mean()), 2)
        summary["train_window_days"] = 20
    else:
        summary["train_month"] = f"{year}-04"
        summary["train_days"] = int(train.signal_date.nunique())
        summary["train_rows"] = len(train)
        summary["train_class_counts"] = train.target_class.value_counts().to_dict()
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False,
                                                      indent=2) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path,
                        default=Path("docs/stock_automl_runs/20261011_board_band_april_may"))
    parser.add_argument("--scheme", action="append",
                        choices=("legacy", "board", "board_window"))
    parser.add_argument("--year", action="append", choices=("2025", "2026"))
    parser.add_argument("--test-through-month", type=int, choices=(5, 6, 7), default=5)
    parser.add_argument("--rolling20", action="store_true")
    args = parser.parse_args()
    with db_connection(Path(".env")) as db:
        intervals = load_st_intervals(db)
    for year in (args.year or ("2025", "2026")):
        for scheme in (args.scheme or ("legacy", "board")):
            summary = run_one(year, scheme, intervals, args.output / year / scheme,
                              args.test_through_month, args.rolling20)
            print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
