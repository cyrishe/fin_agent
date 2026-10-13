"""Re-fit a smaller D3 feature set chosen from the first 13 test days only."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from scripts.experiment_automl_deep_v_d3 import BASE, OPEN, score_day


ROOT = Path("outputs/stock_automl/deep_v_20261013/model_v1")
DROP = {"close_location", "lower_wick", "flow_main_pct", "flow_main_mean3"}
SLIM = [name for name in BASE if name not in DROP]


def main() -> None:
    panel = pd.read_csv(ROOT / "panel.csv.gz", dtype={"symbol6": str})
    days = sorted(panel.signal_date.unique())
    predictions = []
    metrics = []
    for i in range(20, len(days)):
        day = days[i]
        train = panel[panel.signal_date.isin(days[i - 20:i])]
        test = panel[panel.signal_date.eq(day)]
        for timing, features in (("prior", SLIM), ("open", SLIM + OPEN)):
            scored, metric = score_day(day, f"hgb_{timing}", test, train, features)
            scored["model"] = f"hgb_{timing}_slim"
            metric["model"] = f"hgb_{timing}_slim"
            predictions.append(scored)
            metrics.append(metric)
        print(f"scored slim {day}", flush=True)
    pred = pd.concat(predictions, ignore_index=True)
    pred.to_csv(ROOT / "predictions_slim.csv.gz", index=False, compression="gzip")
    pd.DataFrame(metrics).to_csv(ROOT / "daily_slim.csv", index=False)
    pred[pred.rank_all.le(20)].to_csv(ROOT / "top20_slim.csv", index=False)


if __name__ == "__main__":
    main()
