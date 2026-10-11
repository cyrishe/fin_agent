"""Compare >=3% and <-1% second-high cohorts using pre-signal facts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from scripts.benchmark_automl_1440_inference import FEATURES, db_connection, query
from scripts.experiment_automl_close9_models import DATA
from scripts.experiment_automl_second_high_regression import SECOND_HIGHS


OUT = Path("docs/stock_automl_runs/20261010_second_high_factor_audit")
FLOW = ("main_net_buy_value_ratio", "large_net_buy_value_ratio",
        "huge_net_buy_value_ratio", "medium_net_to_amount")
MARGIN = ("financing_balance_to_float_mv_pct", "financing_net_buy_to_amount")
PRICE = ("tminus1_return", "tminus1_body", "tminus1_amplitude", "tminus1_turnover")
ADDITIONAL = (*FLOW, *MARGIN, *PRICE)


def load_history(base: pd.DataFrame, env_file: Path) -> pd.DataFrame:
    conn = db_connection(env_file)
    pieces = []
    try:
        for day, group in base.groupby("signal_date", sort=True):
            previous = query(conn, "SELECT MAX(trade_date) AS previous_date FROM kcrp_stock_price "
                             "WHERE trade_date < %s", (day,)).iloc[0].previous_date
            if pd.isna(previous):
                raise ValueError(f"No previous trading date for {day}")
            previous = str(previous)[:10]
            cutoff = pd.Timestamp(day + " 14:40:00")
            codes = set(group.symbol6)
            daily = query(conn, "SELECT LEFT(stk_code,6) AS symbol6, close, preclose, open, "
                          "amplitude, turn_ratio, amount, update_time "
                          "FROM kcrp_stock_price WHERE trade_date=%s", (previous,))
            flow = query(conn, "SELECT LEFT(stk_code,6) AS symbol6, "
                         "main_net_buy_value_ratio, large_net_buy_value_ratio, "
                         "huge_net_buy_value_ratio, medium_buy_value, medium_sell_value, "
                         "update_time FROM kcrp_stock_moneyflow WHERE trade_date=%s", (previous,))
            margin = query(conn, "SELECT LEFT(stk_code,6) AS symbol6, trading_balance, "
                           "purch_with_borrow_money, repayment_to_broker, "
                           "fin_balance_to_liqmu, update_time FROM kcrp_stock_margintrade "
                           "WHERE trade_date=%s", (previous,))
            industry = query(conn, "SELECT LEFT(stk_code,6) AS symbol6, industry_name, "
                             "begin_date, end_date, update_time FROM kcrp_stock_industry "
                             "WHERE industry_type='SW2021' AND level=1 AND begin_date<=%s "
                             "AND (end_date IS NULL OR end_date>%s)", (day, day))
            tables = []
            for name, frame in (("daily", daily), ("flow", flow), ("margin", margin),
                                ("industry", industry)):
                frame = frame[frame.symbol6.isin(codes)].copy()
                frame = frame[pd.to_datetime(frame.update_time, errors="coerce").le(cutoff)]
                if name == "industry":
                    frame = frame.sort_values("begin_date").drop_duplicates("symbol6", keep="last")
                elif frame.duplicated("symbol6").any():
                    raise ValueError(f"Duplicate {name} rows on {previous}")
                tables.append(frame.drop(columns="update_time"))
            daily, flow, margin, industry = tables
            daily["amount"] = pd.to_numeric(daily.amount, errors="coerce")
            for column in ("main_net_buy_value_ratio", "large_net_buy_value_ratio",
                           "huge_net_buy_value_ratio"):
                flow[column] = pd.to_numeric(flow[column], errors="coerce")
            daily["tminus1_return"] = pd.to_numeric(daily.close)/pd.to_numeric(daily.preclose)-1
            daily["tminus1_body"] = pd.to_numeric(daily.close)/pd.to_numeric(daily.open)-1
            daily["tminus1_amplitude"] = pd.to_numeric(daily.amplitude, errors="coerce")
            daily["tminus1_turnover"] = pd.to_numeric(daily.turn_ratio, errors="coerce")
            flow["medium_net_to_amount"] = (
                pd.to_numeric(flow.medium_buy_value, errors="coerce")-
                pd.to_numeric(flow.medium_sell_value, errors="coerce"))
            margin["financing_balance_to_float_mv_pct"] = pd.to_numeric(
                margin.fin_balance_to_liqmu, errors="coerce")
            margin["financing_net_buy"] = (pd.to_numeric(margin.purch_with_borrow_money, errors="coerce")-
                                          pd.to_numeric(margin.repayment_to_broker, errors="coerce"))
            combined = group.merge(daily[["symbol6", "amount", *PRICE]], on="symbol6", how="left",
                                   validate="one_to_one")
            combined = combined.merge(flow[["symbol6", *FLOW]], on="symbol6", how="left",
                                      validate="one_to_one")
            combined = combined.merge(margin[["symbol6", "financing_balance_to_float_mv_pct",
                                              "financing_net_buy"]], on="symbol6", how="left",
                                      validate="one_to_one")
            denominator = combined.amount.where(combined.amount.gt(0))
            combined["medium_net_to_amount"] = combined.medium_net_to_amount / denominator
            combined["financing_net_buy_to_amount"] = combined.financing_net_buy / denominator
            combined = combined.merge(industry[["symbol6", "industry_name"]], on="symbol6",
                                      how="left", validate="one_to_one")
            combined["tminus1_date"] = previous
            pieces.append(combined)
            print(f"{day}: {len(combined)} candidates, T-1 {previous}", flush=True)
    finally:
        conn.rollback()
        conn.close()
    return pd.concat(pieces, ignore_index=True)


def summarize(data: pd.DataFrame) -> dict:
    extremes = data[data.cohort.ne("middle")].copy()
    rows = []
    for feature in (*FEATURES, *ADDITIONAL):
        subset = extremes[["signal_date", "cohort", feature]].dropna()
        high = subset[subset.cohort.eq("high3")][feature]
        low = subset[subset.cohort.eq("low_minus1")][feature]
        if min(len(high), len(low)) < 20:
            continue
        daily = subset.groupby(["signal_date", "cohort"])[feature].median().unstack()
        daily = daily.dropna(subset=["high3", "low_minus1"])
        auc = roc_auc_score(subset.cohort.eq("high3"), subset[feature])
        daily_auc = []
        for _, daily_subset in subset.groupby("signal_date"):
            if daily_subset.cohort.nunique() == 2 and daily_subset[feature].nunique() > 1:
                daily_auc.append(roc_auc_score(daily_subset.cohort.eq("high3"),
                                               daily_subset[feature]))
        rows.append({"feature": feature, "high3_available": len(high),
                     "low_minus1_available": len(low),
                     "high3_coverage": len(high)/data.cohort.eq("high3").sum(),
                     "low_minus1_coverage": len(low)/data.cohort.eq("low_minus1").sum(),
                     "high3_median": float(high.median()), "low_minus1_median": float(low.median()),
                     "pooled_extreme_auc": float(auc),
                     "mean_same_day_extreme_auc": float(np.mean(daily_auc)) if daily_auc else None,
                     "paired_days": len(daily),
                     "days_high3_median_above_low": int(daily.high3.gt(daily.low_minus1).sum()),
                     "median_same_day_difference": float((daily.high3-daily.low_minus1).median())})
    factor_table = pd.DataFrame(rows).sort_values("pooled_extreme_auc", ascending=False)
    OUT.mkdir(parents=True, exist_ok=True)
    factor_table.to_csv(OUT / "numeric_factor_comparison.csv", index=False)
    sectors = []
    valid = data[data.industry_name.notna()]
    daily_pool = valid.groupby("signal_date").cohort.apply(lambda s: s.eq("high3").mean())
    for sector, group in valid.groupby("industry_name"):
        if len(group) < 100:
            continue
        day_sector = group.groupby("signal_date").cohort.agg(
            n="size", high3_rate=lambda s: s.eq("high3").mean())
        excess = day_sector.high3_rate-daily_pool.loc[day_sector.index].to_numpy()
        sectors.append({"industry": sector, "candidates": len(group),
                        "high3": int(group.cohort.eq("high3").sum()),
                        "low_minus1": int(group.cohort.eq("low_minus1").sum()),
                        "high3_rate": float(group.cohort.eq("high3").mean()),
                        "low_minus1_rate": float(group.cohort.eq("low_minus1").mean()),
                        "days_present": len(day_sector),
                        "same_day_high3_excess_pp_weighted": float(np.average(excess, weights=day_sector.n)*100),
                        "days_above_same_day_pool": int(excess.gt(0).sum())})
    pd.DataFrame(sectors).sort_values("high3_rate", ascending=False).to_csv(
        OUT / "industry_comparison.csv", index=False)
    # A small set of real examples supports manual inspection; avoid dumping the full DB join.
    examples = pd.concat([
        data[data.cohort.eq(label)].sort_values(["second_high_return", "signal_date"],
                                              ascending=[label != "high3", True]).head(30)
        for label in ("high3", "low_minus1")])
    examples[["signal_date", "symbol6", "name", "cohort", "second_high_return",
              "industry_name", *FEATURES, *ADDITIONAL]].to_csv(OUT / "extreme_examples.csv", index=False)
    data[["signal_date", "next_date", "symbol6", "industry_name", *ADDITIONAL]].to_csv(
        OUT / "candidate_extra_features.csv.gz", index=False, compression="gzip")
    summary = {"candidates": len(data), "days": data.signal_date.nunique(),
               "cohorts": {str(k): int(v) for k, v in data.cohort.value_counts().items()},
               "asof": "Only T-1 rows with current update_time <= T 14:40; industry interval valid on T and update_time <= T 14:40. Provider correction history is not available.",
               "meaning": "Descriptive feature screen on repeatedly viewed historical dates, not an independent model gain.",
               "coverage": {feature: float(data[feature].notna().mean()) for feature in ADDITIONAL},
               "numeric_comparison": rows,
               "source_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in (DATA, SECOND_HIGHS)}}
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n")
    return summary


def run(env_file: Path = Path("/Volumes/ext/fin_agent/.env")) -> dict:
    base = pd.read_csv(DATA, dtype={"symbol6": str})
    prices = pd.read_csv(SECOND_HIGHS, dtype={"symbol6": str})
    data = base.merge(prices[["next_date", "symbol6", "second_high"]],
                      on=["next_date", "symbol6"], validate="one_to_one")
    data["second_high_return"] = data.second_high / data.entry_1440 - 1
    data["cohort"] = np.select([data.second_high_return.ge(.03),
                                data.second_high_return.lt(-.01)],
                               ["high3", "low_minus1"], default="middle")
    if len(data) != 14292 or data.signal_date.nunique() != 40:
        raise ValueError("Expected exact 40-day candidate pool")
    return summarize(load_history(data, env_file))


if __name__ == "__main__":
    print(json.dumps({k: v for k, v in run().items() if k != "numeric_comparison"},
                     ensure_ascii=False, indent=2))
