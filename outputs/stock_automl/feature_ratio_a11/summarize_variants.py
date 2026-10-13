"""Compare A11 feature variants against frozen B1 by original Top1 gate."""
from pathlib import Path
import json
import os

import numpy as np
import pandas as pd

from scripts.append_automl_stock_amount_state import (
    archive_amounts, database_amounts, required_bars,
)
from scripts.mk_minute_archive import day_files, trading_day


ROOT = Path(__file__).resolve().parent
TREE_ROOT = Path("outputs/stock_automl/tree_family_comparison_20261013")
MODES = {"feb_jul": ("none", "drop_volume", "drop_volume_cap99",
                     "ratio", "ratio_cap99", "base_cap99"),
         "aug_oct": ("none", "drop_volume", "drop_volume_cap99", "base_cap99")}
P = ["p_lt0", "p_0to1", "p_1to3", "p_ge3"]


def path_for(cohort, mode, version):
    if mode == "none" and version == "1.9.1":
        filename = ("scores_sklearn_191.csv.gz" if cohort == "feb_jul"
                    else "scores_aug_oct_sklearn_191.csv.gz")
        return Path("outputs/stock_automl/sklearn_binning_deep_audit_20261013") / filename
    if mode == "none":
        return TREE_ROOT / f"{cohort}_hgb_b1_seed42/scores.csv.gz"
    return ROOT / f"{cohort}_{mode}_sk{version.replace('.', '')}/scores.csv.gz"


def pick_one(frame):
    eligible = frame.loc[frame.predicted_class.isin(("ge3", "1to3"))].copy()
    eligible["priority"] = eligible.predicted_class.map({"ge3": 0, "1to3": 1})
    eligible["score"] = np.where(eligible.priority.eq(0), eligible.p_ge3,
                                 eligible.p_ge3 + eligible.p_1to3)
    return eligible.sort_values(
        ["signal_date", "priority", "score", "symbol6"],
        ascending=[True, True, False, True]).drop_duplicates("signal_date")


def score_quality(frame):
    known = frame.second_high.notna()
    labeled = frame.loc[known]
    realized = labeled.second_high / labeled.signal_price - 1
    target = np.select([realized < 0, realized < .01, realized < .03],
                       [0, 1, 2], default=3)
    probabilities = labeled[P].to_numpy()
    return {"labeled_candidates": int(known.sum()),
            "logloss": float(-np.log(np.maximum(
                probabilities[np.arange(len(target)), target], 1e-15)).mean()),
            "brier4": float(np.square(probabilities - np.eye(4)[target]).sum(axis=1).mean())}


def calendar_for(cohort):
    if cohort == "feb_jul":
        return [trading_day(p) for p in day_files([
            Path("outputs/stock_automl/mk_1m_compact")])]
    return pd.read_csv("docs/stock_automl_runs/20261011_aug_oct_independent20/market_context.csv",
                       dtype={"trade_date": str}).trade_date.tolist()


def gate_amounts(cohort, picks, calendar):
    saved = ROOT / f"{cohort}_amounts.csv.gz"
    cache_path = saved if saved.exists() else TREE_ROOT / f"{cohort}_a9_amounts.csv.gz"
    cached = pd.read_csv(cache_path, dtype={"date": str, "symbol6": str})
    if os.environ.get("A11_REUSE_AMOUNT_CACHE") == "1" and saved.exists():
        if cached.duplicated(["date", "symbol6"]).any():
            raise ValueError("Duplicate stock-date amount")
        return {(r.date, r.symbol6): float(r.amount_100m_cny)
                for r in cached.itertuples(index=False)}
    needed = required_bars([{"选股日": r.signal_date, "Top1代码": r.symbol6}
                            for r in picks.itertuples(index=False)], calendar)
    cache_keys = set(zip(cached.date, cached.symbol6))
    missing = {day: {code for code in codes if (day, code) not in cache_keys}
               for day, codes in needed.items()}
    missing = {day: codes for day, codes in missing.items() if codes}
    if missing:
        if cohort == "feb_jul":
            added = archive_amounts(missing, root=Path(
                "outputs/stock_automl/mk_1m_compact"), before=None)
        else:
            added = database_amounts(missing, Path(".env"))
        cached = pd.concat([cached, pd.DataFrame(added)], ignore_index=True)
    if cached.duplicated(["date", "symbol6"]).any():
        raise ValueError("Duplicate stock-date amount")
    cached.to_csv(ROOT / f"{cohort}_amounts.csv.gz", index=False,
                  compression={"method": "gzip", "mtime": 0})
    return {(r.date, r.symbol6): float(r.amount_100m_cny)
            for r in cached.itertuples(index=False)}


