"""Compare the 20-day tradable-ST model with prior no-ST and reranked controls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


ARM = "rolling20_tminus1"


def summarize(rows: pd.DataFrame) -> dict:
    returns = rows.actual_0940_return.dropna()
    return {"selected_days": len(rows), "observed_days": len(returns),
            "ge3": int(returns.ge(.03).sum()),
            "mean_return_pct": float(returns.mean() * 100)
            if len(returns) else None}


def compare(current: pd.DataFrame, no_st: pd.DataFrame,
            reranked: pd.DataFrame, trades: pd.DataFrame) -> dict:
    no_st = no_st[no_st.arm.eq(ARM)]
    reranked = reranked[reranked.arm.eq(ARM)]
    paired = current[["signal_date", "symbol6", "actual_0940_return"]].merge(
        no_st[["signal_date", "symbol6", "actual_0940_return"]],
        on="signal_date", suffixes=("_current", "_no_st"),
        validate="one_to_one")
    delta = paired.actual_0940_return_current - paired.actual_0940_return_no_st
    fixed = trades[trades.exit_strategy.eq("fixed_0940")]
    st = fixed[fixed.historical_st_type.isin(("S", "Y"))]
    return {
        "tradable_st_20d": summarize(current),
        "no_st_20d": summarize(no_st),
        "original_model_nonlimit_rerank_20d": summarize(reranked),
        "paired_common_days": {
            "days": len(paired),
            "same_top1_stock": int(paired.symbol6_current.eq(paired.symbol6_no_st).sum()),
            "mean_delta_pct_points": float(delta.mean() * 100),
            "better_days": int(delta.gt(0).sum()),
            "worse_days": int(delta.lt(0).sum()),
            "same_return_days": int(delta.eq(0).sum()),
        },
        "selected_historical_st": {
            "trades": len(st), "at_entry_limit": int(st.entry_at_limit.sum()),
            "sale_ge3": int(st.exit_return.ge(.03).sum()),
            "mean_sale_return_pct": float(st.exit_return.mean() * 100)
            if len(st) else None,
        },
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--current", type=Path, required=True)
    p.add_argument("--no-st", type=Path, required=True)
    p.add_argument("--reranked", type=Path, required=True)
    p.add_argument("--trades", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    kwargs = {"dtype": {"symbol6": str}}
    report = compare(pd.read_csv(a.current, **kwargs),
                     pd.read_csv(a.no_st, **kwargs),
                     pd.read_csv(a.reranked, **kwargs),
                     pd.read_csv(a.trades, **kwargs))
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
