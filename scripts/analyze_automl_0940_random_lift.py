"""Compare each day's frozen Top1 with a uniform draw from that day's 14:40 pool."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts.experiment_automl_second_high_four_class import four_class


ROOT = Path("docs/stock_automl_runs")
POOL = ROOT / "20261010_four_class_short_window_0940/daily_candidate_class_distribution.csv"
MODEL19 = ROOT / "20261010_four_class_validated_pure_leaves/top1_verified.csv"
MODEL20 = ROOT / "20261010_four_class_baseline_recent20/baseline_top1.csv"
OUT = ROOT / "20261010_four_class_0940_random_lift"


def poisson_binomial_pmf(probabilities: list[float]) -> list[float]:
    """Exact hit-count distribution for one independent random draw per date."""
    pmf = [1.0]
    for p in probabilities:
        following = [0.0] * (len(pmf) + 1)
        for hits, mass in enumerate(pmf):
            following[hits] += mass * (1.0 - p)
            following[hits + 1] += mass * p
        pmf = following
    return pmf


def summarize(daily: pd.DataFrame, target: str, model: str) -> dict:
    probabilities = daily[f"random_p_{target}"].tolist()
    hits = int(daily[f"{model}_{target}"].sum())
    expected = sum(probabilities)
    pmf = poisson_binomial_pmf(probabilities)
    return {
        "target": target,
        "model": model,
        "days": len(daily),
        "model_hits": hits,
        "model_rate": hits / len(daily),
        "random_expected_hits": expected,
        "random_expected_rate": expected / len(daily),
        "hit_rate_lift": hits / expected if expected else None,
        "one_sided_p_at_least_model_hits": sum(pmf[hits:]),
        "one_sided_p_at_most_model_hits": sum(pmf[:hits + 1]),
        "null_count_pmf": pmf,
    }


def main() -> None:
    pool = pd.read_csv(POOL)
    pool = pool.loc[(pool.outcome == "0940_close") & (pool.signal_date >= "2026-09-02")].copy()
    old = pd.read_csv(MODEL19, dtype={"symbol6": str})
    old = old.loc[(old.arm == "baseline19") & (old.selection_rank == 1),
                  ["signal_date", "symbol6", "name", "actual_0940_return", "actual_0940_class"]]
    old = old.rename(columns={"symbol6": "model19_symbol6", "name": "model19_name",
                              "actual_0940_return": "model19_0940_return",
                              "actual_0940_class": "model19_0940_class"})
    recent = pd.read_csv(MODEL20, dtype={"symbol6": str})
    recent = recent.loc[recent.selection_rank == 1,
                        ["signal_date", "symbol6", "name", "actual_0940_return"]].copy()
    recent["model20_0940_class"] = four_class(recent.actual_0940_return).astype(str)
    recent = recent.rename(columns={"symbol6": "model20_symbol6", "name": "model20_name",
                                    "actual_0940_return": "model20_0940_return"})
    daily = pool.merge(old, on="signal_date", validate="one_to_one").merge(
        recent, on="signal_date", validate="one_to_one")
    if len(daily) != 20 or daily.signal_date.nunique() != 20:
        raise ValueError("Expected the same 20 test signal dates in all inputs")
    if not daily.candidate_rows.eq(daily.known + daily.unknown).all():
        raise ValueError("Pool count does not reconcile")
    if not daily.known.eq(daily[["ge3", "1to3", "0to1", "lt0"]].sum(axis=1)).all():
        raise ValueError("Known outcomes do not reconcile")
    if daily[["model19_0940_class", "model20_0940_class"]].eq("UNKNOWN").any().any():
        raise ValueError("A selected Top1 has no observed 09:40 outcome")

    # Unknown morning outcomes remain in the ex-ante candidate pool; they are not hits.
    daily["random_p_ge3"] = daily.ge3 / daily.candidate_rows
    daily["random_p_ge1"] = (daily.ge3 + daily["1to3"]) / daily.candidate_rows
    daily["random_p_lt0"] = daily.lt0 / daily.candidate_rows
    for model in ("model19", "model20"):
        outcome = daily[f"{model}_0940_class"]
        daily[f"{model}_ge3"] = outcome.eq("ge3").astype(int)
        daily[f"{model}_ge1"] = outcome.isin(["ge3", "1to3"]).astype(int)
        daily[f"{model}_lt0"] = outcome.eq("lt0").astype(int)
    summaries = [summarize(daily, target, model)
                 for target in ("ge3", "ge1", "lt0")
                 for model in ("model19", "model20")]
    if (int(daily.candidate_rows.sum()), int(daily.ge3.sum()),
            int(daily.unknown.sum())) != (5984, 455, 3):
        raise ValueError("Candidate data changed from frozen report")
    if [int(daily[f"model19_{c}"].sum()) for c in ("ge3", "ge1", "lt0")] != [3, 9, 6]:
        raise ValueError("Frozen model19 results changed")
    OUT.mkdir(parents=True, exist_ok=True)
    daily.to_csv(OUT / "daily_random_lift.csv", index=False, float_format="%.10f")
    (OUT / "summary.json").write_text(
        json.dumps(summaries, indent=2, ensure_ascii=False), encoding="utf-8")
    for row in summaries:
        print(row["model"], row["target"], "hits", row["model_hits"],
              "expected", f'{row["random_expected_hits"]:.4f}',
              "lift", f'{row["hit_rate_lift"]:.3f}',
              "p>=", f'{row["one_sided_p_at_least_model_hits"]:.6f}')


if __name__ == "__main__":
    main()
