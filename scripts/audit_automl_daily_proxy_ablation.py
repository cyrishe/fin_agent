"""Audit daily-close proxies and ablate the intraday-low factor on fixed dates."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from scripts.backfill_automl_august_minute_full import connection
from scripts.experiment_automl_tail_three_class import BINARY, COMPACT_FEATURES, read_samples


def fit(rows: pd.DataFrame, features: tuple[str, ...]):
    numeric = [f for f in features if f not in BINARY]
    boolean = [f for f in features if f in BINARY]
    model = make_pipeline(ColumnTransformer([
        ("numeric", StandardScaler(), numeric), ("binary", "passthrough", boolean)]),
        LogisticRegression(C=0.2, max_iter=2000))
    model.fit(rows[list(features)], rows["class"].ne(-1).astype(int))
    return model


def outcomes(rows: pd.DataFrame) -> dict:
    n = len(rows)
    strong = int(rows["class"].eq(1).sum())
    neutral = int(rows["class"].eq(0).sum())
    return {"n": n, "strong": strong, "neutral": neutral,
            "critical": n - strong - neutral,
            "strong_rate": strong / n if n else None,
            "pass_rate": (strong + neutral) / n if n else None}


def evaluate(expanded: Path, sep30: Path, env_file: Path, output: Path) -> dict:
    data = pd.concat([read_samples(expanded), read_samples(sep30)], ignore_index=True)
    if data.duplicated(["signal_date", "symbol6"]).any():
        raise ValueError("Duplicate stock-day")
    train = data[data.signal_date.between("2026-08-25", "2026-09-21")].copy()
    test = data[~data.signal_date.isin(train.signal_date.unique())].copy()
    if train.signal_date.nunique() != 20 or test.signal_date.nunique() != 17:
        raise ValueError("Expected fixed 20 training / 17 test dates")
    models = {"八因子原模型": tuple(COMPACT_FEATURES),
              "删除日内最低价因子": tuple(f for f in COMPACT_FEATURES
                                    if f != "all_intraday_lows_above_ma"),
              "只保留日内最低价因子": tuple(f for f in COMPACT_FEATURES
                                       if f != "price_above_all_ma")}
    model_report = {}
    for name, features in models.items():
        selected = test.copy()
        selected["score"] = fit(train, features).predict_proba(test[list(features)])[:, 1]
        selected = selected.sort_values(["signal_date", "score", "symbol6"],
                                        ascending=[True, False, True])
        selected["rank"] = selected.groupby("signal_date").cumcount() + 1
        model_report[name] = {"features": list(features), "top2": outcomes(selected[selected["rank"].le(2)]),
                              "top5": outcomes(selected[selected["rank"].le(5)]),
                              "aug_top2": outcomes(selected[selected["rank"].le(2) &
                                                                selected.signal_date.le("2026-08-21")]),
                              "sep_top2": outcomes(selected[selected["rank"].le(2) &
                                                                selected.signal_date.ge("2026-09-22")])}
    with connection(env_file) as db:
        with db.cursor() as cursor:
            cursor.execute("""SELECT trade_date AS signal_date,LEFT(stk_code,6) AS symbol6,
                             close,low,volume FROM kcrp_stock_price
                             WHERE trade_date BETWEEN '2026-08-07' AND '2026-09-30'""")
            daily = pd.DataFrame(cursor.fetchall())
    daily["signal_date"] = pd.to_datetime(daily.signal_date)
    daily["symbol6"] = daily.symbol6.astype(str).str.zfill(6)
    if daily.duplicated(["signal_date", "symbol6"]).any():
        raise ValueError("Duplicate daily stock-day")
    merged = data.merge(daily, on=["signal_date", "symbol6"], how="left", validate="one_to_one")
    for field in ("close", "low", "volume"):
        merged[field] = pd.to_numeric(merged[field], errors="coerce")
    merged = merged.dropna(subset=["close", "low", "volume"])
    merged["close_return"] = merged.close / merged.t_reference_preclose - 1
    merged["close_volume_ratio"] = merged.volume / merged.avg_volume5_shares
    merged["close_turnover_pct"] = merged.turnover_so_far_pct * merged.volume / merged.minute_volume_shares
    merged["daily_low_above_ma"] = merged.low > merged[["ma5", "ma10", "ma20"]].max(axis=1)
    merged["daily_close_above_ma"] = merged.close > merged[["ma5", "ma10", "ma20"]].max(axis=1)
    merged["daily_volume_ratio_proxy"] = merged.volume / merged.avg_volume5_shares
    merged["daily_turnover_proxy"] = merged.turnover_so_far_pct * merged.volume / merged.minute_volume_shares * 220 / 240
    proxy_ratio = merged.daily_volume_ratio_proxy / merged.volume_ratio
    proxy_turnover = merged.daily_turnover_proxy / merged.turnover_so_far_pct
    signal_in_range = merged.signal_return.between(.03, .06, inclusive="both")
    close_in_range = merged.close_return.between(.03, .06, inclusive="both")
    proxy = {"paired_rows": len(merged),
             "signal_3to6_but_close_outside": int((signal_in_range & ~close_in_range).sum()),
             "signal_3to6_but_close_outside_rate": float((signal_in_range & ~close_in_range).mean()),
             "close_minus_1440_return_median_pp": float((merged.close_return - merged.signal_return).median() * 100),
             "close_minus_1440_return_abs_p90_pp": float((merged.close_return - merged.signal_return).abs().quantile(.9) * 100),
             "daily_volume_to_1440_volume_median": float((merged.volume / merged.minute_volume_shares).median()),
             "daily_volume_to_1440_volume_p90": float((merged.volume / merged.minute_volume_shares).quantile(.9)),
             "daily_volume_ratio_to_1440_ratio_median": float(proxy_ratio.median()),
             "daily_volume_ratio_to_1440_ratio_abs_pct_error_p90": float((proxy_ratio - 1).abs().quantile(.9) * 100),
             "prorated_daily_turnover_to_1440_turnover_abs_pct_error_p90": float((proxy_turnover - 1).abs().quantile(.9) * 100),
             "daily_close_above_vs_1440_above_disagree": int((merged.daily_close_above_ma != merged.price_above_all_ma.astype(bool)).sum()),
             "daily_low_above_vs_1440_all_lows_disagree": int((merged.daily_low_above_ma != merged.all_intraday_lows_above_ma.astype(bool)).sum()),
             "daily_low_above_implies_1440_all_lows_violations": int((merged.daily_low_above_ma &
                                  ~merged.all_intraday_lows_above_ma.astype(bool)).sum())}
    # This isolates feature proxy error within the *same known* 14:40 candidate
    # pool. It cannot assess candidates missed or added by close-based screening.
    seven = models["删除日内最低价因子"]
    trained = fit(train, seven)
    paired_test = merged[~merged.signal_date.isin(train.signal_date.unique())].copy()
    actual_features = paired_test[list(seven)].copy()
    close_features = actual_features.copy()
    close_features["signal_return"] = paired_test.close_return.to_numpy()
    close_features["volume_ratio"] = paired_test.daily_volume_ratio_proxy.to_numpy()
    close_features["turnover_so_far_pct"] = paired_test.daily_turnover_proxy.to_numpy()
    close_features["price_above_all_ma"] = paired_test.daily_close_above_ma.astype(int).to_numpy()
    paired_test["actual_score"] = trained.predict_proba(actual_features)[:, 1]
    paired_test["proxy_score"] = trained.predict_proba(close_features)[:, 1]
    selected_sets = {}
    for score_col in ("actual_score", "proxy_score"):
        ordered = paired_test.sort_values(["signal_date", score_col, "symbol6"],
                                          ascending=[True, False, True]).copy()
        ordered["rank"] = ordered.groupby("signal_date").cumcount() + 1
        selected_sets[score_col] = ordered
    a = selected_sets["actual_score"]
    b = selected_sets["proxy_score"]
    proxy["seven_factor_same_candidate_pool"] = {
        "score_spearman": float(paired_test.actual_score.corr(paired_test.proxy_score, method="spearman")),
        "actual_top2": outcomes(a[a["rank"].le(2)]),
        "proxy_top2": outcomes(b[b["rank"].le(2)]),
        "actual_top5": outcomes(a[a["rank"].le(5)]),
        "proxy_top5": outcomes(b[b["rank"].le(5)]),
        "top2_selection_overlap": len(set(zip(a.loc[a["rank"].le(2), "signal_date"],
                                               a.loc[a["rank"].le(2), "symbol6"])) &
                                      set(zip(b.loc[b["rank"].le(2), "signal_date"],
                                               b.loc[b["rank"].le(2), "symbol6"]))),
        "top5_selection_overlap": len(set(zip(a.loc[a["rank"].le(5), "signal_date"],
                                               a.loc[a["rank"].le(5), "symbol6"])) &
                                      set(zip(b.loc[b["rank"].le(5), "signal_date"],
                                               b.loc[b["rank"].le(5), "symbol6"])))}
    audit = pd.concat([pd.read_csv(p.parent / "candidates_1440.csv", dtype={"symbol6": str})
                       for p in (expanded, sep30)], ignore_index=True)
    audit["signal_date"] = pd.to_datetime(audit.signal_date)
    audit = audit[["signal_date", "symbol6", "entry_1450", "next_high10"]]
    audit = merged[["signal_date", "symbol6", "close"]].merge(
        audit, on=["signal_date", "symbol6"], how="inner", validate="one_to_one")
    for field in ("entry_1450", "next_high10"):
        audit[field] = pd.to_numeric(audit[field], errors="coerce")
    audit = audit.dropna()
    proxy_labels = np.select((audit.next_high10 / audit.close - 1 > .01,
                              audit.next_high10 / audit.close - 1 < .005), (1, -1), 0)
    actual_labels = np.select((audit.next_high10 / audit.entry_1450 - 1 > .01,
                               audit.next_high10 / audit.entry_1450 - 1 < .005), (1, -1), 0)
    proxy["close_as_1450_entry_label_audit"] = {
        "n": len(audit),
        "close_vs_entry_abs_return_p90_pp": float((audit.close / audit.entry_1450 - 1).abs().quantile(.9) * 100),
        "changed_class_count": int((proxy_labels != actual_labels).sum()),
        "changed_class_rate": float((proxy_labels != actual_labels).mean())}
    overlap = pd.crosstab(data.price_above_all_ma, data.all_intraday_lows_above_ma)
    result = {"train_days": int(train.signal_date.nunique()), "train_rows": len(train),
              "test_days": int(test.signal_date.nunique()), "test_rows": len(test),
              "test_candidate_baseline": outcomes(test), "models": model_report,
              "feature_overlap_counts": {str(a): {str(b): int(overlap.loc[a, b]) for b in overlap.columns}
                                         for a in overlap.index},
              "proxy_on_existing_candidate_pool": proxy}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--expanded", type=Path, default=Path(
        "outputs/stock_automl/tail_expanded_august/trainable_1440.csv"))
    p.add_argument("--sep30", type=Path, default=Path(
        "outputs/stock_automl/tail_sep30_check/trainable_1440.csv"))
    p.add_argument("--env-file", type=Path, default=Path("/Volumes/ext/fin_agent/.env"))
    p.add_argument("--output", type=Path, default=Path(
        "docs/stock_automl_runs/20261008_daily_proxy_ablation/summary.json"))
    a = p.parse_args()
    r = evaluate(a.expanded, a.sep30, a.env_file, a.output)
    print(json.dumps({k: v for k, v in r.items() if k in
                      ("models", "feature_overlap_counts", "proxy_on_existing_candidate_pool")},
                     ensure_ascii=False, indent=2))
