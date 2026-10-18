"""Read-only reconciliation of the B2 replay with KingdomAI's strategy registry.

No production rows are changed.  This is a dated evidence producer, not a
runtime trading or database migration entry point.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.benchmark_automl_1440_inference import db_connection


DEFAULT_REPLAY = Path("outputs/stock_automl/b2_independent_recheck_20261018/continuous/replay.json")
DEFAULT_OUTPUT = Path("docs/stock_automl_runs/20261018_b2_strict_recheck/db_registry_readback.json")


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def decoded(value):
    return json.loads(value) if isinstance(value, str) else value or {}


def reconcile(replay: dict, definitions: list[dict], runs: list[dict], rows: list[dict], archives: list[dict]) -> dict:
    candidates = {(x["d0_date"], x["d0_rank"]): x for x in replay["candidates"]}
    trades = {(x["d0_date"], x["d0_rank"]): x for x in replay["trades"]}
    assert len(candidates) == len(replay["candidates"])
    assert len(definitions) == 1 and definitions[0]["strategy_id"] == "B2"
    overlap = [x for x in rows if (str(x["signal_date"]), x["selection_rank"]) in candidates]
    mismatches = []
    score_max = buy_max = sell_max = net_max = 0.0
    matched_buys = 0
    for row in overlap:
        key = (str(row["signal_date"]), row["selection_rank"])
        candidate = candidates[key]
        detail = decoded(row["details_json"])
        if row["stock_code"] != candidate["symbol"] or bool(row["actionable"]) != bool(candidate["actionable"]):
            mismatches.append({"key": key, "reason": "stock_or_action"})
            continue
        if "score" not in detail:
            mismatches.append({"key": key, "reason": "missing_score"})
            continue
        score_max = max(score_max, abs(float(detail["score"]) - candidate["score"]))
        if (row["buy_price"] is None) != (candidate["buy"] is None):
            mismatches.append({"key": key, "reason": "buy_presence"})
            continue
        if row["buy_price"] is not None:
            matched_buys += 1
            buy_max = max(buy_max, abs(float(row["buy_price"]) - candidate["buy"]))
            trade = trades.get(key)
            if trade is None:
                mismatches.append({"key": key, "reason": "missing_trade"})
                continue
            if row["sell_price"] is not None and trade["sell"] is not None:
                sell_max = max(sell_max, abs(float(row["sell_price"]) - trade["sell"]))
            if row["net_return_pct"] is not None and trade["return_"] is not None:
                net_max = max(net_max, abs(float(row["net_return_pct"]) - trade["return_"] * 100))
    overlap_days = {str(x["signal_date"]) for x in overlap}
    run_by_day = {str(x["signal_date"]): x for x in runs}
    for day in overlap_days:
        if day not in run_by_day or run_by_day[day]["selection_count"] != 3:
            mismatches.append({"key": day, "reason": "missing_or_incomplete_run"})
    expected_overlap = [x for x in replay["candidates"] if x["d0_date"] in overlap_days]
    if len(overlap) != len(expected_overlap):
        mismatches.append({"reason": "overlap_count", "db": len(overlap), "replay": len(expected_overlap)})
    if score_max > 1e-12 or buy_max > 1e-8 or sell_max > 1e-8 or net_max > 1e-6:
        mismatches.append({"reason": "numeric_difference"})
    early = [x for x in replay["candidates"] if x["d0_date"] < min(overlap_days)]
    assert len(early) % 3 == 0
    return {
        "status": "PASS" if not mismatches else "FAIL",
        "checked_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "database": "kingdomai", "user_id": "imac", "strategy_id": "B2",
        "strategy_name": "涨停冷静", "model_version": "v1.0",
        "definition_sha256": definitions[0]["definition_sha256"],
        "existing_database": {"first_day": str(min(x["signal_date"] for x in runs)),
                              "last_day": str(max(x["signal_date"] for x in runs)),
                              "days": len(runs), "candidates": len(rows),
                              "buys": sum(bool(x["actionable"]) for x in rows)},
        "replay_overlap": {"first_day": min(overlap_days), "last_day": max(overlap_days),
                           "days": len(overlap_days), "candidates": len(overlap),
                           "buys": matched_buys,
                           "max_score_abs_diff": score_max, "max_buy_price_abs_diff": buy_max,
                           "max_sell_price_abs_diff": sell_max,
                           "max_net_return_pct_abs_diff": net_max},
        "before_database_range": {"first_day": min(x["d0_date"] for x in early),
                                  "last_day": max(x["d0_date"] for x in early),
                                  "days": len(early) // 3, "candidates": len(early),
                                  "buys": sum(bool(x["actionable"]) for x in early)},
        "training_archive": [{"experiment_id": x["experiment_id"],
                              "cloud_verified": x["cloud_verified_at"] is not None}
                             for x in archives],
        "mismatches": mismatches,
        "note": "Read-only snapshot. Existing live rows and historical replay rows were not updated.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay", type=Path, default=DEFAULT_REPLAY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    replay = json.loads(args.replay.read_text())
    with db_connection() as conn:
        with conn.cursor() as cursor:
            def select(sql, params=()):
                cursor.execute(sql, params)
                return list(cursor.fetchall())
            definitions = select("SELECT strategy_id,definition_sha256 FROM automl_strategy_definition WHERE user_id='imac' AND strategy_id='B2'")
            runs = select("SELECT signal_date,selection_count FROM strategy_daily_run WHERE user_id='imac' AND strategy_name='涨停冷静' AND model_version='v1.0' ORDER BY signal_date")
            rows = select("SELECT signal_date,selection_rank,stock_code,actionable,buy_price,sell_price,net_return_pct,details_json FROM strategy_execution WHERE user_id='imac' AND strategy_name='涨停冷静' AND model_version='v1.0' ORDER BY signal_date,selection_rank")
            archives = select("SELECT experiment_id,cloud_verified_at FROM automl_training_archive WHERE user_id='imac' AND experiment_id IN ('B2_20261015','B2_20261015_R2') ORDER BY experiment_id")
        conn.rollback()
    result = reconcile(replay, definitions, runs, rows, archives)
    result["replay_sha256"] = sha(args.replay)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: result[k] for k in ("status", "existing_database", "replay_overlap", "before_database_range", "mismatches")}, ensure_ascii=False))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
