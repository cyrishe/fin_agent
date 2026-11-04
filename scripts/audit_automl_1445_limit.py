"""Audit 14:45 minute availability and limit-price exposure of frozen signals.

The current day's final limit flag and close are ex-post audit facts, never
model inputs. Minute rows must have a completed, timely, non-fallback 14:45 bar.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from scripts.experiment_automl_next_open_1445 import fetch_frame, minute_connection
from src.quant_research.automl.data import kingdom_connection, query


SELECTED = Path("outputs/stock_automl/next_open_targets/selected_signals.csv")
OUTPUT = Path("outputs/stock_automl/next_open_targets/minute_limit_audit.json")


def main():
    load_dotenv(".env")
    selected = pd.read_csv(SELECTED, dtype={"symbol": str})
    selected = selected[selected.period.eq("后续观察期")].copy()
    selected["symbol6"] = selected.symbol.str[:6]
    minutes = []
    with minute_connection() as conn:
        for day, part in selected.groupby("decision_date"):
            marks = ",".join(["%s"] * len(part))
            frame = fetch_frame(conn, f"""
                SELECT trade_date AS decision_date, stk_code AS symbol6,
                       latest_price, source_snapshot_time, is_fallback, is_finalized
                FROM aiia_stock_realtime_minute_snapshot_full
                WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
                  AND bar_end_time=%s AND stk_code IN ({marks})
            """, (day, f"{day} 14:45:00", *part.symbol6.tolist()))
            if not frame.empty:
                minutes.append(frame)
    minute = pd.concat(minutes, ignore_index=True)
    minute.decision_date = pd.to_datetime(minute.decision_date).dt.strftime("%Y-%m-%d")
    if minute.duplicated(["decision_date", "symbol6"]).any():
        raise ValueError("duplicate 14:45 minute rows")
    daily_rows = []
    with kingdom_connection() as conn:
        for day, part in selected.groupby("decision_date"):
            marks = ",".join(["%s"] * len(part))
            daily_rows.extend(query(conn, f"""
                SELECT trade_date AS decision_date, stk_code AS symbol,
                       close AS final_close, is_limit_price AS final_limit_flag
                FROM kcrp_stock_price
                WHERE trade_date=%s AND stk_code IN ({marks})
            """, (day, *part.symbol.tolist())))
    daily = pd.DataFrame(daily_rows)
    daily.decision_date = pd.to_datetime(daily.decision_date).dt.strftime("%Y-%m-%d")
    result = selected.merge(daily, on=["decision_date", "symbol"],
                            validate="one_to_one").merge(
        minute, on=["decision_date", "symbol6"], how="left", validate="one_to_one")
    if len(result) != len(selected):
        raise ValueError("daily audit rows do not reconcile to frozen selections")
    for col in ("latest_price", "final_close", "final_limit_flag", "is_fallback", "is_finalized"):
        result[col] = pd.to_numeric(result[col], errors="coerce")
    cutoff = pd.to_datetime(result.decision_date) + pd.Timedelta(hours=14, minutes=45)
    result["usable_1445"] = (result.is_finalized.eq(1) & result.is_fallback.eq(0) &
                             pd.to_datetime(result.source_snapshot_time).le(cutoff))
    result["at_final_limit_price_1445"] = (result.usable_1445 &
        result.final_limit_flag.eq(1) & result.latest_price.eq(result.final_close))
    usable = result[result.usable_1445]
    at_limit = usable[usable.at_final_limit_price_1445]
    other = usable[~usable.at_final_limit_price_1445]
    audit = {"frozen_signals": len(result), "usable_1445_bars": len(usable),
             "missing_or_unusable_1445_bars": int((~result.usable_1445).sum()),
             "at_final_limit_price_1445": len(at_limit),
             "high2_hits_at_final_limit_price_1445": int(at_limit.high_open_gt_2pct.sum()),
             "other_usable_signals": len(other),
             "high2_hits_other_usable": int(other.high_open_gt_2pct.sum()),
             "high2_hits_missing_or_unusable": int(result.loc[
                 ~result.usable_1445, "high_open_gt_2pct"].sum()),
             "note": "Final daily close/limit flag are ex-post verification only; order-book fills unknown."}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
