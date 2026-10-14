"""A12 exact Top1 and 09:40 metrics with and without the original amount gate."""
from pathlib import Path
import json

import numpy as np
import pandas as pd

from scripts.append_automl_stock_amount_state import archive_amounts, required_bars
from scripts.mk_minute_archive import day_files, trading_day


OUT = Path(__file__).resolve().parent
A11 = Path("outputs/stock_automl/feature_ratio_a11")
VERSIONS = ("1.7.1", "1.9.1")


def scores_path(version: str, weighted: bool) -> Path:
    suffix = version.replace(".", "")
    if weighted:
        return OUT / f"ratio_log_weight_sk{suffix}/scores.csv.gz"
    return A11 / f"feb_jul_ratio_sk{suffix}/scores.csv.gz"


def pick_one(frame: pd.DataFrame) -> pd.DataFrame:
    eligible = frame.loc[frame.predicted_class.isin(("ge3", "1to3"))].copy()
    eligible["priority"] = eligible.predicted_class.map({"ge3": 0, "1to3": 1})
    eligible["score"] = np.where(eligible.priority.eq(0), eligible.p_ge3,
                                  eligible.p_ge3 + eligible.p_1to3)
    return eligible.sort_values(
        ["signal_date", "priority", "score", "symbol6"],
        ascending=[True, True, False, True]).drop_duplicates("signal_date")


def amounts_for(picks: pd.DataFrame, calendar: list[str]) -> dict[tuple[str, str], float]:
    cache_path = OUT / "feb_jul_amounts.csv.gz"
    old_path = A11 / "feb_jul_amounts.csv.gz"
    cached = pd.read_csv(cache_path if cache_path.exists() else old_path,
                         dtype={"date": str, "symbol6": str})
    if cached.duplicated(["date", "symbol6"]).any():
        raise ValueError("Duplicate cached stock-date amount")
    if cache_path.exists():
        # This fixed experiment's first pass already scanned every required archive day.
        return {(r.date, r.symbol6): float(r.amount_100m_cny)
                for r in cached.itertuples(index=False)}
    needed = required_bars([{"选股日": r.signal_date, "Top1代码": r.symbol6}
                            for r in picks.itertuples(index=False)], calendar)
    keys = set(zip(cached.date, cached.symbol6))
    missing = {day: codes - {code for code in codes if (day, code) in keys}
               for day, codes in needed.items()}
    missing = {day: codes for day, codes in missing.items() if codes}
    if missing:
        added = archive_amounts(missing, root=Path(
            "outputs/stock_automl/mk_1m_compact"), before=None)
        cached = pd.concat([cached, pd.DataFrame(added)], ignore_index=True)
    if cached.duplicated(["date", "symbol6"]).any():
        raise ValueError("Duplicate stock-date amount after archive lookup")
    cached.to_csv(cache_path, index=False,
                  compression={"method": "gzip", "mtime": 0})
    return {(r.date, r.symbol6): float(r.amount_100m_cny)
            for r in cached.itertuples(index=False)}


def one_row(day: str, arm: str, row: pd.Series | None,
            calendar: list[str], positions: dict[str, int],
            amounts: dict[tuple[str, str], float]) -> dict:
    result = {"signal_date": day, "month": day[:7], "arm": arm,
              "symbol6": "", "top1_result": "", "gate": "no_pick",
              "sale_return_pct_gate": np.nan, "sale_return_pct_no_gate": np.nan}
    if row is None:
        return result
    code = row.symbol6
    result["symbol6"] = code
    if pd.notna(row.second_high):
        actual = row.second_high / row.signal_price - 1
        result["top1_result"] = ("达标" if actual >= .03 else
                                 "可接受" if actual >= .01 else "错误")
    if pd.notna(row.close_40):
        result["sale_return_pct_no_gate"] = (row.close_40 / row.signal_price - 1) * 100
    prior = [amounts.get((d, code)) for d in
             calendar[positions[day] - 20:positions[day]]]
    current = amounts.get((day, code))
    if len(prior) != 20 or any(value is None for value in prior) or current is None:
        result["gate"] = "missing_amount"
    elif current < float(np.median(prior)):
        result["gate"] = "below_median"
    elif pd.isna(row.close_40):
        result["gate"] = "missing_outcome"
    else:
        result["gate"] = "trade"
        result["sale_return_pct_gate"] = result["sale_return_pct_no_gate"]
    return result


def aggregate(group: pd.DataFrame) -> dict:
    known = group.top1_result.ne("")
    n = int(known.sum())
    gated = group.sale_return_pct_gate.dropna()
    ungated = group.sale_return_pct_no_gate.dropna()
    return {
        "prediction_days": len(group), "top1_picks": int(group.symbol6.ne("").sum()),
        "top1_result_known": n,
        "top1_result_unknown": int(group.symbol6.ne("").sum()) - n,
        "top1_pass_pct": round(float(group.top1_result.eq("达标").sum() / n * 100), 3),
        "top1_acceptable_pct": round(float(group.top1_result.eq("可接受").sum() / n * 100), 3),
        "top1_error_pct": round(float(group.top1_result.eq("错误").sum() / n * 100), 3),
        "gate_trades": len(gated),
        "gate_mean_per_trade_pct": round(float(gated.mean()), 5) if len(gated) else None,
        "no_gate_trades": len(ungated),
        "no_gate_mean_per_trade_pct": round(float(ungated.mean()), 5) if len(ungated) else None,
        "rule_skip_days": int(group.gate.eq("below_median").sum()),
        "missing_amount_days": int(group.gate.eq("missing_amount").sum()),
        "no_0940_exit_days": int(group.gate.eq("missing_outcome").sum()),
    }


