"""Observe next-morning ST sale conditions for previously selected Top1 stocks."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from scripts.automl_st_status import attach_st_status, load_st_intervals
from scripts.audit_automl_mk_1m_entry_limit import mark_entry_limit
from scripts.benchmark_automl_1440_inference import db_connection, query


def audit(picks: pd.DataFrame, candidates: pd.DataFrame,
          intervals: pd.DataFrame, daily: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    rows = picks.merge(candidates[["signal_date", "symbol6", "next_close_40",
                                    "next_volume5"]],
                       on=["signal_date", "symbol6"], validate="many_to_one")
    rows = rows.rename(columns={"signal_date": "original_signal_date",
                                "next_date": "signal_date",
                                "signal_price": "entry_1440"})
    rows = attach_st_status(rows, intervals)
    rows["signal_date"] = rows.signal_date.dt.strftime("%Y-%m-%d")
    rows = rows.merge(daily, on=["signal_date", "symbol6"],
                      how="left", validate="many_to_one")
    rows = mark_entry_limit(rows.assign(signal_price=rows.next_close_40))
    rows = rows.rename(columns={"signal_date": "next_date",
                                "original_signal_date": "signal_date",
                                "st_type": "next_st_type",
                                "entry_at_limit": "next_0940_at_limit"})
    rows["exit_0940_return"] = rows.next_close_40 / rows.entry_1440 - 1
    fields = ["arm", "signal_date", "next_date", "symbol6", "name", "entry_1440",
              "next_close_40", "next_volume5", "next_st_type", "preclose",
              "limit_price", "next_0940_at_limit", "entry_status_known",
              "exit_0940_return"]
    result = rows[fields]
    summary = {}
    for arm, group in result.groupby("arm"):
        st = group[group.next_st_type.isin(("S", "Y"))]
        summary[arm] = {
            "selected": len(group), "next_day_st": len(st),
            "next_day_st_at_0940_limit": int(st.next_0940_at_limit.sum()),
            "next_day_st_positive_first5_volume": int(st.next_volume5.gt(0).sum()),
        }
    return result, summary


def run(picks_path: Path, candidates_path: Path, output: Path) -> dict:
    picks = pd.read_csv(picks_path, dtype={"symbol6": str, "signal_date": str,
                                           "next_date": str})
    candidates = pd.read_csv(candidates_path,
                             usecols=["signal_date", "symbol6", "next_close_40",
                                      "next_volume5"],
                             dtype={"symbol6": str, "signal_date": str})
    with db_connection(Path(".env")) as db:
        intervals = load_st_intervals(db)
        daily = query(db, "SELECT trade_date AS signal_date, "
                      "LEFT(stk_code,6) AS symbol6, preclose "
                      "FROM kcrp_stock_price WHERE trade_date BETWEEN %s AND %s",
                      (picks.next_date.min(), picks.next_date.max()))
    daily["signal_date"] = pd.to_datetime(daily.signal_date).dt.strftime("%Y-%m-%d")
    daily["preclose"] = pd.to_numeric(daily.preclose)
    result, summary = audit(picks, candidates, intervals, daily)
    output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output, index=False)
    return summary


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--picks", type=Path, required=True)
    p.add_argument("--candidates", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(run(a.picks, a.candidates, a.output),
                     ensure_ascii=False, indent=2))
