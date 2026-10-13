"""Read-only audit of whether concept plate data support historical replay."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.benchmark_automl_1440_inference import db_connection, query


OUT = Path("outputs/stock_automl/limit_rebound_2y_r1/plate_audit.json")


def run(path: Path = OUT) -> dict:
    with db_connection() as conn:
        members = query(conn, "SELECT COUNT(*) n, COUNT(DISTINCT plate_code) plates,"
                        "COUNT(DISTINCT stk_code) stocks, MIN(create_time) first_created,"
                        "MAX(create_time) last_created FROM kcrp_yp_plate_member")
        prices = query(conn, "SELECT YEAR(trade_date) y, MONTH(trade_date) m,"
                       "COUNT(*) price_rows, COUNT(DISTINCT trade_date) days,"
                       "COUNT(DISTINCT plate_code) plates, MIN(create_time) first_created,"
                       "MAX(create_time) last_created FROM kcrp_yp_plate_price "
                       "GROUP BY YEAR(trade_date), MONTH(trade_date) ORDER BY y,m")
        conn.rollback()
    result = {"membership": {key: str(value) if isinstance(value, (datetime,)) else value
                              for key, value in members.iloc[0].to_dict().items()},
              "price_months": [{key: str(value) if isinstance(value, (datetime,)) else value
                                for key, value in row.items()}
                               for row in prices.to_dict("records")],
              "conclusion": "Current plate membership was created October 2026; no historical membership vintages. Plate price has no 2024-2025 rows and is absent in August 2026. Cannot use it for confirmatory two-year stock-level backtest.",
              "generated_at_utc": datetime.now(timezone.utc).isoformat()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    run()
