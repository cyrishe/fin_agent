"""Cache 14:40 A-share turnover and breadth for the Aug–Oct replay."""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.report_automl_daily_market_context import MARKET_SQL


OUT = Path("outputs/stock_automl/aug_oct_single_benchmark_market.csv")
CONTEXT = Path("docs/stock_automl_runs/20261011_aug_oct_single_benchmark/market_context.csv")
JULY = Path("docs/stock_automl_runs/20261011_mk_1m_three_st_policies/"
            "tradable_st_new_exit_market/market_amount_1440.csv")


def fetch(output: Path, env_file: Path, refresh_dates: set[str] | None = None) -> pd.DataFrame:
    old = pd.read_csv(JULY, dtype={"trade_date": str})
    saved = (pd.read_csv(output, dtype={"trade_date": str}) if output.exists()
             else pd.DataFrame(columns=["trade_date", "amount_1440_100m_cny",
                                        "symbols_at_1440", "minute_rows", "exact_rows"]))
    if refresh_dates:
        saved = saved[~saved.trade_date.isin(refresh_dates)].copy()
    with db_connection(env_file) as db:
        breadth = query(db, MARKET_SQL, ("2026-07-01", "2026-10-09"))
        breadth["trade_date"] = pd.to_datetime(breadth.trade_date).dt.strftime("%Y-%m-%d")
        dates = breadth.loc[breadth.trade_date.ge("2026-08-01"), "trade_date"].tolist()
        for day in dates:
            if day in saved.trade_date.tolist():
                continue
            values = query(db, """SELECT COUNT(*) AS minute_rows,
                SUM(amount)/100000000 AS amount_1440_100m_cny,
                SUM(is_finalized=1 AND is_fallback=0 AND source_snapshot_time=bar_end_time)
                  AS exact_rows
                FROM aiia_stock_realtime_minute_snapshot_full
                WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
                  AND bar_end_time BETWEEN %s AND %s
                  AND LEFT(stk_code,1) IN ('0','3','6')""",
                (day, day + " 09:31:00", day + " 14:40:00")).iloc[0].to_dict()
            coverage = query(db, """SELECT COUNT(*) AS symbols_at_1440
                FROM aiia_stock_realtime_minute_snapshot_full
                WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
                  AND bar_end_time=%s AND LEFT(stk_code,1) IN ('0','3','6')""",
                (day, day + " 14:40:00")).iloc[0].to_dict()
            saved = pd.concat([saved, pd.DataFrame([{"trade_date": day, **values,
                                                      **coverage}])], ignore_index=True)
            output.parent.mkdir(parents=True, exist_ok=True)
            saved.sort_values("trade_date").to_csv(output, index=False)
            print(f"{day}: {values['amount_1440_100m_cny']} 亿元, "
                  f"{coverage['symbols_at_1440']} symbols", flush=True)
    market = breadth.merge(pd.concat([old, saved], ignore_index=True),
                           on="trade_date", how="left", validate="one_to_one")
    for column in ("amount_1440_100m_cny", "symbols_at_1440", "minute_rows", "exact_rows"):
        market[column] = pd.to_numeric(market[column], errors="coerce")
    market["amount_valid"] = (market.trade_date.le("2026-07-31") |
                              (market.symbols_at_1440.ge(5000) &
                               market.minute_rows.ge(market.symbols_at_1440 * 220 * .98) &
                               market.exact_rows.div(market.minute_rows).ge(.95)))
    return market


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--context-output", type=Path, default=CONTEXT)
    parser.add_argument("--refresh-dates", default="")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    market = fetch(args.output, args.env_file,
                   set(filter(None, args.refresh_dates.split(","))))
    args.context_output.parent.mkdir(parents=True, exist_ok=True)
    market.to_csv(args.context_output, index=False)


if __name__ == "__main__":
    main()
