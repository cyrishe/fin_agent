"""D3 extension: learn event occurrence as well as recovery on exact full-day labels."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from scripts.experiment_automl_deep_v_d3 import BASE, OPEN, make_model


ROOT = Path("outputs/stock_automl/deep_v_20261013/model_v1")


def main() -> None:
    panel = pd.read_csv(ROOT / "panel.csv.gz", dtype={"symbol6": str})
    panel.label = pd.to_numeric(panel.label, errors="coerce")
    full = pd.read_csv(ROOT / "full_universe.csv.gz", dtype={"symbol6": str})
    full["full240"] = True
    panel = panel.merge(full, on=["signal_date", "symbol6"], how="left",
                        validate="one_to_one")
    panel["full240"] = panel.full240.eq(True)
    if not panel.loc[panel.is_event, "full240"].all():
        raise ValueError("D1 event missing from exact full-day universe")
    conditional = pd.read_csv(ROOT / "predictions.csv.gz", dtype={"symbol6": str})
    days = sorted(panel.signal_date.unique())
    outputs = []
    metrics = []
    specs = (("prior", BASE), ("open", BASE + OPEN))
    for i in range(20, len(days)):
        day = days[i]
        train = panel[panel.signal_date.isin(days[i - 20:i]) & panel.full240]
        test = panel[panel.signal_date.eq(day)].copy()
        for timing, features in specs:
            train_set = train[train.p40.notna()] if timing == "open" else train
            test_set = test[test.p40.notna()].copy() if timing == "open" else test.copy()
            x_train = train_set[features].replace([np.inf, -np.inf], np.nan)
            x_test = test_set[features].replace([np.inf, -np.inf], np.nan)
            targets = {"event": train_set.is_event.astype(int),
                       "direct": train_set.label.eq(1).astype(int)}
            scores = {}
            for target, y in targets.items():
                model = make_model("hgb_prior")
                model.fit(x_train, y)
                scores[target] = model.predict_proba(x_test)[:, 1]
            cond = conditional[(conditional.signal_date.eq(day)) &
                               (conditional.model.eq("hgb_open" if timing == "open" else "hgb_prior"))]
            test_set = test_set.merge(cond[["symbol6", "score"]], on="symbol6",
                                      how="left", validate="one_to_one")
            if test_set.score.isna().any():
                raise ValueError("conditional score missing")
            scores["joint"] = scores["event"] * test_set.score.to_numpy()
            for target in ("event", "direct", "joint"):
                result = test_set[["signal_date", "symbol6", "label", "is_event",
                                   "full240", "prior_close", "p40"]].copy()
                result["score"] = scores[target]
                result["model"] = f"{target}_{timing}"
                result["rank_all"] = result.score.rank(ascending=False, method="first").astype(int)
                outputs.append(result)
                observed = result[result.full240]
                target_event = observed.is_event.astype(int)
                target_v = observed.label.eq(1).astype(int)
                top = result.nsmallest(20, "rank_all")
                metrics.append({"day": day, "model": f"{target}_{timing}",
                                "train_rows": len(train_set),
                                "score_universe": len(result),
                                "full240": len(observed),
                                "event_base_rate": float(target_event.mean()),
                                "deepv_base_rate": float(target_v.mean()),
                                "event_auc": roc_auc_score(target_event, observed.score),
                                "deepv_auc": roc_auc_score(target_v, observed.score),
                                "deepv_ap": average_precision_score(target_v, observed.score),
                                "top20_full": int(top.full240.sum()),
                                "top20_events": int(top.is_event.sum()),
                                "top20_deepv": int(top.label.eq(1).sum())})
        print(f"scored full-universe {day}", flush=True)
    predictions = pd.concat(outputs, ignore_index=True)
    daily = pd.DataFrame(metrics)
    predictions.to_csv(ROOT / "predictions_universe.csv.gz", index=False, compression="gzip")
    daily.to_csv(ROOT / "daily_universe.csv", index=False)
    predictions[predictions.rank_all.le(20)].to_csv(ROOT / "top20_universe.csv", index=False)
    print(daily.groupby("model")[["event_auc", "deepv_auc", "deepv_ap",
                                  "top20_events", "top20_deepv"]].mean().round(4),
          flush=True)


if __name__ == "__main__":
    main()
