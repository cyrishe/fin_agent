"""Read back local R24 test-minute sources against the frozen first-ten-bar evidence."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.export_limit_rebound_short_mixed_r12 import filtered_local


R24 = Path("outputs/stock_automl/limit_rebound_four_minutes_r24_20261015")
OUT = Path("outputs/stock_automl/b2_independent_recheck_20261018/audit/local_minute_readback.json")


def main() -> None:
    cohort = pd.read_pickle(R24 / "four_all_top3.pkl")
    cohort = cohort[cohort.part.eq("test")][["symbol", "d0_date"]]
    cached = pd.read_pickle(R24 / "first10_evidence.pkl")
    cached = cached.merge(cohort, on=["symbol", "d0_date"], how="inner")
    local = cached[cached.source.ne("database_finalized")].copy()
    stock_days = 0
    source_files = 0
    for path, evidence in local.groupby("source"):
        source = Path(path)
        if not source.exists():
            raise FileNotFoundError(source)
        raw = filtered_local(source, set(evidence.symbol.str[:6]))
        raw["symbol"] = raw.symbol.str[:6]
        for (symbol, d0), bars in evidence.groupby(["symbol", "d0_date"]):
            actual = raw[raw.symbol.eq(symbol)].sort_values("time").iloc[:10]
            bars = bars.sort_values("time")
            if len(actual) != 10 or len(bars) != 10:
                raise AssertionError((path, symbol, d0, "bar count"))
            if not np.array_equal(pd.to_datetime(actual.time).to_numpy(),
                                  pd.to_datetime(bars.time).to_numpy()):
                raise AssertionError((path, symbol, d0, "bar time"))
            for field in ("open", "high", "low", "close", "volume", "turnover"):
                if not np.allclose(actual[field], bars[field], rtol=0, atol=1e-6):
                    raise AssertionError((path, symbol, d0, field))
            stock_days += 1
        source_files += 1
    database_days = int(cached[cached.source.eq("database_finalized")][
        ["symbol", "d0_date"]].drop_duplicates().shape[0])
    receipt = {
        "status": "PASS", "scope": "R24 test months, local minute sources only",
        "source_files": source_files, "stock_days": stock_days, "bars": 10 * stock_days,
        "database_stock_days_not_rechecked": database_days,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
