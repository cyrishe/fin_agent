"""Read-only timing of the single-day increment needed for 2026-10-13 preparation."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import time

import joblib
import numpy as np
import pandas as pd
import sklearn

from scripts.automl_st_status import load_st_intervals
from scripts.benchmark_automl_1440_inference import FEATURES, db_connection
from scripts.run_automl_1440_strategy import (
    amount_cache, database_calendar, database_signal, daily_sources,
    exclude_st_at_limit, morning_labels, seven_factors,
)
from scripts.run_automl_four_class_pipeline import (
    generate_training_features, generate_training_labels, infer, select_top,
    train_model,
)


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/stock_automl_runs/20261013_171_1440_postfill_x6/evidence"
OUTPUT = ROOT / "outputs/stock_automl/live_1440_171_postfill/incremental_benchmark.json"
DAY = "2026-10-13"
PREVIOUS = "2026-10-12"


def main() -> None:
    marks = {}
    clock = time.perf_counter()

    def mark(name: str) -> None:
        nonlocal clock
        now = time.perf_counter()
        marks[name] = round(now - clock, 3)
        clock = now

    with db_connection(ROOT / ".env") as db:
        calendar = database_calendar(db, DAY)
        daily, valuation = daily_sources(db, calendar)
        intervals = load_st_intervals(db)
        mark("calendar_daily_and_st_seconds")
        quotes, source = database_signal(db, PREVIOUS, daily, None)
        mark("previous_day_signal_db_seconds")
        previous = seven_factors(quotes, PREVIOUS, calendar[-22:-1], daily, valuation)
        previous = exclude_st_at_limit(db, previous, intervals)
        mark("previous_day_features_st_seconds")
        labels = morning_labels(db, DAY, previous.symbol6.tolist(), None)
        previous = previous.merge(labels, on="symbol6", how="left", validate="one_to_one")
        mark("current_morning_labels_seconds")
        with TemporaryDirectory() as folder:
            amount_cache(db, [PREVIOUS], Path(folder), None)
        mark("one_day_market_amount_seconds")
    saved = pd.read_csv(EVIDENCE / "cache/2026-10-13_training.csv.gz",
                        dtype={"signal_date": str, "symbol6": str},
                        float_precision="round_trip")
    mark("read_cached_training_seconds")
    expected = saved[saved.signal_date.eq(PREVIOUS)]
    actual = previous[previous.second_high.notna()]
    joined = actual.merge(expected, on="symbol6", suffixes=("_fresh", "_saved"),
                          validate="one_to_one")
    if len(joined) != len(actual) or len(joined) != len(expected):
        raise ValueError("One-day refreshed stock keys differ from saved full preparation")
    differences = {}
    for col in ("signal_price", "second_high", *FEATURES):
        fresh = joined[col + "_fresh"].to_numpy(dtype=float)
        cached = joined[col + "_saved"].to_numpy(dtype=float)
        gap = np.abs(fresh-cached)
        differences[col] = {"max_abs": float(gap.max()),
                            "max_relative": float((gap / np.maximum(np.abs(cached), 1e-12)).max())}
        if not np.allclose(fresh, cached, rtol=1e-6, atol=1e-8):
            raise ValueError(f"Incremental one-day data differs: {col} {differences[col]}")
    mark("one_day_identity_check_seconds")
    model = train_model(generate_training_features(saved),
                        generate_training_labels(saved))
    mark("fit_6576_rows_seconds")
    capture = pd.read_csv(EVIDENCE / "capture_replay/2026-10-13_all_predictions.csv.gz",
                          dtype={"symbol6": str}, float_precision="round_trip")
    scored = infer(model, capture)
    picks = select_top(scored)
    mark("score_327_candidates_seconds")
    stored = joblib.load(EVIDENCE / "cache/2026-10-13_model.joblib")
    if not np.allclose(model.predict_proba(capture[list(FEATURES)]),
                       stored.predict_proba(capture[list(FEATURES)]),
                       rtol=0, atol=1e-12):
        raise ValueError("Incremental fit does not reproduce full model probabilities")
    result = {"day": DAY, "previous_day": PREVIOUS,
              "sklearn_version": sklearn.__version__, "signal_source": source,
              "previous_day_candidates": len(previous),
              "previous_day_known_labels": len(actual),
              "training_rows": len(saved), "current_candidates": len(capture),
              "top1": picks.symbol6.iloc[0] if len(picks) else None,
              "probabilities_match_full_prepare": True,
              "one_day_value_differences": differences, "steps_seconds": marks,
              "sum_measured_seconds": round(sum(marks.values()), 3)}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
