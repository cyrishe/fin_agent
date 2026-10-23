"""Read back V1/V2 definition identities and daily-run coverage from KingdomAI."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.automl_b2 import digest
from scripts.automl_b2_v2 import BASE_DEFINITION_SHA256, STRATEGY_ID
from scripts.register_automl_b2_v2 import payload
from scripts.benchmark_automl_1440_inference import db_connection


def audit(env_file: Path, user_id: str) -> dict:
    frozen = payload()
    with db_connection(env_file) as db:
        with db.cursor() as cursor:
            cursor.execute(
                "SELECT strategy_id,title,summary,training_method,selection_formula,"
                "execution_formula,formula_json,definition_sha256 FROM automl_strategy_definition "
                "WHERE user_id=%s AND strategy_id IN (%s,%s) ORDER BY strategy_id",
                (user_id, "B2", STRATEGY_ID),
            )
            definitions = {row["strategy_id"]: row
                           for row in cursor.fetchall()}
            cursor.execute(
                "SELECT model_version,COUNT(*) AS run_days,MIN(signal_date) AS first_day,"
                "MAX(signal_date) AS last_day FROM strategy_daily_run "
                "WHERE user_id=%s AND strategy_name=%s "
                "GROUP BY model_version ORDER BY model_version",
                (user_id, "涨停冷静"),
            )
            runs = [{"model_version": row["model_version"], "run_days": int(row["run_days"]),
                     "first_day": str(row["first_day"]), "last_day": str(row["last_day"])}
                    for row in cursor.fetchall()]
            cursor.execute(
                "SELECT COUNT(*) AS final_candidates FROM strategy_execution "
                "WHERE user_id=%s AND strategy_name=%s AND model_version=%s "
                "AND signal_date BETWEEN %s AND %s AND actionable=1",
                (user_id, "涨停冷静", "v1.0", "2026-09-01", "2026-09-30"),
            )
            september = int(cursor.fetchone()["final_candidates"])
        db.rollback()
    def row_digest(row: dict | None) -> str | None:
        if row is None:
            return None
        formula = row["formula_json"]
        value = {key: row[key] for key in (
            "title", "summary", "training_method", "selection_formula", "execution_formula")}
        value["formula"] = json.loads(formula) if isinstance(formula, str) else formula
        return digest(value)

    base, v2 = definitions.get("B2"), definitions.get(STRATEGY_ID)
    return {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "user_id": user_id,
            "base_definition_sha256": base["definition_sha256"] if base else None,
            "base_content_sha256": row_digest(base),
            "base_definition_matches": bool(base and base["definition_sha256"] == row_digest(base) == BASE_DEFINITION_SHA256),
            "v2_strategy_id": STRATEGY_ID,
            "v2_expected_definition_sha256": frozen["definition_sha256"],
            "v2_registered_definition_sha256": v2["definition_sha256"] if v2 else None,
            "v2_content_sha256": row_digest(v2),
            "v2_definition_matches": bool(v2 and v2["definition_sha256"] == row_digest(v2) == frozen["definition_sha256"]),
            "daily_run_versions": runs,
            "september_2026_v1_final_D0_candidates": september}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--user-id", default="imac")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    result = audit(args.env_file, args.user_id)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    print(text, end="")


if __name__ == "__main__":
    main()
