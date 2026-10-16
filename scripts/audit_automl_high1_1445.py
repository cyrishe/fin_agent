"""Audit 14:45 minute availability for the >1% model's later-period picks.

Final daily close and limit flags are used only to verify whether the timely
14:45 minute price was already at the eventual upper limit price.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from scripts.experiment_automl_next_open_1445 import fetch_frame, minute_connection
from src.quant_research.automl.data import kingdom_connection, query


ROOT = Path("outputs/stock_automl/next_open_targets")


def main():
    load_dotenv(".env")
    frame = pd.read_csv(ROOT / "high1_consistent_selected_diagnostics.csv", dtype={"symbol": str})
    frame = frame[frame.period.eq("later_check")].copy()
    frame.signal_date = pd.to_datetime(frame.signal_date).dt.strftime("%Y-%m-%d")
    frame["symbol6"] = frame.symbol.str[:6]
    unique = frame[["signal_date", "symbol", "symbol6"]].drop_duplicates()
    minutes, daily = [], []
    with minute_connection() as conn:
        for day, part in unique.groupby("signal_date"):
            marks = ",".join(["%s"] * len(part))
            one = fetch_frame(conn, f"""
                SELECT trade_date AS signal_date, stk_code AS symbol6, latest_price,
                       source_snapshot_time, is_finalized, is_fallback
                FROM aiia_stock_realtime_minute_snapshot_full
                WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
                  AND bar_end_time=%s AND stk_code IN ({marks})
            """, (day, f"{day} 14:45:00", *part.symbol6.tolist()))
            if not one.empty:
                minutes.append(one)
    if not minutes:
        raise ValueError("no later-period 14:45 minute rows")
    minute = pd.concat(minutes, ignore_index=True)
    minute.signal_date = pd.to_datetime(minute.signal_date).dt.strftime("%Y-%m-%d")
    if minute.duplicated(["signal_date", "symbol6"]).any():
        raise ValueError("duplicate minute bar")
    with kingdom_connection() as conn:
        for day, part in unique.groupby("signal_date"):
            marks = ",".join(["%s"] * len(part))
            daily.extend(query(conn, f"""
                SELECT trade_date AS signal_date, stk_code AS symbol,
                       close AS final_close, is_limit_price AS final_limit_flag
                FROM kcrp_stock_price
                WHERE trade_date=%s AND stk_code IN ({marks})
            """, (day, *part.symbol.tolist())))
    daily = pd.DataFrame(daily)
    daily.signal_date = pd.to_datetime(daily.signal_date).dt.strftime("%Y-%m-%d")
    joined = frame.merge(daily, on=["signal_date", "symbol"], validate="many_to_one")
    joined = joined.merge(minute, on=["signal_date", "symbol6"],
                          how="left", validate="many_to_one")
    if len(joined) != len(frame):
        raise ValueError("later signal count changed during minute audit")
    for col in ("latest_price", "final_close", "final_limit_flag", "is_finalized", "is_fallback"):
        joined[col] = pd.to_numeric(joined[col], errors="coerce")
    cutoff = pd.to_datetime(joined.signal_date) + pd.Timedelta(hours=14, minutes=45)
    joined["usable_1445"] = (joined.is_finalized.eq(1) & joined.is_fallback.eq(0) &
                             pd.to_datetime(joined.source_snapshot_time).le(cutoff))
    joined["at_final_limit_price_1445"] = (joined.usable_1445 &
        joined.final_limit_flag.eq(1) & joined.latest_price.eq(joined.final_close))
    rows = []
    for (scope, model), part in joined.groupby(["scope", "model"]):
        usable = part[part.usable_1445]
        at_limit = usable[usable.at_final_limit_price_1445]
        other = usable[~usable.at_final_limit_price_1445]
        rows.append({"scope": scope, "model": model, "signals": len(part),
                     "high1": int(part.high1.sum()), "usable_1445": len(usable),
                     "at_final_limit_price_1445": len(at_limit),
                     "high1_at_final_limit_price_1445": int(at_limit.high1.sum()),
                     "other_usable": len(other), "high1_other_usable": int(other.high1.sum()),
                     "missing_or_unusable": int((~part.usable_1445).sum()),
                     "high1_missing_or_unusable": int(part.loc[~part.usable_1445, "high1"].sum())})
    output = {"note": "Later period only; final daily flag/close are ex-post checks, no order-book fill proof.",
              "distinct_stock_dates": len(unique), "results": rows}
    (ROOT / "high1_consistent_minute_audit.json").write_text(json.dumps(output, ensure_ascii=False, indent=2),
                                                    encoding="utf-8")
    joined.to_csv(ROOT / "high1_consistent_minute_audit_rows.csv", index=False, encoding="utf-8-sig")
    print(json.dumps(output, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
