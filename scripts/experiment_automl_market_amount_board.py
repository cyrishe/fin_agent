"""Walk-forward tests of a five-day 14:40 market-amount gate and board features.

This is a research replay of the existing 20-day, seven-factor ST-tradable
cohort. Every market feature is available by the signal day's 14:40 cutoff.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.benchmark_automl_1440_inference import FEATURES, db_connection
from scripts.experiment_automl_mk_1m_st_policies import (
    POLICIES, SOURCE, make_cohorts, settle_suspensions,
)
from scripts.experiment_automl_second_high_four_class import CLASSES, four_class, models
from scripts.automl_st_status import load_st_intervals
from scripts.report_automl_tradable_st_new_exit_market import PICKS
from scripts.run_automl_four_class_pipeline import select_top, verify_next_day


AMOUNT = Path("docs/stock_automl_runs/20261011_mk_1m_three_st_policies/"
              "tradable_st_new_exit_market/market_amount_1440.csv")
OUT = Path("docs/stock_automl_runs/20261011_mk_1m_market_amount_board")
ARMS = ("amount_binary", "amount_tier", "high_train", "high_board_feature",
        "high_board_models")
BOARD_NAMES = ("SZ000", "SZOther", "ChiNext", "SHMain", "STAR")
BOARD_COLUMNS = tuple(f"board_{name}" for name in BOARD_NAMES)


def market_by_day(amount: pd.DataFrame) -> pd.DataFrame:
    market = amount.sort_values("trade_date").reset_index(drop=True).copy()
    if market.trade_date.duplicated().any() or len(market) != 139:
        raise ValueError("Expected 139 distinct source trading days")
    market["amount_avg5_1440_100m"] = market.amount_1440_100m_cny.rolling(5).mean()
    market["prior_avg5_1440_100m"] = market.amount_1440_100m_cny.shift(1).rolling(5).mean()
    market["high_amount"] = market.amount_avg5_1440_100m.ge(20000)
    market["prior_high_amount"] = market.prior_avg5_1440_100m.ge(20000)
    market["amount_binary"] = market.high_amount.astype(int)
    # Ordinal buckets: <20k is 1; [20k,25k) is 2, then +1 per 5k (亿元).
    market["amount_tier"] = np.where(
        market.amount_avg5_1440_100m.lt(20000), 1,
        2 + np.floor((market.amount_avg5_1440_100m - 20000) / 5000))
    market.loc[market.amount_avg5_1440_100m.isna(), ["amount_binary", "amount_tier"]] = np.nan
    return market


def board_of(symbol6: str) -> str:
    if symbol6.startswith("000"):
        return "SZ000"
    if symbol6.startswith(("001", "002", "003")):
        return "SZOther"
    if symbol6.startswith(("300", "301", "302")):
        return "ChiNext"
    if symbol6.startswith(("600", "601", "603", "605")):
        return "SHMain"
    if symbol6.startswith(("688", "689")):
        return "STAR"
    raise ValueError(f"Unclassified A-share board: {symbol6}")


def add_asof_features(rows: pd.DataFrame, market: pd.DataFrame) -> pd.DataFrame:
    columns = ["trade_date", "amount_1440_100m_cny", "amount_avg5_1440_100m",
               "high_amount", "amount_binary", "amount_tier"]
    result = rows.merge(market[columns], left_on="signal_date", right_on="trade_date",
                        validate="many_to_one")
    if len(result) != len(rows):
        raise ValueError("Missing as-of market amount for stock candidates")
    result["board"] = result.symbol6.map(board_of)
    for name in BOARD_NAMES:
        result[f"board_{name}"] = result.board.eq(name).astype(int)
    return result.drop(columns="trade_date")


def feature_columns(arm: str) -> tuple[str, ...]:
    if arm == "amount_binary":
        return (*FEATURES, "amount_binary")
    if arm == "amount_tier":
        return (*FEATURES, "amount_tier")
    if arm == "high_board_feature":
        return (*FEATURES, *BOARD_COLUMNS)
    if arm in ("high_train", "high_board_models"):
        return FEATURES
    raise ValueError(arm)


def score(model, candidates: pd.DataFrame, features: tuple[str, ...]) -> pd.DataFrame:
    scored = candidates[["signal_date", "next_date", "symbol6", "name",
                         "signal_price", "board"]].copy()
    if scored.empty:
        return scored
    probability = model.predict_proba(candidates[list(features)])
    scored["predicted_class"] = np.asarray(model.classes_)[probability.argmax(axis=1)]
    for label in CLASSES:
        scored[f"p_{label}"] = (probability[:, list(model.classes_).index(label)]
                                 if label in model.classes_ else 0.)
    return scored


def one_fold(rows: pd.DataFrame, market: pd.DataFrame, dates: list[str],
             index: int, arm: str) -> tuple[pd.DataFrame, dict]:
    day = dates[index]
    previous_dates = dates[index-20:index]
    train = rows[rows.signal_date.isin(previous_dates) & rows.second_high.notna()].copy()
    if train.next_date.gt(day).any() or train.signal_date.nunique() != 20:
        raise ValueError(f"Immature or incomplete 20-date training window: {day}")
    if arm.startswith("high_"):
        train = train[train.high_amount].copy()
    current = rows[rows.signal_date.eq(day)].copy()
    if arm.startswith("high_") and not bool(market.loc[
            market.trade_date.eq(day), "high_amount"].iloc[0]):
        return current.iloc[0:0].copy(), {"day": day, "train_rows": len(train),
                                            "candidates": 0, "picks": 0, "skipped": True}
    features = feature_columns(arm)
    if train[list(features)].isna().any().any() or current[list(features)].isna().any().any():
        raise ValueError(f"Incomplete as-of training or inference feature: {arm} {day}")
    if train.empty or train.signal_date.nunique() < 5:
        raise ValueError(f"Insufficient as-of training days: {arm} {day}")
    labels = four_class(train.second_high / train.signal_price - 1).astype(str)
    if arm == "high_board_models":
        scored_parts = []
        for board, today_board in current.groupby("board", sort=True):
            board_train = train[train.board.eq(board)]
            board_labels = four_class(board_train.second_high /
                                      board_train.signal_price - 1).astype(str)
            if len(board_train) < 100 or len(set(board_labels)) < 2:
                raise ValueError(f"Insufficient board training rows: {board} {day}")
            model = models()["small_boosted_tree"]
            model.fit(board_train[list(features)], board_labels)
            scored_parts.append(score(model, today_board, features))
        scored = pd.concat(scored_parts, ignore_index=True)
    else:
        model = models()["small_boosted_tree"]
        model.fit(train[list(features)], labels)
        scored = score(model, current, features)
    picks = verify_next_day(select_top(scored), rows)
    return picks, {"day": day, "train_rows": len(train),
                   "training_days": int(train.signal_date.nunique()),
                   "candidates": len(current), "picks": len(picks), "skipped": False}


def run_arm(arm: str, output: Path, env_file: Path) -> None:
    if arm not in ARMS:
        raise ValueError(arm)
    source = pd.read_csv(SOURCE, dtype={"symbol6": str, "signal_date": str,
                                     "next_date": str}, low_memory=False)
    dates = sorted(source.signal_date.unique())
    if len(source) != 59959 or len(dates) != 139:
        raise ValueError("Original candidate pool changed")
    with db_connection(env_file) as db:
        intervals = load_st_intervals(db)
    cohort = make_cohorts(source, intervals)[0][POLICIES[1]]
    market = market_by_day(pd.read_csv(AMOUNT, dtype={"trade_date": str}))
    cohort = add_asof_features(cohort, market)
    checkpoint = output / "checkpoints" / arm
    checkpoint.mkdir(parents=True, exist_ok=True)
    # Jan 5–8 lack four prior minute archives. Start after 20 complete market
    # feature dates, so every feature arm trains on a full 20-date window.
    for index in range(24, len(dates)-1):
        day = dates[index]
        path = checkpoint / f"{day}.json"
        if path.exists():
            continue
        picks, fold = one_fold(cohort, market, dates, index, arm)
        compact = picks[["signal_date", "next_date", "symbol6", "name",
                         "signal_price", "board", "selection_rank",
                         "predicted_class", "actual_class", "second_high",
                         "close_40", "actual_0940_return"]].to_dict("records") if len(picks) else []
        path.write_text(json.dumps({"fold": fold, "picks": compact}, ensure_ascii=False) + "\n")
        print(f"{arm} {day}: {fold['train_rows']} train, {fold['picks']} picks", flush=True)


def summarize(picks: pd.DataFrame, days: list[str]) -> pd.DataFrame:
    rows = []
    for (arm, rank), group in picks.groupby(["arm", "selection_rank"]):
        for label, frame in (("全期", group), *[(month, part) for month, part in
                           group.groupby(group.signal_date.str[:7])]):
            known = frame[frame.sale_status.ne("缺失")]
            rows.append({"方案": arm, "期间": label, "排名": f"Top{rank}",
                         "交易笔数": len(frame), "有收益笔数": len(known),
                         "测试日数": len(days) if label == "全期" else sum(
                             day.startswith(label) for day in days),
                         "达标数": int(frame.actual_class.eq("ge3").sum()),
                         "可接受数": int(frame.actual_class.eq("1to3").sum()),
                         "中性数": int(frame.actual_class.eq("0to1").sum()),
                         "错误数": int(frame.actual_class.eq("lt0").sum()),
                         "09:40平均收益（%）": known.actual_0940_return.mean()*100,
                         "09:40收益简单求和（%）": known.actual_0940_return.sum()*100,
                         "09:40复利代理（%）": ((1+known.actual_0940_return).prod()-1)*100})
    return pd.DataFrame(rows)


def collect(output: Path, env_file: Path) -> None:
    experiments = output / "experiments"
    experiments.mkdir(parents=True, exist_ok=True)
    market = market_by_day(pd.read_csv(AMOUNT, dtype={"trade_date": str}))
    source = pd.read_csv(SOURCE, usecols=["signal_date", "symbol6", "next_close_40"],
                         dtype={"signal_date": str, "symbol6": str}, low_memory=False)
    days = sorted(pd.read_csv(PICKS, usecols=["signal_date"],
                              dtype=str).signal_date.unique())[4:]
    original = pd.read_csv(PICKS, dtype={"signal_date": str, "symbol6": str})
    original = original[original.policy.eq(POLICIES[1]) &
                        original.signal_date.isin(days)].copy()
    original["arm"] = "原模型"
    original["board"] = original.symbol6.map(board_of)
    frames = [original]
    folds = []
    for arm in ARMS:
        files = sorted((output / "checkpoints" / arm).glob("*.json"))
        if len(files) != len(days):
            raise ValueError(f"Incomplete {arm}: {len(files)}/{len(days)} folds")
        for path in files:
            payload = json.loads(path.read_text())
            folds.append({"arm": arm, **payload["fold"]})
            if payload["picks"]:
                frames.append(pd.DataFrame(payload["picks"]).assign(arm=arm))
    picks = pd.concat(frames, ignore_index=True)
    picks = picks.merge(source, on=["signal_date", "symbol6"], validate="many_to_one")
    if picks.next_close_40.isna().any():
        picks["_row_id"] = np.arange(len(picks))
        missing = picks[picks.next_close_40.isna()].copy()
        missing["actual_0940_return"] = pd.to_numeric(missing.actual_0940_return)
        missing = settle_suspensions(missing, env_file)
        if not missing.suspension_zero.all():
            raise ValueError(f"Selected stocks without full next-morning prices: {len(missing)}")
        picks.loc[missing._row_id, "actual_0940_return"] = 0.
        picks.loc[missing._row_id, "actual_class"] = "0to1"
        picks["sale_status"] = "正常"
        picks.loc[missing._row_id, "sale_status"] = "停牌不盈亏"
        picks = picks.drop(columns="_row_id")
    else:
        picks["sale_status"] = "正常"
    picks["actual_0940_return"] = pd.to_numeric(picks.actual_0940_return)
    prices = picks.next_close_40 / picks.signal_price - 1
    if not np.allclose(prices[picks.sale_status.eq("正常")],
                       picks.loc[picks.sale_status.eq("正常"), "actual_0940_return"],
                       atol=1e-10):
        raise ValueError("09:40 return does not match source minute close")
    picks = picks.merge(market[["trade_date", "amount_avg5_1440_100m", "high_amount",
                                "prior_avg5_1440_100m", "prior_high_amount"]],
                        left_on="signal_date", right_on="trade_date", validate="many_to_one")
    picks["gate_pass"] = picks.high_amount
    gated = picks[picks.gate_pass & ~picks.arm.str.startswith("high_")].copy()
    gated["arm"] = gated.arm + "+含T五日轮空"
    prior_gated = picks[picks.arm.eq("原模型") & picks.prior_high_amount].copy()
    prior_gated["arm"] = "原模型+前五日轮空"
    combined = pd.concat([picks, gated, prior_gated], ignore_index=True)
    combined.to_csv(experiments / "all_picks.csv", index=False)
    pd.DataFrame(folds).to_csv(experiments / "folds.csv", index=False)
    market.to_csv(experiments / "daily_market.csv", index=False)
    summarize(combined, days).to_csv(experiments / "summary.csv", index=False,
                                     float_format="%.6f")


def write_gate_replay(output: Path) -> None:
    prior = Path("docs/stock_automl_runs/20261011_mk_1m_three_st_policies/"
                 "tradable_st_new_exit_market/daily.csv")
    source = pd.read_csv(prior, dtype={"Top1股票": str, "Top2股票": str})
    previous_columns = [f"前{lag}交易日截至14:40成交额（亿元）"
                        for lag in range(1, 6)]
    if len(source) != 118 or source[previous_columns].isna().any().any():
        raise ValueError("Incomplete previous-five-day market amount replay")
    daily = source[["选股日", "前一交易日上涨家数", "前一交易日下跌家数",
                    *previous_columns, "Top1股票", "Top1结果", "Top1固定09:40收益（%）",
                    "Top2股票", "Top2结果", "Top2固定09:40收益（%）"]].copy()
    daily["前五日平均成交额（亿元）"] = daily[previous_columns].mean(axis=1)
    daily["是否交易"] = daily["前五日平均成交额（亿元）"].ge(20000)
    daily = daily.rename(columns={f"Top{rank}固定09:40收益（%）":
                                  f"Top{rank} 09:40收益（%）" for rank in (1, 2)})
    for rank in (1, 2):
        daily.loc[~daily["是否交易"], [f"Top{rank}股票", f"Top{rank}结果",
                                     f"Top{rank} 09:40收益（%）"]] = np.nan
    output.mkdir(parents=True, exist_ok=True)
    daily.to_csv(output / "gate_daily.csv", index=False, float_format="%.6f")
    months = []
    for month, group in daily.groupby(daily["选股日"].str[:7]):
        row = {"月份": month, "选股日数": len(group),
               "交易日数": int(group["是否交易"].sum()),
               "轮空日数": int((~group["是否交易"]).sum())}
        for rank in (1, 2):
            returns = group[f"Top{rank} 09:40收益（%）"].dropna()
            row[f"Top{rank}交易笔数"] = len(returns)
            row[f"Top{rank}平均收益（%）"] = returns.mean()
            row[f"Top{rank}收益简单求和（%）"] = returns.sum()
            for label in ("达标", "可接受", "中性", "错误"):
                row[f"Top{rank}{label}数"] = int(group[f"Top{rank}结果"].eq(label).sum())
        months.append(row)
    pd.DataFrame(months).to_csv(output / "gate_monthly.csv", index=False,
                                float_format="%.6f")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=ARMS)
    parser.add_argument("--collect", action="store_true")
    parser.add_argument("--gate-only", action="store_true")
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    if args.arm:
        run_arm(args.arm, args.output, args.env_file)
    if args.collect:
        collect(args.output, args.env_file)
    if args.gate_only or args.collect:
        write_gate_replay(args.output)


if __name__ == "__main__":
    main()
