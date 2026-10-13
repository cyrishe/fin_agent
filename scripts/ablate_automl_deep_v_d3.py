"""Date-block feature-family ablation on the first 13 rolling D3 test dates."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from scripts.experiment_automl_deep_v_d3 import BASE, OPEN, score_day


ROOT = Path("outputs/stock_automl/deep_v_20261013/model_v1")
FAMILIES = {
    "trend": ["ret1", "ret5", "ret20", "ma5_gap", "ma20_gap", "ma20_slope5", "position20"],
    "prev_candle": ["close_location", "lower_wick"],
    "vol_liquidity": ["amplitude_mean14", "range5_count20", "volatility20",
                      "amount_ratio20", "amount_trend5", "turnover_pct"],
    "moneyflow": ["flow_main_pct", "flow_main_mean3"],
    "market": ["market_median_ret1", "market_down_share", "relative_ret1"],
    "open_price": ["open_gap", "early_return", "early_range", "early_drawdown",
                   "early_position", "opening_relative_ret"],
    "open_amount": ["early_amount_ratio20", "early_amount_second_first"],
}


def main() -> None:
    panel = pd.read_csv(ROOT / "panel.csv.gz", dtype={"symbol6": str})
    days = sorted(panel.signal_date.unique())
    metrics = []
    for i in range(20, min(33, len(days))):
        day = days[i]
        train = panel[panel.signal_date.isin(days[i - 20:i])]
        test = panel[panel.signal_date.eq(day)]
        for timing, base in (("prior", BASE), ("open", BASE + OPEN)):
            families = ["trend", "prev_candle", "vol_liquidity", "moneyflow", "market"]
            if timing == "open":
                families += ["open_price", "open_amount"]
            for family in families:
                features = [name for name in base if name not in FAMILIES[family]]
                _, metric = score_day(day, f"hgb_{timing}", test, train, features)
                metric["ablation"] = family
                metrics.append(metric)
        print(f"ablated {day}", flush=True)
    pd.DataFrame(metrics).to_csv(ROOT / "ablation_development.csv", index=False)


if __name__ == "__main__":
    main()
