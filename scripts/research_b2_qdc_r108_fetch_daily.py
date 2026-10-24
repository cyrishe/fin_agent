"""Fetch point-in-time daily and turnover fields for the 2013-16 B2 extension.

The QDC OHLCV files do not contain official previous close or turnover. Keep
read-only Tushare responses in an isolated, resumable cache for model fitting.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from scripts.backfill_tushare_minutes import SlidingRateLimiter, Tushare, token_from_file

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/stock_automl/b2_qdc_r108_20261024"
RAW = OUT / "tushare_daily_pairs"
API = Tushare(token_from_file(ROOT / ".tushare"), SlidingRateLimiter(60))
FIELDS = {
    "daily": "ts_code,trade_date,pre_close,open,high,low,close,vol,amount",
    "daily_basic": "ts_code,trade_date,turnover_rate,total_share,float_share",
}


def trading_days(through: str) -> list[str]:
    path = OUT / "trade_cal.csv"
    if not path.exists():
        fields, rows = API.query("trade_cal", dict(exchange="SSE", start_date="20130101",
                                                 end_date="20161231"))
        pd.DataFrame(rows, columns=fields).to_csv(path, index=False)
    frame = pd.read_csv(path, dtype=str)
    days = sorted(frame.loc[frame.is_open.eq("1"), "cal_date"].tolist())
    assert days[0] == "20130104" and days[-1] == "20161230" and len(days) > 900
    if through > "20161231":
        extension = OUT / "trade_cal_201701.csv"
        if not extension.exists():
            fields, rows = API.query("trade_cal", dict(exchange="SSE", start_date="20170101",
                                                      end_date="20170131"))
            pd.DataFrame(rows, columns=fields).to_csv(extension, index=False)
        frame = pd.read_csv(extension, dtype=str)
        days += sorted(frame.loc[frame.is_open.eq("1"), "cal_date"].tolist())
    return days


def fetch(api_name: str, first: str, last: str) -> tuple[str, str, int]:
    target = RAW / f"{api_name}_{first}_{last}.csv.gz"
    if target.exists():
        return api_name, first, -1
    fields, rows = API.query(api_name, dict(start_date=first, end_date=last), FIELDS[api_name])
    assert set(FIELDS[api_name].split(",")) <= set(fields)
    assert len(rows) < 8000, (api_name, first, last, len(rows))
    result = pd.DataFrame(rows, columns=fields)
    assert set(result.trade_date) == {first, last}, (api_name, first, last, len(result))
    assert not result.duplicated(["ts_code", "trade_date"]).any()
    temporary = target.with_suffix(".partial")
    result.to_csv(temporary, index=False, compression="gzip")
    temporary.replace(target)
    return api_name, first, len(result)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--through", default="20161230", help="last trading date for a staged run")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)
    days = [day for day in trading_days(args.through) if day <= args.through]
    pairs = [(days[i], days[i + 1]) for i in range(0, len(days) - 1, 2)]
    if len(days) % 2:
        pairs.append((days[-1], days[-1]))
    jobs = [(name, first, last) for first, last in pairs for name in FIELDS]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for count, future in enumerate(as_completed([pool.submit(fetch, *job) for job in jobs]), 1):
            name, first, rows = future.result()
            if count % 40 == 0 or count == len(jobs):
                print(count, "/", len(jobs), name, first, "rows", rows, flush=True)
    print("complete", len(days), "days", len(jobs), "files", flush=True)


if __name__ == "__main__":
    main()
