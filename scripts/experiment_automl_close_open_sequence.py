"""Compare the ordered 6x10 afternoon matrix to single-cut inputs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from sklearn.linear_model import Ridge

from scripts.analyze_automl_cost_robustness import net_return
from scripts.build_automl_close_open_sequences import CHANNELS, CUTS, DATA
from scripts.experiment_automl_close_open_screen_targets import ROOT, metrics
from scripts.research_automl_close_open_objectives import Transform


LOG = {"volume_ratio", "turnover_so_far_pct", "float_mv_100m_cny", "relative_turnover"}
BINARY = {"all_intraday_lows_above_ma"}
COLUMNS = tuple(f"{name}_{clock[:5].replace(':', '')}" for clock in CUTS for name in CHANNELS)


class MatrixTransform:
    """Train-only scaling per time/channel; keep the original 6x10 order."""

    def __init__(self):
        self.numeric = np.array([i for i, col in enumerate(COLUMNS)
                                 if col.rsplit("_", 1)[0] not in BINARY])

    def raw(self, rows: pd.DataFrame) -> np.ndarray:
        a = rows[list(COLUMNS)].to_numpy(float).copy()
        for i, col in enumerate(COLUMNS):
            if col.rsplit("_", 1)[0] in LOG:
                a[:, i] = np.log1p(np.maximum(a[:, i], 0))
        a[~np.isfinite(a)] = np.nan
        return a

    def fit_transform(self, rows: pd.DataFrame) -> np.ndarray:
        a = self.raw(rows)
        self.median = pd.DataFrame(a).median().fillna(0).to_numpy()
        a = np.where(np.isnan(a), self.median, a)
        self.low, self.high = np.quantile(a[:, self.numeric], [.01, .99], axis=0)
        a[:, self.numeric] = np.clip(a[:, self.numeric], self.low, self.high)
        self.mean = a[:, self.numeric].mean(axis=0)
        self.std = a[:, self.numeric].std(axis=0)
        a[:, self.numeric] = (a[:, self.numeric]-self.mean)/np.maximum(self.std, 1e-10)
        return a

    def transform(self, rows: pd.DataFrame) -> np.ndarray:
        a = self.raw(rows)
        a = np.where(np.isnan(a), self.median, a)
        a[:, self.numeric] = np.clip(a[:, self.numeric], self.low, self.high)
        a[:, self.numeric] = (a[:, self.numeric]-self.mean)/np.maximum(self.std, 1e-10)
        return a


def cut_frame(rows: pd.DataFrame, clock: str) -> pd.DataFrame:
    code = clock[:5].replace(":", "")
    return rows[[f"{name}_{code}" for name in CHANNELS]].rename(
        columns={f"{name}_{code}": name for name in CHANNELS})


def fit_predict(train: pd.DataFrame, test: pd.DataFrame, model_type: str) -> np.ndarray:
    if model_type == "matrix6":
        transform = MatrixTransform()
        x, z = transform.fit_transform(train), None
        z = transform.transform(test)
    else:
        clock = "14:40:00" if model_type == "cut1440" else "14:55:00"
        t, u = cut_frame(train, clock), cut_frame(test, clock)
        transform = Transform(CHANNELS)
        x, z = transform.fit_transform(t), transform.transform(u)
    y = np.clip(train.return_1456_close40.to_numpy(float), -.10, .10)
    model = Ridge(alpha=100., solver="cholesky").fit(x, y)
    return model.predict(z)


def run() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    source = DATA / "candidate_sequence_targets.csv.gz"
    rows = pd.read_csv(source, dtype={"symbol6": str, "period": str})
    original_dates = {period: sorted(part.signal_date.unique())
                      for period, part in rows.groupby("period", sort=False)}
    rows = rows[rows.sequence_complete & rows.still_in_band_1455].copy()
    rows["return_1456_close40"] = rows.close_40_raw / rows.entry_1456 - 1
    if rows[list(COLUMNS)].isna().any().any() or rows.entry_1456.le(0).any():
        raise ValueError("Incomplete or invalid sequence matrix")
    all_picks, folds = [], []
    for period, group in rows.groupby("period", sort=False):
        period_dates = original_dates[period]
        by_day = {day: part for day, part in group.groupby("signal_date", sort=False)}
        for i, day in enumerate(period_dates):
            if i < 24 or day not in by_day:
                continue
            test = by_day[day]
            if not test.return_1456_close40.notna().any():
                continue
            past = period_dates[i-20:i]
            if not all(date in by_day for date in past):
                continue
            train = pd.concat([by_day[date] for date in past], ignore_index=True)
            train = train[train.return_1456_close40.notna()]
            if train.signal_date.nunique() != 20 or train.next_date.max() > day:
                raise ValueError("Missing mature training days for sequence")
            for name in ("cut1440", "cut1455", "matrix6"):
                scores = fit_predict(train, test, name)
                k = np.lexsort((test.symbol6.to_numpy(), -scores))[0]
                selected = test.iloc[k]
                all_picks.append({"period": period, "signal_date": day, "model": name,
                                  "symbol6": selected.symbol6, "traded": True,
                                  "return": selected.return_1456_close40,
                                  "target_return": selected.return_1456_close40,
                                  "score": float(scores[k]),
                                  "entry_1456": selected.entry_1456,
                                  "close_40_raw": selected.close_40_raw})
            folds.append({"period": period, "signal_date": day, "train_first": past[0],
                          "train_last": past[-1], "last_label_date": train.next_date.max(),
                          "train_rows": len(train), "test_rows": len(test)})
            if i % 30 == 0:
                print(f"Sequence fits {period} {day}: {i+1}/{len(period_dates)}", flush=True)
    picks = pd.DataFrame(all_picks)
    summary, monthly = metrics(picks)
    picks["month"] = picks.signal_date.str[:7]
    picks["net"] = net_return(picks["return"], 50_000, .00025)
    picks.to_csv(ROOT / "sequence_picks.csv", index=False)
    summary.to_csv(ROOT / "sequence_summary.csv", index=False)
    monthly.to_csv(ROOT / "sequence_monthly.csv", index=False)
    pd.DataFrame(folds).to_csv(ROOT / "sequence_folds.csv", index=False)
    (ROOT / "sequence_manifest.json").write_text(json.dumps({
        "source_path": str(source), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "entry": "14:56 1m open after observing complete 14:55 1m bar",
        "target": "T+1 09:40 1m close", "cuts": list(CUTS), "channels": list(CHANNELS),
        "cohort": "old 14:40 candidates still in 3%-6% at 14:55 with complete 235 observed bars",
        "sklearn_version": sklearn.__version__,
    }, indent=2) + "\n")
    print(summary[["period", "model", "days", "unknown", "net_daily_pct", "positive_months",
                   "months"]].to_string(index=False))


if __name__ == "__main__":
    run()