report = {}
all_daily = []
for cohort in ("feb_jul", "aug_oct"):
    calendar = calendar_for(cohort)
    modes = MODES[cohort]
    arms = {}
    for version in ("1.7.1", "1.9.1"):
        for mode in modes:
            path = path_for(cohort, mode, version)
            if path.exists():
                arms[f"sklearn_{version}_{mode}"] = pd.read_csv(
                    path, dtype={"signal_date": str, "symbol6": str})
    reference = arms["sklearn_1.7.1_none"].sort_values(
        ["signal_date", "symbol6"]).reset_index(drop=True)
    for name, frame in arms.items():
        aligned = frame.sort_values(["signal_date", "symbol6"]).reset_index(drop=True)
        if not aligned[["signal_date", "symbol6"]].equals(
                reference[["signal_date", "symbol6"]]):
            raise ValueError(f"Candidate mismatch: {cohort}/{name}")
        for column in ("signal_price", "second_high", "close_40"):
            if not np.allclose(aligned[column], reference[column],
                               rtol=0, atol=0, equal_nan=True):
                raise ValueError(f"Outcome mismatch: {cohort}/{name}/{column}")
    prediction_days = sorted(reference.signal_date.unique())
    picks = {name: pick_one(frame) for name, frame in arms.items()}
    amount_picks = pd.concat([value[["signal_date", "symbol6"]]
                              for value in picks.values()], ignore_index=True)
    amounts = gate_amounts(cohort, amount_picks, calendar)
    positions = {day: i for i, day in enumerate(calendar)}
    results = {}
    pick_symbols = {}
    for name, frame in arms.items():
        selected = picks[name]
        quality = score_quality(frame)
        raw = (selected.close_40 / selected.signal_price - 1)
        raw_daily = raw.fillna(0)
        pick_symbols[name] = selected.set_index("signal_date").symbol6.reindex(
            prediction_days).fillna("")
        gated_rows = []
        indexed = selected.set_index("signal_date")
        for day in prediction_days:
            if day not in indexed.index:
                gated_rows.append({"cohort": cohort, "arm": name,
                                   "signal_date": day, "symbol6": "",
                                   "gate": "no_pick", "gross_return": 0.0})
                continue
            row = indexed.loc[day]
            code = row.symbol6
            prior = [amounts.get((date, code)) for date in
                     calendar[positions[day]-20:positions[day]]]
            current = amounts.get((day, code))
            if any(value is None for value in prior) or current is None:
                gate = "missing_amount"
            elif current < float(np.median(prior)):
                gate = "below_median"
            elif pd.isna(row.close_40):
                gate = "missing_outcome"
            else:
                gate = "trade"
            gross = float(row.close_40 / row.signal_price - 1) if gate == "trade" else 0.0
            gated_rows.append({"cohort": cohort, "arm": name,
                               "signal_date": day, "symbol6": code,
                               "gate": gate, "gross_return": gross})
        daily = pd.DataFrame(gated_rows)
        all_daily.append(daily)
        returns = daily.gross_return
        curve = (1 + returns).cumprod()
        results[name] = {**quality, "selected_days": len(selected),
                         "unknown_outcome_picks": int(raw.isna().sum()),
                         "raw_compound_proxy": float((1 + raw_daily).prod() - 1),
                         "gate_counts": daily.gate.value_counts().to_dict(),
                         "gated_compound_proxy": float(curve.iloc[-1] - 1),
                         "gated_max_drawdown": float((curve / curve.cummax().clip(lower=1) - 1).min()),
                         "gated_compound_hypothetical_0p2pct_cost": float(
                             (1 + returns - .002 * daily.gate.eq("trade")).prod() - 1)}
    report[cohort] = {"prediction_days": len(prediction_days),
                      "arms": results,
                      "same_top1_between_versions": {
                          mode: int(pick_symbols[f"sklearn_1.7.1_{mode}"].eq(
                              pick_symbols[f"sklearn_1.9.1_{mode}"]).sum())
                          for mode in modes},
                      "same_top1_vs_171_none": {
                          name: int(pick_symbols[name].eq(
                              pick_symbols["sklearn_1.7.1_none"]).sum())
                          for name in arms}}

pd.concat(all_daily, ignore_index=True).to_csv(ROOT / "a11_daily_gate.csv", index=False)
(ROOT / "a11_summary.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps(report, ensure_ascii=False, indent=2))
