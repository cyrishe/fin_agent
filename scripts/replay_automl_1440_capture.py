"""Re-score a saved exact 14:40 candidate snapshot with a prepared seven-factor model.

The snapshot was produced during the live minute by run_automl_1440_strategy.
This replay does not fetch a later quote and does not place or save an order.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn

from scripts.benchmark_automl_1440_inference import FEATURES, db_connection
from scripts.run_automl_1440_strategy import (
    _prior_amounts, database_calendar, model_for_day,
)
from scripts.run_automl_four_class_pipeline import infer, select_top


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", required=True)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--capture-decision", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    recorded = json.loads(args.capture_decision.read_text())
    if (recorded.get("signal_date") != args.day or
            recorded.get("quote_source") != "full_market_quote_api" or
            not str(recorded.get("signal_capture_at", "")).startswith(args.day + " 14:40:")):
        raise ValueError("Saved candidate file is not tied to an exact 14:40 capture")
    frame = pd.read_csv(args.capture, dtype={"symbol6": str},
                        float_precision="round_trip")
    if (len(frame) != recorded["candidate_count"] or
            frame.symbol6.duplicated().any() or
            not frame.signal_date.eq(args.day).all() or
            frame[list(FEATURES)].isna().any().any()):
        raise ValueError("Saved capture has inconsistent candidates or factors")
    with db_connection(args.env_file) as db:
        calendar = database_calendar(db, args.day)
        model, meta = model_for_day(db, args.day, None, args.cache)
        scored = infer(model, frame.drop(columns=[
            "predicted_class", "p_lt0", "p_0to1", "p_1to3", "p_ge3"]))
        selected = select_top(scored)
        picks = []
        for pick in selected.to_dict("records"):
            prior = _prior_amounts(db, pick["symbol6"], calendar[-20:], None, args.cache)
            median = float(np.median(prior)) if all(x is not None for x in prior) else None
            current = float(pick["amount_so_far"])
            gate = median is not None and current >= median
            picks.append({**pick, "amount_prior20_valid_days": sum(x is not None for x in prior),
                          "amount_prior20_median_cny": median,
                          "turnover_gate_pass": bool(gate),
                          "actionable": bool(pick["selection_rank"] == 1 and gate)})
    args.output.mkdir(parents=True, exist_ok=True)
    scored.to_csv(args.output / f"{args.day}_all_predictions.csv.gz", index=False,
                  compression={"method": "gzip", "mtime": 0}, float_format="%.17g")
    result = {"day": args.day, "mode": "saved_1440_capture_replay",
              "capture_at": recorded["signal_capture_at"],
              "capture_sha256": hashlib.sha256(args.capture.read_bytes()).hexdigest(),
              "sklearn_version": sklearn.__version__,
              "training_sha256": meta["training_sha256"],
              "model_sha256": meta["model_sha256"],
              "training_rows": meta["training_rows"],
              "candidate_count": len(frame), "picks": picks,
              "top1_actionable": bool(picks and picks[0]["actionable"]),
              "orders_placed": 0}
    (args.output / f"{args.day}_decision.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
