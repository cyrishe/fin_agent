"""Verify the frozen 2026 Jan–Jul tradable-ST training input and amount gate."""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.experiment_automl_mk_1m_rolling20 import one_fold


SOURCE = Path("outputs/stock_automl/mk_1m_jan_jul/tradable_st_candidates.csv.gz")
REPORT = Path("docs/stock_automl_runs/20261011_mk_1m_tradable_st_rolling20")
MARKET = Path("docs/stock_automl_runs/20261011_mk_1m_three_st_policies/"
              "tradable_st_new_exit_market/market_amount_1440.csv")
GATE = Path("docs/stock_automl_runs/20261011_mk_1m_market_amount_board/gate_daily.csv")


def verify() -> dict:
    summary = json.loads((REPORT / "summary.json").read_text())
    source_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    if source_hash != summary["data_sha256"]:
        raise ValueError("The frozen training input differs from the reported input")
    rows = pd.read_csv(SOURCE, dtype={"symbol6": str, "signal_date": str,
                                      "next_date": str}, low_memory=False)
    dates = sorted(rows.signal_date.unique())
    if len(rows) != summary["source_rows"] or len(dates) != summary["signal_dates"]:
        raise ValueError("Frozen candidate coverage differs from the report")

    saved = pd.read_csv(REPORT / "top2_verified.csv", dtype={"symbol6": str})
    checked_folds = []
    with tempfile.TemporaryDirectory() as directory:
        for index in (20, len(dates) - 2):
            day = dates[index]
            one_fold(rows, dates, index, Path(directory))
            actual = pd.read_csv(Path(directory) / f"{day}_picks.csv",
                                 dtype={"symbol6": str}).sort_values("selection_rank")
            expected = saved[saved.signal_date.eq(day)].sort_values("selection_rank")
            if (actual.symbol6.tolist() != expected.symbol6.tolist() or
                    actual.predicted_class.tolist() != expected.predicted_class.tolist()):
                raise ValueError(f"Re-fitted picks differ on {day}")
            for column in ("p_ge3", "p_1to3", "actual_0940_return"):
                if not np.allclose(actual[column], expected[column],
                                   rtol=0, atol=1e-12, equal_nan=True):
                    raise ValueError(f"Re-fitted {column} differs on {day}")
            checked_folds.append(day)

    market = pd.read_csv(MARKET, dtype={"trade_date": str}).sort_values("trade_date")
    market["prior5"] = market.amount_1440_100m_cny.shift(1).rolling(5).mean()
    gate = pd.read_csv(GATE, dtype={"选股日": str, "Top1股票": str})
    decisions = gate.merge(market[["trade_date", "prior5"]],
                           left_on="选股日", right_on="trade_date", validate="one_to_one")
    if not decisions.prior5.ge(20000).equals(decisions["是否交易"]):
        raise ValueError("Saved market-amount gate decisions differ")
    top1 = pd.read_csv(REPORT / "top1_verified.csv",
                       dtype={"signal_date": str, "symbol6": str})
    trades = decisions[decisions["是否交易"]].merge(
        top1[["signal_date", "symbol6", "actual_0940_return"]],
        left_on="选股日", right_on="signal_date", how="left", validate="one_to_one")
    observed = trades[trades.symbol6.notna()]
    if (len(decisions) != 118 or decisions["是否交易"].sum() != 97 or
            len(observed) != 94 or
            observed.symbol6.tolist() != observed["Top1股票"].tolist() or
            abs(observed.actual_0940_return.mean() * 100 - 0.5117235862942326) > 1e-9):
        raise ValueError("Saved 94-trade market-gated result differs")
    return {"source_sha256": source_hash, "candidate_rows": len(rows),
            "checked_training_folds": checked_folds,
            "gate_pass_days": int(decisions["是否交易"].sum()),
            "top1_trades": len(observed),
            "mean_0940_return_pct": observed.actual_0940_return.mean() * 100}


if __name__ == "__main__":
    print(json.dumps(verify(), ensure_ascii=False, indent=2))
