"""Show rolling Top1/Top2 and prior-five-day 14:40 market amounts."""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.audit_automl_mk_1m_entry_limit import mark_entry_limit
from scripts.automl_st_status import attach_st_status, load_st_intervals
from scripts.benchmark_automl_1440_inference import FEATURES, db_connection, query
from scripts.experiment_automl_mk_1m_st_policies import SOURCE
from scripts.experiment_automl_second_high_four_class import models
from scripts.run_automl_four_class_pipeline import (
    generate_training_features, generate_training_labels, infer, select_top,
    select_inference_candidates, verify_next_day,
)


PREPARED = Path("docs/stock_automl_runs/20261010_four_class_similar_days/"
                "prepared_candidates.csv.gz")
OCT = Path("outputs/stock_automl/oct_20261008_09_standard/candidates_1440.csv")
SEPTEMBER_PATCH = Path("outputs/stock_automl/sep14_16_rebuilt/candidates_1440.csv")
RECOVERED_LABELS = Path("docs/stock_automl_runs/20261010_four_class_baseline_recent20/"
                        "minute_api_label_audit.csv")
OUT = Path("docs/stock_automl_runs/20261011_aug_oct_single_benchmark")
MARKET = OUT / "market_context.csv"
LABELS = {"ge3": "达标", "1to3": "可接受", "0to1": "中性", "lt0": "错误"}
PRIOR = [f"前{lag}交易日截至14:40成交额（亿元）" for lag in range(1, 6)]


def attach_observed_morning(db, candidates: pd.DataFrame) -> pd.DataFrame:
    records = []
    for next_day, frame in candidates.groupby("next_date"):
        if next_day > "2026-10-09":
            continue
        codes = sorted(frame.symbol6.unique())
        for start in range(0, len(codes), 300):
            chunk = codes[start:start + 300]
            placeholders = ",".join(["%s"] * len(chunk))
            bars = query(db, "SELECT stk_code AS symbol6, bar_end_time, high_price, "
                         "latest_price, is_finalized, is_fallback, source_snapshot_time "
                         "FROM aiia_stock_realtime_minute_snapshot_full "
                         "WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1 "
                         "AND bar_end_time BETWEEN %s AND %s "
                         f"AND stk_code IN ({placeholders})",
                         (next_day, next_day + " 09:31:00",
                          next_day + " 09:40:00", *chunk))
            if bars.empty:
                continue
            bars["symbol6"] = bars.symbol6.astype(str).str.zfill(6)
            bars["bar_end_time"] = pd.to_datetime(bars.bar_end_time)
            for code, group in bars.groupby("symbol6"):
                if (len(group) != 10 or group.bar_end_time.nunique() != 10 or
                        not group.is_finalized.eq(1).all() or
                        not group.is_fallback.eq(0).all() or
                        not pd.to_datetime(group.source_snapshot_time).eq(
                            group.bar_end_time).all()):
                    continue
                high = pd.to_numeric(group.high_price)
                close = pd.to_numeric(group.loc[group.bar_end_time.eq(
                    pd.Timestamp(next_day + " 09:40:00")), "latest_price"])
                if len(close) == 1 and high.gt(0).all() and close.gt(0).all():
                    records.append({"next_date": next_day, "symbol6": code,
                                    "second_high": np.sort(high)[-2],
                                    "close_40": close.iloc[0]})
    return candidates.merge(pd.DataFrame(records, columns=["next_date", "symbol6",
                                                         "second_high", "close_40"]),
                            on=["next_date", "symbol6"], how="left",
                            validate="many_to_one")


def attach_recovered_labels(rows: pd.DataFrame, audit: pd.DataFrame) -> pd.DataFrame:
    recovered = audit.loc[audit.status.eq("complete"),
                          ["signal_date", "symbol6", "second_high", "close_40"]]
    rows = rows.merge(recovered, on=["signal_date", "symbol6"], how="left",
                      suffixes=("", "_recovered"), validate="one_to_one")
    for column in ("second_high", "close_40"):
        recovered_column = f"{column}_recovered"
        conflict = (rows[column].notna() & rows[recovered_column].notna() &
                    ~np.isclose(rows[column], rows[recovered_column], equal_nan=True))
        if conflict.any():
            raise ValueError(f"Conflicting recovered {column} for {int(conflict.sum())} rows")
        rows[column] = rows[column].fillna(rows[recovered_column])
    return rows.drop(columns=["second_high_recovered", "close_40_recovered"])


