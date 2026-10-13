"""Materialize D4's verified 240-bar沪深 stock-day universe from daily prices.

D4 checked every traded stock-day against the minute table: 223,896 traded,
223,894 exact full days, with only 688816 on Aug 5/6 missing. This avoids a
second full-table scan while making that provenance explicit and assertable.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


ROOT = Path("outputs/stock_automl/deep_v_20261013_d5/model_v1")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    source = args.root / "source"
    price = pd.concat([
        pd.read_csv(source / f"price_2026-{month}.csv.gz", dtype={"stk_code": str},
                    usecols=["trade_date", "stk_code", "volume"])
        for month in ("08", "09", "10")], ignore_index=True)
    price["signal_date"] = price.trade_date.astype(str).str[:10]
    price["symbol6"] = price.stk_code.str[:6]
    traded = price[
        price.signal_date.between("2026-08-05", "2026-10-12") &
        price.symbol6.str.startswith(("0", "3", "6")) & price.volume.gt(0)
    ][["signal_date", "symbol6"]].copy()
    if len(traded) != 223896 or traded.signal_date.nunique() != 43 or traded.duplicated().any():
        raise ValueError("daily traded universe differs from D4 verification")
    missing = (traded.symbol6.eq("688816") &
               traded.signal_date.isin(["2026-08-05", "2026-08-06"]))
    if int(missing.sum()) != 2:
        raise ValueError("D4's two known missing stock-days were not found")
    full = traded[~missing].sort_values(["signal_date", "symbol6"])
    full.to_csv(args.root / "full_universe.csv.gz", index=False, compression="gzip")
    print({"traded": len(traded), "full240": len(full),
           "days": full.signal_date.nunique()}, flush=True)


if __name__ == "__main__":
    main()
