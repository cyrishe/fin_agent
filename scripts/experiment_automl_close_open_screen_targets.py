"""Walk-forward feature screens and alternative morning training targets."""
from __future__ import annotations

from collections import Counter
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn

from scripts.analyze_automl_cost_robustness import net_return
from scripts.build_automl_close_open_sequences import DATA
from scripts.research_automl_close_open_objectives import DATA as OLD_DATA
from scripts.research_automl_close_open_objectives import (
    EXPANDED, FEATURES, ROOT as PREVIOUS, Transform, benchmark,
)
from scripts.research_automl_close_open_objectives import fit, predict


ROOT = Path("docs/stock_automl_runs/20261014_close_open_screen_sequence_targets")
F9 = ("signal_return", "volume_ratio", "turnover_so_far_pct", "float_mv_100m_cny",
      "open_gap", "from_open", "prior5_return", "relative_turnover", "distance_ma5")
F11 = tuple(FEATURES) + ("open_gap", "from_open", "prior5_return", "relative_turnover")
F14 = F11 + ("lag_return", "prior5_volatility", "distance_ma5")
SCREENS = {"f7": tuple(FEATURES), "f9": F9, "f11": F11,
           "f14": F14, "f22": EXPANDED}
TARGETS = ("close", "max_high", "second_high", "mean_openclose40")


def daily_spearman(frame: pd.DataFrame) -> np.ndarray:
    """Cross-sectional ranks; an unlabeled/constant date contributes zero."""
    valid = frame[frame.actual.notna()]
    if len(valid) < 5:
        return np.zeros(len(EXPANDED))
    x = valid[list(EXPANDED)].rank(axis=0).to_numpy(float)
    y = valid.actual.rank().to_numpy(float)
    x -= np.nanmean(x, axis=0)
    y -= y.mean()
    numerator = np.nansum(x*y[:, None], axis=0)
    denominator = np.sqrt(np.nansum(x*x, axis=0) * np.sum(y*y))
    return np.divide(numerator, denominator, out=np.zeros_like(numerator),
                     where=denominator > 1e-12)


def select12(correlation: np.ndarray) -> tuple[str, ...]:
    if correlation.shape != (20, len(EXPANDED)):
        raise ValueError("Train-only selector needs exactly 20 past dates")
    sign = np.maximum((correlation > 0).sum(axis=0), (correlation < 0).sum(axis=0)) / 20
    score = np.abs(correlation.mean(axis=0)) * sign
    order = np.lexsort((np.arange(len(EXPANDED)), -score))
    return tuple(EXPANDED[i] for i in order[:12])


def as_target(rows: pd.DataFrame, target: str) -> pd.DataFrame:
    frame = rows.copy()
    if target == "close":
        return frame
    prices = {"max_high": frame.max_high, "second_high": frame.second_high,
              "mean_openclose40": (frame.open_40 + frame.close_40_raw)/2}
    frame["actual"] = prices[target] / frame.signal_price - 1
    return frame


def selected_row(test: pd.DataFrame, scores: np.ndarray, period: str, day: str,
                 model: str, target: str) -> dict:
    k = np.lexsort((test.symbol6.to_numpy(), -scores))[0]
    rec = test.iloc[k]
    target_price = {"close": rec.close_40_raw, "max_high": rec.max_high,
                    "second_high": rec.second_high,
                    "mean_openclose40": (rec.open_40 + rec.close_40_raw)/2}[target]
    return {"period": period, "signal_date": day, "symbol6": rec.symbol6,
            "model": model, "target": target, "traded": True,
            "return": rec.actual, "target_return": target_price/rec.signal_price-1,
            "score": float(scores[k])}


