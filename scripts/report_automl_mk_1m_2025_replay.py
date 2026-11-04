"""Report 2025 fixed/new exits and prior market context for the tradable-ST policy."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.report_automl_daily_market_context import MARKET_SQL
from scripts.report_automl_tradable_st_new_exit_market import (
    NEXT_CLOSES, attach_market, make_daily, market_amounts, summarize,
)


BASE = Path("outputs/stock_automl/mk_1m_2025_mar_aug")


def run(base: Path, output: Path, env_file: Path,
        expected_source_days: int = 144, expected_test_days: int = 123,
        backtest_dir: Path | None = None,
        candidate_source: Path | None = None) -> dict:
    backtest_dir = backtest_dir or base / "backtest"
    candidate_source = candidate_source or base / "cohort/trainable_candidates.csv.gz"
    picks = pd.read_csv(backtest_dir / "top2_verified.csv",
                        dtype={"symbol6": str, "signal_date": str})
    candidates = pd.read_csv(candidate_source,
                             usecols=["signal_date", "symbol6", *NEXT_CLOSES],
                             dtype={"symbol6": str, "signal_date": str}, low_memory=False)
    folds = pd.read_csv(backtest_dir / "folds.csv", dtype={"day": str})
    dates = sorted(folds.day.unique())
    if len(dates) != expected_test_days:
        raise ValueError(f"Expected {expected_test_days} out-of-sample days")
    daily, trades = make_daily(picks, candidates, dates, expected_picks=None)
    amount = market_amounts((base / "source_days",), output / "market_amount_1440.csv",
                            expected_days=expected_source_days)
    with db_connection(env_file) as db:
        breadth = query(db, MARKET_SQL, (amount.trade_date.min(), amount.trade_date.max()))
    breadth["trade_date"] = pd.to_datetime(breadth.trade_date).dt.strftime("%Y-%m-%d")
    daily = attach_market(daily, amount, breadth)
    monthly, summary = summarize(trades)
    for column in ("前一交易日上涨家数", "前一交易日下跌家数"):
        daily[column] = daily[column].astype(int)
    output.mkdir(parents=True, exist_ok=True)
    daily.to_csv(output / "daily.csv", index=False, float_format="%.8f")
    monthly.to_csv(output / "monthly.csv", index=False, float_format="%.8f")
    trades.to_csv(output / "trades.csv", index=False, float_format="%.8f")
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False,
                                                   indent=2) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=BASE)
    parser.add_argument("--output", type=Path, default=BASE / "report")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--expected-source-days", type=int, default=144)
    parser.add_argument("--expected-test-days", type=int, default=123)
    parser.add_argument("--backtest-dir", type=Path)
    parser.add_argument("--candidate-source", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.base, args.output, args.env_file,
                         args.expected_source_days, args.expected_test_days,
                         args.backtest_dir, args.candidate_source),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