def load_candidates(env_file: Path) -> pd.DataFrame:
    july = pd.read_csv(SOURCE, dtype={"signal_date": str, "next_date": str,
                                      "symbol6": str}, low_memory=False)
    july = july[july.signal_date.ge("2026-07-01")]
    august = pd.read_csv(PREPARED, dtype={"signal_date": str, "next_date": str,
                                           "symbol6": str})
    september = pd.read_csv(SEPTEMBER_PATCH, dtype={"symbol6": str})
    september = september[september.feature_complete].copy()
    september["signal_date"] = september.signal_date.astype(str)
    september["next_date"] = september.next_date.astype(str)
    august = august[~august.signal_date.isin(september.signal_date.unique())]
    october = pd.read_csv(OCT, dtype={"symbol6": str})
    october = october[october.feature_complete].copy()
    october["signal_date"] = october.signal_date.astype(str)
    october["next_date"] = october.next_date.astype(str)
    with db_connection(env_file) as db:
        september = attach_observed_morning(db, september)
        october = attach_observed_morning(db, october)
        intervals = load_st_intervals(db)
    rows = pd.concat([july, august, september, october], ignore_index=True)
    rows = attach_recovered_labels(
        rows, pd.read_csv(RECOVERED_LABELS, dtype={"symbol6": str}))
    rows = rows.drop(columns=[column for column in ("st_type", "ann_date")
                              if column in rows])
    rows["symbol6"] = rows.symbol6.astype(str).str.zfill(6)
    rows["preclose"] = rows.signal_price / (1 + rows.signal_return)
    rows = mark_entry_limit(attach_st_status(rows, intervals))
    rows["signal_date"] = rows.signal_date.dt.strftime("%Y-%m-%d")
    rows = rows[rows.entry_status_known &
                ~(rows.st_type.isin(("S", "Y")) & rows.entry_at_limit)].copy()
    if rows.duplicated(["signal_date", "symbol6"]).any() or rows[list(FEATURES)].isna().any().any():
        raise ValueError("Duplicate or incomplete candidate features")
    return rows