def metrics(picks: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = picks.copy()
    rows["month"] = rows.signal_date.str[:7]
    rows["net"] = net_return(rows["return"], 50_000, .00025)
    rows.loc[~rows.traded, "net"] = 0.
    rows["target_net"] = net_return(rows.target_return, 50_000, .00025)
    rows.loc[~rows.traded, "target_net"] = 0.
    monthly = rows.groupby(["period", "model", "month"]).agg(
        days=("signal_date", "count"), trades=("traded", "sum"),
        observed=("net", "count"), net_daily=("net", "mean"),
        oracle_target_daily=("target_net", "mean"),
        win_rate=("net", lambda s: s.gt(0).mean()),
        loss_below_2pct=("net", lambda s: s.lt(-.02).mean())).reset_index()
    summary = []
    combined = rows.copy()
    combined["period"] = "combined"
    for (period, model), group in pd.concat([rows, combined], ignore_index=True).groupby(
            ["period", "model"]):
        known = group[group.net.notna()]
        trades = known[known.traded]
        months = known.groupby("month").net.mean()
        if not len(months):
            continue
        best = months.idxmax()
        remove = trades.groupby("month").net.idxmax() if len(trades) else []
        summary.append({"period": period, "model": model, "days": len(group),
                        "trades": int(group.traded.sum()), "unknown": int(group.net.isna().sum()),
                        "net_daily_pct": known.net.mean()*100,
                        "target_oracle_daily_pct": known.target_net.mean()*100,
                        "win_rate_pct": trades.net.gt(0).mean()*100,
                        "loss_below_2pct_pct": trades.net.lt(-.02).mean()*100,
                        "positive_months": int(months.gt(0).sum()), "months": len(months),
                        "worst_month_pct": months.min()*100,
                        "ex_best_month_pct": known.loc[known.month.ne(best), "net"].mean()*100,
                        "ex_month_top_trade_pct": known.drop(remove).net.mean()*100,
                        "clipped5_net_pct": known.net.clip(-.05, .05).mean()*100})
    return pd.DataFrame(summary), monthly


def run(screen_only: bool = False) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    source = (OLD_DATA / "candidate_paths.csv.gz" if screen_only else
              DATA / "candidate_sequence_targets.csv.gz")
    rows = pd.read_csv(source, dtype={"symbol6": str, "period": str})
    if screen_only:
        # Only the return ratio matters for close-label screening. This
        # provisional pass uses the unchanged frozen panel before minute build.
        rows["signal_price"] = 1.
        rows["close_40_raw"] = 1 + rows.actual
        rows["open_40"] = rows["max_high"] = rows["second_high"] = np.nan
    rows["mean_openclose40"] = (rows.open_40 + rows.close_40_raw)/2
    rows["target_close"] = rows.actual
    if rows.duplicated(["period", "signal_date", "symbol6"]).any():
        raise ValueError("Duplicate frozen candidate")
    old = benchmark()
    picks, folds, selections = [], [], []
    for period, group in rows.groupby("period", sort=False):
        dates = sorted(group.signal_date.unique())
        by_date = {day: part for day, part in group.groupby("signal_date", sort=False)}
        # Cached daily correlations are only indexed through strictly older dates.
        correlations = {day: daily_spearman(part) for day, part in by_date.items()}
        for i, day in enumerate(dates):
            if i < 24:
                continue
            test = by_date[day]
            if not test.actual.notna().any():
                continue
            past = dates[i-20:i]
            train = pd.concat([by_date[d] for d in past], ignore_index=True)
            if train.next_date.max() > day or train.signal_date.nunique() != 20:
                raise ValueError("Immature label or missing training date")
            screened = select12(np.stack([correlations[d] for d in past]))
            selections.append({"period": period, "signal_date": day,
                               "features": ",".join(screened)})
            specs = [(f"screen_{name}", cols, "close") for name, cols in SCREENS.items()]
            specs.append(("screen_f12", screened, "close"))
            if not screen_only:
                specs.extend((f"target_{feature_set}_{target}", SCREENS[feature_set], target)
                             for feature_set in ("f11", "f14") for target in TARGETS[1:])
            for name, cols, target in specs:
                labeled = as_target(train, target)
                labeled = labeled[labeled.actual.notna()]
                if labeled.signal_date.nunique() != 20:
                    raise ValueError(f"No mature {target} labels for every training date")
                scores = predict(fit(labeled, cols, False), test)
                picks.append(selected_row(test, scores, period, day, name, target))
            tree = old[old.year.eq(period) & old.signal_date.eq(day)]
            if len(tree) > 1:
                raise ValueError("Duplicate original tree selection")
            picks.append({"period": period, "signal_date": day, "model": "tree171_saved",
                          "target": "close", "symbol6": tree.iloc[0].symbol6 if len(tree) else "",
                          "traded": bool(len(tree)), "return": tree.iloc[0]["return"] if len(tree) else 0.,
                          "target_return": tree.iloc[0]["return"] if len(tree) else 0.})
            folds.append({"period": period, "signal_date": day, "train_start": past[0],
                          "train_end": past[-1], "last_matured_label_date": train.next_date.max(),
                          "train_candidates": len(train), "test_candidates": len(test)})
            if i % 30 == 0:
                print(f"Screen and target fits {period} {day}: {i+1}/{len(dates)}", flush=True)
    results = pd.DataFrame(picks)
    summary, monthly = metrics(results)
    results["month"] = results.signal_date.str[:7]
    results["net"] = net_return(results["return"], 50_000, .00025)
    results.loc[~results.traded, "net"] = 0.
    suffix = "_preliminary" if screen_only else ""
    results.to_csv(ROOT / f"screen_target_picks{suffix}.csv", index=False)
    summary.to_csv(ROOT / f"screen_target_summary{suffix}.csv", index=False)
    monthly.to_csv(ROOT / f"screen_target_monthly{suffix}.csv", index=False)
    pd.DataFrame(folds).to_csv(ROOT / f"screen_target_folds{suffix}.csv", index=False)
    selected = pd.DataFrame(selections)
    selected.to_csv(ROOT / f"f12_daily_selections{suffix}.csv", index=False)
    counter = Counter(feature for line in selected.features for feature in line.split(","))
    pd.DataFrame([{"feature": name, "folds_selected": count} for name, count in counter.items()]).sort_values(
        ["folds_selected", "feature"], ascending=[False, True]).to_csv(
            ROOT / f"f12_selection_frequency{suffix}.csv", index=False)
    (ROOT / f"screen_target_manifest{suffix}.json").write_text(json.dumps({
        "source_path": str(source), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "target_note": "max/second/mean are training targets only; net is always 09:40 close",
        "features": {name: list(cols) for name, cols in SCREENS.items()},
        "f12_method": "past 20 daily cross-sectional Spearman mean abs * same-sign fraction",
        "screen_only": screen_only,
        "sklearn_version": sklearn.__version__,
    }, indent=2) + "\n")
    print(summary[["period", "model", "days", "unknown", "net_daily_pct", "positive_months",
                   "months"]].to_string(index=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--screen-only", action="store_true")
    run(parser.parse_args().screen_only)
