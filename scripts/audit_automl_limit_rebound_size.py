"""Read-only check of traded-amount / turnover float-cap proxy."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.benchmark_automl_1440_inference import db_connection, query


OUT = Path("outputs/stock_automl/limit_rebound_2y_r1/size_audit.json")
DAYS = ("2024-10-08", "2025-04-08", "2026-07-01", "2026-09-01")


def run(path: Path = OUT) -> dict:
    results = []
    with db_connection() as conn:
        for day in DAYS:
            frame = query(conn, "SELECT p.amount,p.turn_ratio,v.float_mv "
                          "FROM kcrp_stock_price p JOIN kcrp_stock_pricevaluate v "
                          "ON p.trade_date=v.trade_date AND p.stk_code=v.stk_code "
                          "WHERE p.trade_date=%s AND p.amount>0 AND p.turn_ratio>0.01 "
                          "AND v.float_mv>0", (day,)).astype(float)
            relative_error = (frame.amount / (frame.turn_ratio / 100) /
                              frame.float_mv - 1).abs()
            results.append({"date": day, "matched_stocks": len(relative_error),
                            "median_absolute_error_pct": float(relative_error.median() * 100),
                            "p90_absolute_error_pct": float(relative_error.quantile(.9) * 100),
                            "within_10pct_fraction": float(relative_error.le(.1).mean())})
        conn.rollback()
    result = {"formula": "amount/(turn_ratio/100); approximates float_mv using traded average price",
              "days": results, "generated_at_utc": datetime.now(timezone.utc).isoformat()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    run()
