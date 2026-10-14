"""Audit archived Top1 minute lows against the same-day cumulative VWAP."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.audit_automl_minute_api_backfill import request_bars
from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.experiment_automl_intraday_vwap import EXPECTED_TIMES, MK, OUT
from scripts.mk_minute_archive import day_file


def counts(bars: pd.DataFrame, day: str, symbol: str, multiplier: int) -> dict:
    bars = bars.copy()
    bars["clock"] = bars.clock.astype(str).str[-8:]
    bars = bars[bars.clock.isin(EXPECTED_TIMES)].sort_values("clock")
    if len(bars) != 220 or bars.clock.nunique() != 220 or set(bars.clock) != EXPECTED_TIMES:
        raise ValueError(f"Incomplete minute bars: {day} {symbol}: {len(bars)}")
    for col in ("low", "close", "volume", "amount"):
        bars[col] = pd.to_numeric(bars[col], errors="raise")
    if (bars[["low", "close"]] <= 0).any().any() or (bars[["volume", "amount"]] < 0).any().any():
        raise ValueError(f"Invalid minute prices or trades: {day} {symbol}")
    shares = bars.volume.to_numpy(dtype=float) * multiplier
    cum_shares = np.cumsum(shares)
    cum_amount = np.cumsum(bars.amount.to_numpy(dtype=float))
    valid = cum_shares > 0
    close_above = valid & (bars.close.to_numpy(dtype=float) * cum_shares > cum_amount)
    low_above = valid & (bars.low.to_numpy(dtype=float) * cum_shares > cum_amount)
    return {"signal_date": day, "symbol6": symbol,
            "low_above_minutes": int(low_above.sum()),
            "low_above_ratio": float(low_above.mean()),
            "low_all_above": int(low_above.all()),
            "close_above_minutes_rechecked": int(close_above.sum())}


def archive_bars(day: str, symbol: str) -> pd.DataFrame:
    pieces = []
    for chunk in pd.read_csv(day_file(MK, day),
                             usecols=["time", "symbol", "low", "close", "volume", "turnover"],
                             dtype={"symbol": str}, chunksize=300_000):
        piece = chunk[chunk.symbol.str[:6].eq(symbol)]
        if not piece.empty:
            pieces.append(piece)
    if not pieces:
        raise ValueError(f"Archive stock absent: {day} {symbol}")
    bars = pd.concat(pieces, ignore_index=True)
    bars["clock"] = bars.time.str[-8:]
    return bars.rename(columns={"turnover": "amount"})


def database_bars(db, day: str, symbol: str) -> pd.DataFrame:
    bars = query(db, "SELECT bar_end_time AS clock,low_price AS low,"
                 "latest_price AS close,volume,amount,is_finalized,is_fallback,"
                 "source_snapshot_time FROM aiia_stock_realtime_minute_snapshot_full "
                 "WHERE trade_date=%s AND stk_code=%s AND kline_type='1m' AND period_minutes=1 "
                 "AND bar_end_time BETWEEN %s AND %s ORDER BY bar_end_time",
                 (day, symbol, f"{day} 09:31:00", f"{day} 14:40:00"))
    if len(bars) != 220 or not ((bars.is_finalized == 1) & (bars.is_fallback == 0) &
                                (bars.source_snapshot_time == bars.clock)).all():
        raise ValueError(f"Inexact database minute bars: {day} {symbol}")
    return bars


def api_bars(day: str, symbol: str) -> pd.DataFrame:
    rows = request_bars(symbol, 0, 300)
    date = int(day.replace("-", ""))
    records = []
    for row in rows:
        stamp = row["sttDateTime"]
        if int(stamp["iDate"]) != date:
            continue
        minute = int(stamp["shtTime"])
        records.append({"clock": f"{minute//60:02d}:{minute%60:02d}:00",
                        "low": row["fLow"], "close": row["fClose"],
                        "volume": row["lVolume"], "amount": row["fAmount"]})
    return pd.DataFrame(records)


def run(output: Path = OUT, include_live: bool = True) -> dict:
    fixed = pd.read_csv(output / "existing_top1_vwap.csv",
                        dtype={"signal_date": str, "symbol6": str})
    results = []
    with db_connection(Path(".env")) as db:
        for row in fixed.itertuples(index=False):
            early = row.signal_date < "2026-08-01"
            bars = (archive_bars(row.signal_date, row.symbol6) if early else
                    database_bars(db, row.signal_date, row.symbol6))
            result = counts(bars, row.signal_date, row.symbol6, 1 if early else 100)
            if result["close_above_minutes_rechecked"] != row.vwap_above_minutes:
                raise ValueError(f"Close/VWAP disagrees with existing factor: {row.signal_date} {row.symbol6}")
            result["source"] = "mk_archive" if early else "minute_db"
            results.append(result)
            print("low-vwap", row.signal_date, row.symbol6, result["low_above_minutes"], flush=True)
    historical = pd.DataFrame(results)
    if len(historical) != len(fixed) or historical.duplicated(["signal_date", "symbol6"]).any():
        raise ValueError("Historical Top1 low/VWAP coverage mismatch")
    historical.to_csv(output / "old_top1_low_vwap.csv", index=False)
    if include_live:
        live_file = output / "live_2026-10-12_top2_vwap.csv"
        if live_file.exists():
            live = pd.read_csv(live_file, dtype={"signal_date": str, "symbol6": str})
            additions = []
            for row in live.itertuples(index=False):
                result = counts(api_bars(row.signal_date, row.symbol6),
                                row.signal_date, row.symbol6, 100)
                if result["close_above_minutes_rechecked"] != row.vwap_above_minutes:
                    raise ValueError(f"Live close/VWAP mismatch: {row.symbol6}")
                result["source"] = "minute_api"
                result["selection_rank"] = row.selection_rank
                additions.append(result)
            pd.DataFrame(additions).to_csv(output / "live_2026-10-12_top2_low_vwap.csv", index=False)
    joined = fixed.merge(historical, on=["signal_date", "symbol6"], validate="one_to_one")
    joined["low_ratio_band"] = pd.cut(joined.low_above_ratio,
                                       bins=[-1, .25, .5, .75, 1],
                                       labels=["≤25%", "25–50%", "50–75%", ">75%"])
    bands = joined.groupby("low_ratio_band", observed=False).agg(
        picks=("symbol6", "size"), sale_ge3=("actual_0940_return", lambda x: int(x.ge(.03).sum())),
        sale_negative=("actual_0940_return", lambda x: int(x.lt(0).sum())),
        mean_sale_pct=("actual_0940_return", lambda x: float(x.mean()*100))).reset_index()
    bands.to_csv(output / "old_top1_low_vwap_bands.csv", index=False)
    summary = {"historical_days": len(historical),
               "low_all_above_days": int(joined.low_all_above.sum()),
               "low_ratio_min": float(joined.low_above_ratio.min()),
               "low_ratio_median": float(joined.low_above_ratio.median()),
               "low_ratio_max": float(joined.low_above_ratio.max()),
               "bands": bands.to_dict("records")}
    (output / "old_top1_low_vwap_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    run()
