"""Verify reversed trading-day rank against the original distance encoding."""
from pathlib import Path
import hashlib
import json

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
PROBABILITY_COLUMNS = ["p_lt0", "p_0to1", "p_1to3", "p_ge3"]
report = {}
for cohort in ("feb_jul", "aug_oct"):
    report[cohort] = {}
    for version, suffix in (("1.7.1", ""), ("1.9.1", "_sk191")):
        old_path = ROOT / f"{cohort}_hgb_b1_seed42_age_feature{suffix}/scores.csv.gz"
        new_path = ROOT / f"{cohort}_hgb_b1_seed42_recency_feature{suffix}/scores.csv.gz"
        old = pd.read_csv(old_path, dtype={"signal_date": str, "symbol6": str},
                          float_precision="round_trip")
        new = pd.read_csv(new_path, dtype={"signal_date": str, "symbol6": str},
                          float_precision="round_trip")
        old = old.sort_values(["signal_date", "symbol6"]).reset_index(drop=True)
        new = new.sort_values(["signal_date", "symbol6"]).reset_index(drop=True)
        keys_equal = old[["signal_date", "symbol6"]].equals(
            new[["signal_date", "symbol6"]])
        classes_equal = old.predicted_class.equals(new.predicted_class)
        probability_equal = np.array_equal(
            old[PROBABILITY_COLUMNS].to_numpy(),
            new[PROBABILITY_COLUMNS].to_numpy())
        same_outcomes = all(np.allclose(old[column], new[column], rtol=0, atol=0,
                                        equal_nan=True)
                            for column in ("signal_price", "second_high", "close_40"))
        if not all((keys_equal, classes_equal, probability_equal, same_outcomes)):
            raise ValueError(f"Reversed rank changed predictions: {cohort}/{version}")
        report[cohort][version] = {
            "candidate_rows": len(old),
            "prediction_days": int(old.signal_date.nunique()),
            "all_candidate_probabilities_exactly_equal": probability_equal,
            "all_predicted_classes_equal": classes_equal,
            "all_prices_and_outcomes_equal": same_outcomes,
            "old_score_sha256": hashlib.sha256(old_path.read_bytes()).hexdigest(),
            "new_score_sha256": hashlib.sha256(new_path.read_bytes()).hexdigest(),
        }
for K in range(1, 21):
    distance = 21 - K
    if not np.isclose(2 ** ((K - 20) / 10), 2 ** (-(distance - 1) / 10)):
        raise ValueError(f"Sample weight mismatch at K={K}")
report["weight_formula"] = "2 ** ((K-20)/10) = 2 ** (-(distance-1)/10)"
(ROOT / "recency_encoding_equivalence.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps(report, ensure_ascii=False, indent=2))
