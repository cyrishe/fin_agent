"""Export the compact C1430-A review rows for the main D2-D4 target."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from sklearn.metrics import average_precision_score

from scripts.experiment_automl_limit_rebound_1430_r7 import (
    INTRADAY_FEATURES, OUT, PRIOR_FEATURES, add_labels, split,
)
from scripts.experiment_automl_limit_rebound_d0_open_r5 import build_panel, load_daily
from scripts.experiment_automl_limit_rebound_model import make_model
from scripts.experiment_automl_limit_rebound_st_size_r4 import balanced_weights


def main(out: Path = OUT) -> dict:
    summary = json.loads((out / "summary.json").read_text())
    if sklearn.__version__ != summary["sklearn"]:
        raise RuntimeError(f"Use scikit-learn {summary['sklearn']} to reproduce train scores")
    daily = load_daily()
    panel, _ = build_panel(daily, out)
    panel = panel[panel.d1_date.between("2026-01-05", "2026-07-31")].copy()
    panel["d1_i"] = panel.date_i + 1
    minute = pd.read_csv(out / "minute_1430_features.csv.gz",
                         dtype={"symbol": str, "d1_date": str})
    panel = panel.merge(minute, on=["symbol", "d1_date"], validate="one_to_one")
    panel = panel[panel.d1_1430_price.lt(panel.d1_cap - .005)].copy()
    panel = add_labels(panel, daily)
    train, valid, refit, test = split(panel, sorted(daily.trade_date.unique()), 3)
    expected = summary["targets"]["next3"]
    assert (len(train), len(valid), len(refit), len(test)) == (
        expected["train_rows"], expected["models"]["d0_plus_1430"]["valid"]["rows"],
        expected["refit_rows"], expected["models"]["d0_plus_1430"]["test"]["rows"])
    assert train.d0_st.eq(0).all() and train.d1_st.eq(0).all()
    cols = list(PRIOR_FEATURES) + list(INTRADAY_FEATURES)
    model = make_model("shallow_boost")
    model.fit(train[cols], train.label_next3,
              sample_weight=balanced_weights(train.label_next3))
    train_score = model.predict_proba(train[cols])[:, 1]
    assert np.isclose(average_precision_score(train.label_next3, train_score),
                      expected["models"]["d0_plus_1430"]["train_fit_in"]["ap"])

    result = {"experiment": summary["experiment"], "target": "D2-D4 any sealed-limit close",
              "source_summary_sha256": hashlib.sha256((out / "summary.json").read_bytes()).hexdigest(),
              "sets": {}}
    for name, frame, score in (("train", train, train_score),
                               ("valid", valid, None), ("test", test, None)):
        frame = frame.copy()
        if score is None:
            saved = pd.read_csv(out / f"next3_d0_plus_1430_{name}.csv.gz",
                                dtype={"symbol": str, "d0_date": str, "d1_date": str})
            frame = frame.merge(saved[["symbol", "d0_date", "d1_date", "score"]],
                                on=["symbol", "d0_date", "d1_date"],
                                validate="one_to_one")
            assert len(frame) == len(saved)
        else:
            frame["score"] = score
        frame = frame.sort_values(["d1_date", "score", "symbol"],
                                  ascending=[True, False, True]).copy()
        frame["day_rank"] = frame.groupby("d1_date").cumcount() + 1
        selected = frame[["symbol", "d0_date", "d1_date", "score", "day_rank",
                          "d1_1430_price", "d1_up", "d2_up", "d3_up", "d4_up",
                          "label_next3"]].copy()
        for col in ("d1_up", "d2_up", "d3_up", "d4_up"):
            selected[col] = selected[col].astype(int)
        path = out / f"review_next3_{name}.csv.gz"
        selected.to_csv(path, index=False, compression="gzip", float_format="%.10g")
        result["sets"][name] = {
            "rows": len(selected), "start": selected.d1_date.min(),
            "end": selected.d1_date.max(), "positive": int(selected.label_next3.sum()),
            "top3_hits": int(selected.loc[selected.day_rank.le(3), "label_next3"].sum()),
            "top3_picks": int(selected.day_rank.le(3).sum()),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        metric_key = "train_fit_in" if name == "train" else name
        assert result["sets"][name]["top3_hits"] == expected["models"]["d0_plus_1430"][metric_key]["top"]["3"]["hits"]
    (out / "review_next3_manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
