"""Outcome-completeness and dependent-date uncertainty diagnostics; no tuning."""
import numpy as np
import pandas as pd

from scripts.research_automl_close_open_objectives import ROOT, summarize


def run():
    picks = pd.read_csv(ROOT / "picks.csv", dtype={"period": str, "symbol6": str})
    core = picks[~picks.model.str.startswith("meta_")]
    missing = core.loc[core.net.isna(), ["period", "signal_date"]].drop_duplicates()
    complete = core.merge(missing.assign(exclude=True), on=["period", "signal_date"], how="left")
    complete = complete[complete.exclude.isna()].drop(columns="exclude")
    summarize(complete).to_csv(ROOT / "all_models_known_dates_metrics.csv", index=False)
    rows = []
    # Diagnostic interval conditional on this already-explored sample. It does
    # not adjust for repeated model selection or guarantee future stationarity.
    for name, group in core.groupby("model"):
        if group.net.isna().any():
            continue
        rng, parts = np.random.default_rng(20261014), []
        for _, segment in group.groupby("period", sort=False):
            a = segment.sort_values("signal_date").net.to_numpy()
            n = len(a)
            starts = rng.integers(0, n, size=(4000, int(np.ceil(n/5))))
            sampled = a[(starts[:, :, None] + np.arange(5)) % n].reshape(4000, -1)[:, :n]
            parts.append(sampled)
        sampled = np.concatenate(parts, axis=1)
        low, high = np.quantile(sampled.mean(axis=1), [.025, .975]) * 100
        rows.append({"model": name, "days": len(group), "net_mean_pct": group.net.mean()*100,
                     "block5_low_pct": low, "block5_high_pct": high})
    pd.DataFrame(rows).to_csv(ROOT / "block_bootstrap.csv", index=False)
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    run()
