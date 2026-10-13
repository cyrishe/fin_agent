"""Monthly outlier and approximate A-share round-trip fee audit."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


ROOT = Path("docs/stock_automl_runs/20261013_temporal_stability")
STAMP_SELL = .0005
TRANSFER_EACH = .00001
SCENARIOS = {"1w_yi": (10_000, .0001), "5w_yi": (50_000, .0001),
             "5w_erwu": (50_000, .00025)}


def net_return(gross: pd.Series, principal: int, commission: float) -> pd.Series:
    """Assume an all-in brokerage commission, with a 5 CNY per-side minimum."""
    sale = principal * (1 + gross)
    buy_fee = max(5, principal * commission) + principal * TRANSFER_EACH
    sell_fee = (sale * commission).clip(lower=5) + sale * (STAMP_SELL + TRANSFER_EACH)
    return (sale - sell_fee - principal - buy_fee) / principal


def describe(group: pd.DataFrame, period: str, model: str, scenario: str) -> dict:
    gross = group["return"]
    net = group[f"net_{scenario}"]
    monthly = group.groupby("month")["return"].mean()
    best_month = monthly.idxmax()
    without_month = group[group.month.ne(best_month)]
    without_month_top = group.drop(group.groupby("month")["return"].idxmax())
    return {"period": period, "model": model, "scenario": scenario,
            "trades": len(group), "months": len(monthly),
            "positive_gross_months": int(monthly.gt(0).sum()),
            "gross_mean_pct": gross.mean() * 100, "net_mean_pct": net.mean() * 100,
            "best_month": best_month, "best_month_gross_pct": monthly.max() * 100,
            "ex_best_month_gross_pct": without_month["return"].mean() * 100
            if len(without_month) else None,
            "ex_best_month_net_pct": without_month[f"net_{scenario}"].mean() * 100
            if len(without_month) else None,
            "ex_month_top_trade_gross_pct": without_month_top["return"].mean() * 100,
            "ex_month_top_trade_net_pct": without_month_top[f"net_{scenario}"].mean() * 100,
            "clipped_5pct_gross_mean_pct": gross.clip(-.05, .05).mean() * 100,
            "median_month_gross_pct": monthly.median() * 100}


def run() -> None:
    rows = pd.read_csv(ROOT / "all_20day_dates.csv", dtype={"symbol6": str})
    rows = rows.dropna(subset=["return"]).copy()
    for name, (principal, commission) in SCENARIOS.items():
        rows[f"net_{name}"] = net_return(rows["return"], principal, commission)
    monthly = rows.groupby(["year", "model", "month"]).agg(
        trades=("return", "count"), gross_mean=("return", "mean"),
        net_1w_yi=("net_1w_yi", "mean"), net_5w_yi=("net_5w_yi", "mean"),
        net_5w_erwu=("net_5w_erwu", "mean"))
    monthly.to_csv(ROOT / "monthly_after_fees.csv")
    summary = []
    for (period, model), group in rows.groupby(["year", "model"]):
        for scenario in SCENARIOS:
            summary.append(describe(group, period, model, scenario))
    for model, group in rows.groupby("model"):
        for scenario in SCENARIOS:
            summary.append(describe(group, "combined", model, scenario))
    pd.DataFrame(summary).to_csv(ROOT / "cost_robustness.csv", index=False)
    (ROOT / "cost_assumptions.json").write_text(json.dumps({
        "broker_commission": "all-in for exchange and regulatory fees, per side; minimum CNY 5",
        "stamp_sell": STAMP_SELL, "transfer_each_side": TRANSFER_EACH,
        "scenarios": {name: {"principal_cny": amount, "broker_commission_rate": rate}
                      for name, (amount, rate) in SCENARIOS.items()},
        "excluded": "spread, slippage, impact, failure to fill; one BSE pick uses the same A-share proxy",
    }, ensure_ascii=False, indent=2) + "\n")
    print(pd.DataFrame(summary).to_string(index=False))


if __name__ == "__main__":
    run()