def main() -> None:
    frames = {}
    for version in VERSIONS:
        for weighted in (False, True):
            arm = f"sklearn_{version}_{'ratio_log_weight' if weighted else 'ratio'}"
            frames[arm] = pd.read_csv(scores_path(version, weighted),
                                      dtype={"signal_date": str, "symbol6": str})
    reference = frames["sklearn_1.7.1_ratio"].sort_values(
        ["signal_date", "symbol6"]).reset_index(drop=True)
    for arm, frame in frames.items():
        aligned = frame.sort_values(["signal_date", "symbol6"]).reset_index(drop=True)
        if not aligned[["signal_date", "symbol6"]].equals(
                reference[["signal_date", "symbol6"]]):
            raise ValueError(f"Candidate mismatch: {arm}")
        for column in ("signal_price", "second_high", "close_40"):
            if not np.allclose(aligned[column], reference[column],
                               rtol=0, atol=0, equal_nan=True):
                raise ValueError(f"Outcome mismatch: {arm}/{column}")
    dates = sorted(reference.signal_date.unique())
    picks = {arm: pick_one(frame) for arm, frame in frames.items()}
    calendar = [trading_day(p) for p in day_files([Path(
        "outputs/stock_automl/mk_1m_compact")])]
    positions = {day: index for index, day in enumerate(calendar)}
    all_picks = pd.concat([frame[["signal_date", "symbol6"]]
                           for frame in picks.values()], ignore_index=True)
    amounts = amounts_for(all_picks, calendar)
    daily = []
    for arm, selected in picks.items():
        indexed = selected.set_index("signal_date")
        for day in dates:
            row = indexed.loc[day] if day in indexed.index else None
            daily.append(one_row(day, arm, row, calendar, positions, amounts))
    daily = pd.DataFrame(daily).sort_values(["arm", "signal_date"])
    daily.to_csv(OUT / "a12_daily.csv", index=False, float_format="%.8f")
    changed = []
    for version in VERSIONS:
        old = daily.loc[daily.arm.eq(f"sklearn_{version}_ratio")].set_index("signal_date")
        new = daily.loc[daily.arm.eq(f"sklearn_{version}_ratio_log_weight")].set_index("signal_date")
        for day in dates:
            previous, weighted = old.loc[day], new.loc[day]
            changed.append({
                "version": version, "signal_date": day,
                "old_symbol": previous.symbol6, "weighted_symbol": weighted.symbol6,
                "same_top1": previous.symbol6 == weighted.symbol6,
                "old_top1_result": previous.top1_result,
                "weighted_top1_result": weighted.top1_result,
                "old_gate": previous.gate, "weighted_gate": weighted.gate,
                "old_sale_pct_gate": previous.sale_return_pct_gate,
                "weighted_sale_pct_gate": weighted.sale_return_pct_gate,
                "old_sale_pct_no_gate": previous.sale_return_pct_no_gate,
                "weighted_sale_pct_no_gate": weighted.sale_return_pct_no_gate,
            })
    pd.DataFrame(changed).to_csv(OUT / "a12_changed_days.csv", index=False,
                                 float_format="%.8f")
    overall, monthly = [], []
    for arm, group in daily.groupby("arm"):
        overall.append({"arm": arm, **aggregate(group)})
        for month, block in group.groupby("month"):
            monthly.append({"arm": arm, "month": month, **aggregate(block)})
    overall = pd.DataFrame(overall)
    monthly = pd.DataFrame(monthly)
    overall.to_csv(OUT / "a12_overall.csv", index=False)
    monthly.to_csv(OUT / "a12_monthly.csv", index=False)
    symbols = {arm: frame.set_index("signal_date").symbol6.reindex(dates).fillna("")
               for arm, frame in picks.items()}
    agreement = {
        "within_version_ratio_vs_weight": {
            version: int(symbols[f"sklearn_{version}_ratio"].eq(
                symbols[f"sklearn_{version}_ratio_log_weight"]).sum())
            for version in VERSIONS},
        "across_version_unweighted": int(symbols["sklearn_1.7.1_ratio"].eq(
            symbols["sklearn_1.9.1_ratio"]).sum()),
        "across_version_weighted": int(symbols["sklearn_1.7.1_ratio_log_weight"].eq(
            symbols["sklearn_1.9.1_ratio_log_weight"]).sum()),
    }
    (OUT / "a12_agreement.json").write_text(json.dumps(agreement, indent=2))
    print(overall.to_string(index=False))
    print(monthly[["arm", "month", "gate_trades", "gate_mean_per_trade_pct",
                   "no_gate_trades", "no_gate_mean_per_trade_pct"]].to_string(index=False))
    print(json.dumps(agreement, indent=2))


if __name__ == "__main__":
    main()
