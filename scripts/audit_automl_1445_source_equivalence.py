"""Check native 15-minute signal facts against exact one-minute bars."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.build_automl_db_1445_exact0940 import OUT


DATES = ("2026-09-02", "2026-09-15", "2026-10-09")
REPORT = Path("docs/stock_automl_runs/20261012_1445_exact0940/source_audit.json")


def run(dates: tuple[str, ...] = DATES) -> dict:
    checks = []
    with db_connection(Path(".env")) as db:
        for day in dates:
            native = pd.read_csv(OUT / "days" / f"{day}.csv", dtype={"symbol6": str})
            if not native.signal_bar_type.eq("15m").all():
                raise ValueError(f"Not a native 15m day: {day}")
            pieces = []
            codes = native.symbol6.tolist()
            for start in range(0, len(codes), 500):
                part = codes[start:start+500]
                slots = ",".join(["%s"] * len(part))
                rows = query(db, "SELECT stk_code AS symbol6,COUNT(*) AS bars,"
                             "COUNT(DISTINCT bar_end_time) AS distinct_bars,"
                             "SUM(volume)*100 AS one_minute_volume_shares,"
                             "MIN(low_price) AS one_minute_low,"
                             "MAX(CASE WHEN bar_end_time=%s THEN latest_price END) "
                             "AS one_minute_signal_price "
                             "FROM aiia_stock_realtime_minute_snapshot_full "
                             "WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1 "
                             "AND bar_end_time BETWEEN %s AND %s "
                             f"AND stk_code IN ({slots}) GROUP BY stk_code",
                             (f"{day} 14:45:00", day, f"{day} 09:31:00",
                              f"{day} 14:45:00", *part))
                pieces.append(rows)
            minute = pd.concat(pieces, ignore_index=True)
            minute.symbol6 = minute.symbol6.astype(str).str.zfill(6)
            paired = native.merge(minute, on="symbol6", validate="one_to_one")
            if len(paired) != len(native):
                raise ValueError(f"One-minute key coverage differs on {day}")
            for field in ("bars", "distinct_bars", "one_minute_volume_shares",
                          "one_minute_low", "one_minute_signal_price"):
                paired[field] = pd.to_numeric(paired[field], errors="raise")
            differences = {
                "price": float(np.max(np.abs(paired.signal_price -
                                              paired.one_minute_signal_price))),
                "volume_shares": float(np.max(np.abs(paired.minute_volume_shares -
                                                      paired.one_minute_volume_shares))),
                "low": float(np.max(np.abs(paired.min_low_so_far - paired.one_minute_low))),
            }
            checks.append({"day": day, "native_candidates": len(native),
                           "minute_complete": int((paired.bars.eq(225) &
                                                     paired.distinct_bars.eq(225)).sum()),
                           "max_abs_difference": differences})
            print(day, checks[-1], flush=True)
    result = {"signal_cutoff": "14:45", "checks": checks}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n")
    return result


if __name__ == "__main__":
    run()