def replay(rows: pd.DataFrame, market: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = market.trade_date.tolist()
    position = {day: i for i, day in enumerate(dates)}
    candidate_dates = sorted(rows.loc[rows.signal_date.ge("2026-08-01"),
                                       "signal_date"].unique())
    picks = []
    folds = []
    for day in candidate_dates:
        index = position[day]
        previous_dates = dates[max(0, index-20):index]
        training = rows[rows.signal_date.isin(previous_dates) &
                        rows.second_high.notna() & rows.next_date.le(day)].copy()
        training_days = training.signal_date.nunique()
        if training_days != 20:
            folds.append({"选股日": day, "候选股票数": len(rows[rows.signal_date.eq(day)]),
                          "训练日数": training_days, "训练样本数": len(training),
                          "入选数": 0})
            print(f"{day}: skipped, only {training_days}/20 training days", flush=True)
            continue
        model = models()["small_boosted_tree"]
        model.fit(generate_training_features(training), generate_training_labels(training))
        current = rows[rows.signal_date.eq(day)].copy()
        selected = verify_next_day(select_top(infer(
            model, select_inference_candidates(current, day))), current)
        if len(selected):
            picks.append(selected)
        folds.append({"选股日": day, "候选股票数": len(current),
                      "训练日数": training_days,
                      "训练样本数": len(training), "入选数": len(selected)})
        print(f"{day}: {len(training)} train, {len(current)} candidates, "
              f"{len(selected)} picks", flush=True)
    return pd.concat(picks, ignore_index=True), pd.DataFrame(folds)


def daily_monthly(picks: pd.DataFrame, folds: pd.DataFrame,
                  market: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    positions = {day: i for i, day in enumerate(market.trade_date)}
    by_day = {day: group.set_index("selection_rank") for day, group in
              picks.groupby("signal_date")}
    skipped = set(folds.loc[folds["训练日数"].lt(20), "选股日"])
    records = []
    for day in market.loc[market.trade_date.between("2026-08-01", "2026-10-09"),
                          "trade_date"]:
        index = positions[day]
        previous = market.iloc[index-5:index].iloc[::-1]
        complete = (len(previous) == 5 and previous.amount_valid.all() and
                    previous.amount_1440_100m_cny.notna().all())
        mean = previous.amount_1440_100m_cny.mean() if complete else np.nan
        group = by_day.get(day)
        row = {"选股日": day,
               "前一交易日上涨家数": previous.up_count.iloc[0] if len(previous) else np.nan,
               "前一交易日下跌家数": previous.down_count.iloc[0] if len(previous) else np.nan}
        for lag, column in enumerate(PRIOR, start=1):
            row[column] = previous.amount_1440_100m_cny.iloc[lag-1] if len(previous) >= lag else np.nan
        row["前五日平均成交额（亿元）"] = mean
        row["备注"] = ("训练窗口不足" if day in skipped else
                     "候选数据不足" if group is None else
                     "五日成交额缺失" if not complete else
                     "次日待验证" if day == "2026-10-09" else "已验证")
        for rank in (1, 2):
            stock, label, ret = np.nan, np.nan, np.nan
            if group is not None and rank in group.index:
                pick = group.loc[rank]
                stock = pick.symbol6
                label = LABELS.get(pick.actual_class, "待验证" if day == "2026-10-09"
                                   else "结果缺失")
                ret = pick.actual_0940_return * 100 if pd.notna(pick.actual_0940_return) else np.nan
            row[f"Top{rank}股票"] = stock
            row[f"Top{rank}结果"] = label
            row[f"Top{rank} 09:40收益（%）"] = ret
        records.append(row)
    daily = pd.DataFrame(records)
    daily = daily[["选股日", "前一交易日上涨家数", "前一交易日下跌家数",
                   *PRIOR, "Top1股票", "Top1结果", "Top1 09:40收益（%）",
                   "Top2股票", "Top2结果", "Top2 09:40收益（%）",
                   "前五日平均成交额（亿元）", "备注"]]
    monthly = []
    for month, group in daily.groupby(daily["选股日"].str[:7]):
        row = {"月份": month, "选股日数": len(group),
               "模型选股日数": int(group["Top1股票"].notna().sum()),
               "无模型选股日数": int(group["Top1股票"].isna().sum()),
               "五日成交额缺失日数": int(group["前五日平均成交额（亿元）"].isna().sum())}
        for rank in (1, 2):
            returns = group[f"Top{rank} 09:40收益（%）"].dropna()
            row[f"Top{rank}入选笔数"] = int(group[f"Top{rank}股票"].notna().sum())
            row[f"Top{rank}已验证笔数"] = len(returns)
            row[f"Top{rank}平均收益（%）"] = returns.mean()
            row[f"Top{rank}收益简单求和（%）"] = returns.sum()
            for label in LABELS.values():
                row[f"Top{rank}{label}数"] = int(group[f"Top{rank}结果"].eq(label).sum())
        monthly.append(row)
    monthly = pd.DataFrame(monthly)
    ordered = ["月份", "选股日数", "模型选股日数", "无模型选股日数"]
    for rank in (1, 2):
        ordered.extend([f"Top{rank}入选笔数", f"Top{rank}已验证笔数",
                        f"Top{rank}平均收益（%）",
                        f"Top{rank}收益简单求和（%）"])
        ordered.extend(f"Top{rank}{label}数" for label in LABELS.values())
    return daily, monthly[[*ordered, "五日成交额缺失日数"]]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    rows = load_candidates(args.env_file)
    market = pd.read_csv(MARKET, dtype={"trade_date": str})
    picks, folds = replay(rows, market)
    daily, monthly = daily_monthly(picks, folds, market)
    args.output.mkdir(parents=True, exist_ok=True)
    daily.to_csv(args.output / "daily.csv", index=False, float_format="%.6f")
    monthly.to_csv(args.output / "monthly.csv", index=False, float_format="%.6f")
    folds.to_csv(args.output / "folds.csv", index=False)
    picks.to_csv(args.output / "top2_verified.csv", index=False)


if __name__ == "__main__":
    main()
