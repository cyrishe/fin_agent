"""Monthly walk-forward Conv1D test on the same six-cut cohort and execution proxy."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
import torch
from torch import nn

from scripts.analyze_automl_cost_robustness import net_return
from scripts.build_automl_close_open_sequences import CHANNELS, CUTS, DATA
from scripts.experiment_automl_close_open_screen_targets import ROOT, metrics
from scripts.experiment_automl_close_open_sequence import MatrixTransform, fit_predict


SEEDS = (11, 23, 37)
EPOCHS = 20
BATCH = 512


class SmallConv(nn.Module):
    def __init__(self):
        super().__init__()
        self.network = nn.Sequential(
            nn.Conv1d(len(CHANNELS), 16, kernel_size=3, padding=1), nn.GELU(),
            nn.Conv1d(16, 8, kernel_size=3, padding=1), nn.GELU(),
            nn.Flatten(), nn.Linear(8 * len(CUTS), 1))

    def forward(self, x):
        return self.network(x).squeeze(-1)


def cnn_scores(train: pd.DataFrame, test: pd.DataFrame, seed: int) -> np.ndarray:
    torch.manual_seed(seed)
    transform = MatrixTransform()
    x = transform.fit_transform(train).reshape(-1, len(CUTS), len(CHANNELS)).transpose(0, 2, 1)
    z = transform.transform(test).reshape(-1, len(CUTS), len(CHANNELS)).transpose(0, 2, 1)
    features = torch.tensor(x.copy(), dtype=torch.float32)
    targets = torch.tensor(np.clip(train.return_1456_close40.to_numpy(), -.10, .10)*100,
                           dtype=torch.float32)
    model = SmallConv()
    optimizer = torch.optim.Adam(model.parameters(), lr=.001, weight_decay=.01)
    generator = torch.Generator().manual_seed(seed)
    for _ in range(EPOCHS):
        order = torch.randperm(len(train), generator=generator)
        for selection in order.split(BATCH):
            optimizer.zero_grad(set_to_none=True)
            prediction = model(features[selection])
            loss = torch.mean((prediction-targets[selection])**2)
            loss.backward()
            optimizer.step()
    model.eval()
    with torch.no_grad():
        return model(torch.tensor(z.copy(), dtype=torch.float32)).numpy()/100


def run() -> None:
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    source = DATA / "candidate_sequence_targets.csv.gz"
    original = pd.read_csv(source, dtype={"period": str, "symbol6": str}, low_memory=False)
    rows = original[original.sequence_complete & original.still_in_band_1455].copy()
    rows["return_1456_close40"] = rows.close_40_raw / rows.entry_1456 - 1
    picks, folds = [], []
    for period, group in rows.groupby("period", sort=False):
        dates = sorted(original.loc[original.period.eq(period), "signal_date"].unique())
        by_date = {day: part for day, part in group.groupby("signal_date", sort=False)}
        eligible = [day for i, day in enumerate(dates) if i >= 24 and day in by_date and
                    by_date[day].return_1456_close40.notna().any()]
        for month in sorted({day[:7] for day in eligible}):
            month_days = [day for day in eligible if day.startswith(month)]
            first = month_days[0]
            cutoff = dates.index(first)
            past = dates[cutoff-20:cutoff]
            if len(past) != 20 or not all(day in by_date for day in past):
                continue
            train = pd.concat([by_date[day] for day in past], ignore_index=True)
            train = train[train.return_1456_close40.notna()]
            if train.signal_date.nunique() != 20 or train.next_date.max() > first:
                raise ValueError("Monthly CNN training contains a future/immature label")
            test = pd.concat([by_date[day] for day in month_days], ignore_index=True)
            scores = {"monthly_cut1455": fit_predict(train, test, "cut1455"),
                      "monthly_matrix6": fit_predict(train, test, "matrix6")}
            for seed in SEEDS:
                scores[f"cnn_seed{seed}"] = cnn_scores(train, test, seed)
            for name, values in scores.items():
                positions = np.arange(len(test))
                for day in month_days:
                    subset = positions[test.signal_date.eq(day).to_numpy()]
                    k = subset[np.lexsort((test.symbol6.iloc[subset].to_numpy(), -values[subset]))[0]]
                    selected = test.iloc[k]
                    picks.append({"period": period, "signal_date": day, "model": name,
                                  "symbol6": selected.symbol6, "traded": True,
                                  "return": selected.return_1456_close40,
                                  "target_return": selected.return_1456_close40,
                                  "score": float(values[k])})
            folds.append({"period": period, "month": month, "train_start": past[0],
                          "train_end": past[-1], "last_label_date": train.next_date.max(),
                          "train_rows": len(train), "test_days": len(month_days),
                          "test_candidates": len(test)})
            print(f"Monthly CNN {period} {month}: {len(month_days)} days", flush=True)
    results = pd.DataFrame(picks)
    summary, monthly = metrics(results)
    results["month"] = results.signal_date.str[:7]
    results["net"] = net_return(results["return"], 50_000, .00025)
    results.to_csv(ROOT / "cnn_monthly_picks.csv", index=False)
    summary.to_csv(ROOT / "cnn_monthly_summary.csv", index=False)
    monthly.to_csv(ROOT / "cnn_monthly_monthly.csv", index=False)
    pd.DataFrame(folds).to_csv(ROOT / "cnn_monthly_folds.csv", index=False)
    (ROOT / "cnn_monthly_manifest.json").write_text(json.dumps({
        "source_path": str(source), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "sklearn_version": sklearn.__version__, "torch_version": torch.__version__,
        "model": "Conv1d(10,16,3,pad1)-GELU-Conv1d(16,8,3,pad1)-GELU-Flatten-Linear",
        "seeds": list(SEEDS), "epochs": EPOCHS, "batch": BATCH,
        "optimizer": "Adam lr=.001 weight_decay=.01", "target": "clipped actual close/14:56 open return *100",
        "preprocess": "train-only MatrixTransform; no future price in candidate qualification",
    }, indent=2) + "\n")
    print(summary[["period", "model", "days", "unknown", "net_daily_pct",
                   "positive_months", "months"]].to_string(index=False))


if __name__ == "__main__":
    run()
