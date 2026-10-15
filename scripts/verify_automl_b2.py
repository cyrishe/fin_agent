"""Replay the deployable B2 minute engine against every frozen R24 2026 day."""
from __future__ import annotations
import argparse
from datetime import datetime
import json
from pathlib import Path
import numpy as np
import pandas as pd
from scripts.automl_b2 import evaluate_morning, build_features, FEATURE_ORDER
from scripts.automl_b2_data import model_bundle, score
from scripts.experiment_limit_rebound_short_mixed_r12 import dump, sha
from scripts.build_automl_limit_rebound_features import prepare_daily
import joblib


def main():
    p = argparse.ArgumentParser(); p.add_argument("--assets", type=Path, default=Path("outputs/stock_automl/b2_assets_20261015"))
    p.add_argument("--rebuild-features", action="store_true")
    p.add_argument("--output", type=Path, help="Write verification receipts outside the delivered seed")
    p.add_argument("--rebuild-month", help="Independently reproduce one frozen monthly training/selection")
    a = p.parse_args()
    ref = a.assets / "reference"
    cohort = pd.read_pickle(ref / "four_all_top3.pkl")
    cohort = cohort[cohort.part.eq("test") & cohort.d1_date.ge("2026-01-01")].copy()
    minutes = pd.read_pickle(ref / "first10_evidence.pkl")
    bars = {(s, d): g for (s, d), g in minutes.groupby(["symbol", "d0_date"])}
    selections = []
    counts = 0
    for day, f in cohort.groupby("d1_date"):
        d0 = f.d0_date.iloc[0]; _, rules, _ = model_bundle(a.assets, d0[:7])
        ps = [{"symbol6": r.symbol, "selection_rank": int(r.d0_rank), "preclose": float(r.d1_preclose),
               "cap": float(r.d1_cap), "ordinary": bool(r.eligible)} for r in f.itertuples()]
        src = {r.symbol: bars.get((r.symbol, d0), pd.DataFrame()) for r in f.itertuples()}
        locked = None; entry = None
        for minute in range(34, 41):
            values = evaluate_morning(ps, src, day, datetime.fromisoformat(f"{day} 09:{minute:02}:00"), rules["selected"])
            chosen = next((v for v in values if v["chosen"]), None)
            if locked:
                assert chosen is not None and chosen["symbol6"] == locked
            elif chosen:
                locked = chosen["symbol6"]
            yes = next((v for v in values if v["actionable"]), None)
            if entry:
                assert yes is not None and yes["entry_at"] == entry["entry_at"] and yes["entry_price"] == entry["entry_price"]
            elif yes:
                entry = yes
            counts += 1
        if entry:
            selections.append({"d0_date": d0, "symbol": entry["symbol6"], "entry_time": entry["entry_bar_end"][11:16], "buy": entry["entry_price"]})
    actual = pd.DataFrame(selections).sort_values("d0_date").reset_index(drop=True)
    old = pd.read_pickle(ref / "ledger.pkl")
    expected = old[old.policy.eq("four_fit_delay")][actual.columns].sort_values("d0_date").reset_index(drop=True)
    pd.testing.assert_frame_equal(actual, expected, check_dtype=False, atol=1e-12, rtol=0)
    print(f"Minute replay: {cohort.d1_date.nunique()} days, {len(actual)} entries exactly reproduced", flush=True)
    panel = pd.read_pickle(ref / "panel.pkl")
    max_difference = 0.; rows = 0; day_count = 0
    for month in sorted(cohort.model_month.unique()):
        bundle, _, _ = model_bundle(a.assets, month)
        predictions = pd.read_pickle(a.assets / "months" / month / "forecast_scores.pkl")
        source = panel.merge(predictions[["symbol", "d0_date"]], on=["symbol", "d0_date"], validate="one_to_one")
        new = score(bundle, source)[predictions.columns].reset_index(drop=True)
        pd.testing.assert_frame_equal(new, predictions.reset_index(drop=True), check_exact=False, atol=1e-14, rtol=0)
        max_difference = max(max_difference, float((new.score - predictions.reset_index(drop=True).score).abs().max()))
        rows += len(new); day_count += new.d0_date.nunique()
    print(f"Daily scores: {rows} candidate scores reproduced", flush=True)
    rebuilt = None
    if a.rebuild_features:
        raw = joblib.load(a.assets / "daily.joblib")
        daily = prepare_daily(raw[raw.trade_date.le("2026-10-13")])
        daily["date_i"] = daily.trade_date.map({d: i for i, d in enumerate(sorted(daily.trade_date.unique()))})
        hist = pd.read_csv(a.assets / "status_seed.csv.gz", dtype=str)
        q = build_features(daily, hist, a.assets / "verification_work", "2026-01-01")
        old_features = panel[panel.d0_date.ge("2026-01-01")].set_index(["symbol", "d0_date"])[FEATURE_ORDER].sort_index()
        new_features = q.set_index(["symbol", "d0_date"])[FEATURE_ORDER].sort_index()
        pd.testing.assert_frame_equal(new_features, old_features, check_dtype=False, atol=1e-12, rtol=1e-12)
        rebuilt = len(q)
    month_check = None
    if a.rebuild_month:
        from scripts.automl_b2_data import prepare_month
        month = a.rebuild_month
        work = a.assets / "verification_work" / ("month_rebuild_" + month)
        (work / "months").mkdir(parents=True, exist_ok=True)
        for path in (a.assets / "months").iterdir():
            target = work / "months" / path.name
            if path.name != month and not target.exists():
                target.symlink_to(path.resolve(), target_is_directory=True)
        if not (work / "minute_history").exists():
            (work / "minute_history").symlink_to((a.assets / "minute_history").resolve(), target_is_directory=True)
        raw = joblib.load(a.assets / "daily.joblib")
        daily = prepare_daily(raw[raw.trade_date.lt(month + "-01")])
        daily["date_i"] = daily.trade_date.map({d: i for i, d in enumerate(sorted(daily.trade_date.unique()))})
        hist = pd.read_csv(a.assets / "status_seed.csv.gz", dtype=str)
        prepare_month(None, work, month, daily, hist)
        rebuilt_bundle, rebuilt_rule, rebuilt_meta = model_bundle(work, month)
        original_bundle, original_rule, _ = model_bundle(a.assets, month)
        assert rebuilt_bundle["training_keys"] == original_bundle["training_keys"]
        assert rebuilt_rule["selected"] == original_rule["selected"]
        expected_keys = original_rule["selection_keys"]
        assert rebuilt_rule["selection_keys"] == expected_keys
        q = panel[panel.d0_date.str.startswith(month)]
        actual_scores = score(rebuilt_bundle, q); original_scores = score(original_bundle, q)
        np.testing.assert_allclose(actual_scores.score, original_scores.score, rtol=0, atol=1e-14)
        month_check = {"month": month, "training_keys_identical": True, "shape_selection_keys_identical": True,
            "rule_identical": True, "max_score_difference": float((actual_scores.score - original_scores.score).abs().max()),
            "training_rows": rebuilt_meta["training_rows"], "manifest_sha256": sha(work / "months" / month / "manifest.json")}
    result = {"minute_days": cohort.d1_date.nunique(), "prefix_replays": counts, "entries": len(actual),
              "stock_time_price_exact": True, "daily_prediction_rows": rows, "daily_prediction_days": day_count,
              "max_probability_difference": max_difference, "rebuilt_daily_feature_rows": rebuilt,
              "reference_ledger_sha256": sha(ref / "ledger.pkl"), "monthly_training_check": month_check,
              "scope": "历史合格数据样本；线上仅用已到达K，并记录真实确认时间，不使用未来全天审计决定信号。"}
    output = a.output or a.assets / "verification_runs"
    output.mkdir(parents=True, exist_ok=True)
    target = output / ("verification_monthly.json" if a.rebuild_month else "verification.json")
    dump(target, result); print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
