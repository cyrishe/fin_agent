"""Refresh the 40 labeled signal days needed before today's 14:40 score."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.build_automl_1440_market_states import run as build_market_states
from scripts.run_automl_four_class_pipeline import OLD, SECOND, prepare_candidates, sha256
from scripts.verify_automl_four_class_live import run as verify_previous_decision


SHANGHAI = ZoneInfo("Asia/Shanghai")


def run(day: str, output: Path, env_file: Path) -> dict:
    if day != datetime.now(SHANGHAI).date().isoformat():
        raise ValueError("Daily refresh must target today's Shanghai date")
    conn = db_connection(env_file)
    try:
        calendar = query(conn, "SELECT DISTINCT trade_date FROM kcrp_stock_price "
                         "WHERE trade_date<%s ORDER BY trade_date DESC LIMIT 62",
                         (day,)).trade_date.astype(str).tolist()
    finally:
        conn.rollback()
        conn.close()
    if len(calendar) < 61:
        raise ValueError("Need 40 prior signal days plus at least 21 history days")
    output.mkdir(parents=True, exist_ok=True)
    raw_dir = output / "tail"
    subprocess.run([
        sys.executable, "-m", "scripts.build_automl_tail_standard",
        "--start", calendar[39], "--end", day,
        "--history-start", calendar[60], "--allow-open-end",
        "--env-file", str(env_file), "--output-dir", str(raw_dir),
        "--summary", str(output / "tail_summary.json"),
    ], check=True)
    raw = raw_dir / "candidates_1440.csv"
    prepared = output / "prepared_candidates.csv.gz"
    market = output / "market_states_1440.csv"
    rows = prepare_candidates(raw, OLD, SECOND, prepared, env_file)
    states = build_market_states(raw, market, env_file,
                                  start_day=calendar[39], end_day=calendar[0])
    if states.signal_date.max() != calendar[0] or rows.signal_date.max() > calendar[0]:
        raise ValueError("T-1 historical market state was not refreshed")
    latest = rows[rows.signal_date.eq(calendar[0])]
    if latest.empty and int(states.loc[states.signal_date.eq(calendar[0]),
                                   "candidate_count"].iloc[0]) > 0:
        raise ValueError("T-1 has market candidates but no prepared stock rows")
    if not latest.empty and not latest.second_high.notna().any():
        raise ValueError("T-1 candidates exist but today's 09:31-09:40 labels are unavailable")
    result = {"target_date": day, "latest_training_signal_date": calendar[0],
              "earliest_training_signal_date": calendar[39],
              "candidate_rows": len(rows), "labeled_rows": int(rows.second_high.notna().sum()),
              "market_state_days": len(states), "market_state_valid_days": int(
                  states.market_state_valid.sum()),
              "artifacts": {str(p): sha256(p) for p in (raw, prepared, market)}}
    previous_decision = output.parent / calendar[0] / f"{calendar[0]}_decision.json"
    if previous_decision.exists():
        result["previous_signal_verification"] = verify_previous_decision(
            previous_decision, prepared, output / f"{calendar[0]}_verified.csv")
    (output / "refresh_manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2)+"\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", default=datetime.now(SHANGHAI).date().isoformat())
    parser.add_argument("--output", type=Path)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    output = args.output or Path("outputs/stock_automl/four_class_live") / args.day
    print(json.dumps(run(args.day, output, args.env_file), ensure_ascii=False, indent=2))
