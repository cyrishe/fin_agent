"""Explicit schema migration, EOD refresh and stored-result inspection."""
from __future__ import annotations

import argparse
from datetime import date, datetime
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=False)

from src.services.stock_indicator_daily_job import run_daily
from src.services.stock_indicator_store import apply_schema, market_connection, read_daily_indicators
from src.services.stock_indicator_minute import read_minute_indicators, read_minute_window
from src.services.stock_indicator_realtime import read_realtime_indicators
from src.services.stock_minute_signals import read_minute_signals


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("migrate", "refresh", "query", "minute", "minute-window", "signals", "realtime"))
    parser.add_argument("--date", default=datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat())
    parser.add_argument("--codes", default="")
    parser.add_argument("--expected-server-uuid", default="")
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--universe", default="cn_a_daily_source")
    parser.add_argument("--period", type=int, default=1)
    parser.add_argument("--as-of", help="minute cutoff in ISO format with timezone; defaults to now")
    parser.add_argument("--since", help="exclusive start of output window; same session, with timezone")
    parser.add_argument("--rules", help="comma-separated minute signal rules")
    parser.add_argument("--confirm-bars", type=int, default=3)
    parser.add_argument("--volume-ratio-threshold", type=float, default=1.5)
    parser.add_argument("--include-partial", action="store_true", help="include current period using its complete 1m prefix")
    parser.add_argument("--max-lag-seconds", type=int, help="reject stale indicator values; realtime defaults to 60s")
    parser.add_argument("--artifact-root", default=os.getenv("STOCK_INDICATOR_ARTIFACT_ROOT", str(ROOT / "data/stock_indicator_artifacts")))
    args = parser.parse_args()
    trade_date = date.fromisoformat(args.date)
    codes = [s.strip().upper() for s in args.codes.split(",") if s.strip()]
    if args.action in ("minute-window", "signals"):
        kwargs = dict(codes=codes, period=args.period, max_lag_seconds=args.max_lag_seconds,
            as_of=datetime.fromisoformat(args.as_of) if args.as_of else None,
            since=datetime.fromisoformat(args.since) if args.since else None)
        if args.action == "signals":
            result = read_minute_signals(**kwargs, rules=args.rules.split(",") if args.rules else None,
                confirm_bars=args.confirm_bars, volume_ratio_threshold=args.volume_ratio_threshold)
            result.pop("evaluated", None)
        else:
            result = read_minute_window(**kwargs)
    elif args.action == "realtime":
        if args.as_of:
            parser.error("realtime queries use current source time; historical --as-of belongs to minute queries")
        result = read_realtime_indicators(codes=codes,
            max_lag_seconds=60 if args.max_lag_seconds is None else args.max_lag_seconds)
    elif args.action == "minute":
        result = read_minute_indicators(codes=codes, period=args.period,
            include_partial=args.include_partial, max_lag_seconds=args.max_lag_seconds,
            as_of=datetime.fromisoformat(args.as_of) if args.as_of else None)
    elif args.action == "refresh":
        result = run_daily(trade_date=trade_date, artifact_root=Path(args.artifact_root), codes=codes,
                           progress=lambda row: print(json.dumps(row), flush=True))
    else:
        with market_connection() as connection:
            result = (apply_schema(connection, expected_server_uuid=args.expected_server_uuid)
                      if args.action == "migrate" else read_daily_indicators(connection,
                          as_of=trade_date, codes=codes, count=args.count, universe_key=args.universe))
    print(json.dumps(result, ensure_ascii=False, default=str), flush=True)


if __name__ == "__main__":
    main()
