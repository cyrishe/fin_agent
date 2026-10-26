"""CO21: extend the same B1R OOF recipe back to frozen 2025-02..07 picks."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.research_b1_extended_factors_co20 import (
    ROOT, SRC, OUT as CO20, GATES, evaluate, features, gate_columns, sha,
)

OUT = ROOT / "docs/stock_automl_runs/20261026_b1_early_extension_co21"
INPUT = ROOT / "outputs/stock_automl/b1_early_extension_co21/co7_oof_with_context.csv.gz"
PERIODS = {
    "early_2025H1": ("2025-02-07", "2025-07-31"),
    "discovery_2025H2": ("2025-08-01", "2025-12-31"),
    "validation_2026H1": ("2026-01-01", "2026-06-30"),
    "check_2026Q3": ("2026-07-01", "2026-09-30"),
    "all": ("2025-02-07", "2026-09-30"),
}


def random_same_month(base: pd.DataFrame, gate: str, draws: int = 5000) -> dict:
    rng = np.random.default_rng(20261026)
    actual = evaluate(base, gate)["net_compound_pct"]
    groups = []
    for _, month in base.groupby("month", sort=True):
        candidates = month[month.executed]
        k = int(candidates[gate].sum())
        groups.append((candidates.net.to_numpy(float), k))
    values = []
    for _ in range(draws):
        gross = 1.
        for returns, k in groups:
            if k:
                gross *= np.prod(1 + rng.choice(returns, k, replace=False))
        values.append(100 * (gross - 1))
    x = np.asarray(values)
    return dict(gate=gate, actual_net_pct=actual, draws=draws,
                random_median_pct=float(np.median(x)),
                random_p05_pct=float(np.quantile(x, .05)),
                random_p95_pct=float(np.quantile(x, .95)),
                random_not_worse_fraction=float(np.mean(x >= actual)))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    source = pd.read_csv(INPUT, dtype={"symbol6": str}, float_precision="round_trip")
    top = source[source.selection_rank.eq(1)].copy()
    old = top[top.signal_date.lt("2025-08-01")].copy()
    assert len(old) == 117 and old.signal_date.is_unique
    assert old.input_source.eq("frozen_2025").all()
    later = pd.read_csv(SRC, dtype={"symbol6": str}, float_precision="round_trip")
    overlap = top[top.signal_date.isin(later.signal_date)].merge(
        later, on="signal_date", suffixes=("_old", "_new"), validate="one_to_one")
    assert len(overlap) == 280
    assert (overlap.symbol6_old == overlap.symbol6_new).all()
    np.testing.assert_array_equal(overlap.amount_gate_old.fillna(-1),
                                  overlap.amount_gate_new.fillna(-1))
    np.testing.assert_allclose(overlap.signal_price_old, overlap.signal_price_new, rtol=0, atol=0)
    np.testing.assert_allclose(overlap.close40, overlap.exit_0940, rtol=0, atol=0)

    early = pd.DataFrame({
        "signal_date": old.signal_date, "next_date": old.next_date,
        "symbol6": old.symbol6, "signal_price": old.signal_price,
        "exit_0940": old.close40, "strong": old.strong,
        "baseline_trade": old.amount_gate.eq(1),
        "input_source": old.input_source,
    })
    later["input_source"] = "CO14_CO7_B1R_checked"
    cols = list(early.columns)
    base = pd.concat([early, later[cols]], ignore_index=True).sort_values("signal_date").reset_index(drop=True)
    assert len(base) == 401 and base.signal_date.is_unique
    base["month"] = base.signal_date.str[:7]
    base["executed"] = base.baseline_trade & base.signal_price.gt(0) & base.exit_0940.gt(0)
    assert base.baseline_trade.sum() == base.executed.sum()
    base["net"] = np.where(base.executed,
                           base.exit_0940 * .999 / (base.signal_price * 1.001) - 1,
                           np.nan)
    result, fund_paths = features(base)
    result = gate_columns(result)
    prior = pd.read_csv(CO20 / "oof_factor_panel.csv.gz", dtype={"symbol6": str},
                        float_precision="round_trip")
    common = result[result.signal_date.ge("2025-08-01")].reset_index(drop=True)
    assert len(common) == len(prior)
    assert (common[["signal_date", "symbol6"]].fillna("").to_numpy() ==
            prior[["signal_date", "symbol6"]].fillna("").to_numpy()).all()
    for col in ["net", "ret5", "ret20", "position20", "position60", "fund1", "fund5"]:
        np.testing.assert_allclose(common[col], prior[col], rtol=0, atol=1e-14, equal_nan=True)
    result.to_csv(OUT / "oof_factor_panel.csv.gz", index=False, float_format="%.17g")

    metrics, monthly, exploratory = [], [], []
    for period, (start, end) in PERIODS.items():
        frame = result[result.signal_date.between(start, end)].copy()
        original = evaluate(frame, "all")
        for gate in GATES:
            row = evaluate(frame, gate)
            row.update(period=period, gate=gate,
                       delta_vs_all_pp=row["net_compound_pct"] - original["net_compound_pct"])
            exploratory.append(row)
        for gate in ("all", "position60_low"):
            row = evaluate(frame, gate)
            row.update(period=period, gate=gate,
                       delta_vs_all_pp=row["net_compound_pct"] - original["net_compound_pct"])
            metrics.append(row)
            for month, rows in frame.groupby("month"):
                keep = rows.executed & (rows[gate] if gate != "all" else True)
                monthly.append(dict(period=period, gate=gate, month=month,
                                    trades=int(keep.sum()),
                                    net_compound_pct=100 * (np.prod(1 + rows.net.where(keep, 0.)) - 1),
                                    critical_count=int(rows.loc[keep, "net"].lt(-.02).sum())))
    pd.DataFrame(metrics).to_csv(OUT / "summary.csv", index=False)
    pd.DataFrame(monthly).to_csv(OUT / "monthly.csv", index=False)
    pd.DataFrame(exploratory).to_csv(OUT / "all_gate_descriptive.csv", index=False)
    random = {p: random_same_month(result[result.signal_date.between(a, b)], "position60_low")
              for p, (a, b) in PERIODS.items()}
    (OUT / "random_control.json").write_text(json.dumps(random, ensure_ascii=False, indent=2) + "\n")
    manifest = {
        "experiment": "CO21_20261026_B1R_early_extension",
        "input_sha256": {str(p.relative_to(ROOT)): sha(p) for p in [INPUT, SRC, *fund_paths]},
        "early_frozen_days": len(old), "overlap_checked_days": len(overlap),
        "total_signal_days": len(result), "total_executed_trades": int(result.executed.sum()),
        "factor_rule_locked_from_CO20": "position60_low", "fund_vintage_verified": False,
        "output_sha256": {p.name: sha(p) for p in OUT.glob("*.csv*")
                          if not p.name.startswith("._")},
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in manifest.items() if k not in ("input_sha256", "output_sha256")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
