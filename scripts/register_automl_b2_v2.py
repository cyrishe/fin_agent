"""Preview or register the immutable B2 V2 definition in KingdomAI.

The default command only prints the frozen payload. --write-db is a production
database write and requires the separate human gate documented in AGENTS.md.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.automl_b2 import digest
from scripts.automl_b2_v2 import FROZEN_DEFINITION_SHA256, STRATEGY_ID, definition


def payload() -> dict:
    value = definition()
    actual = digest(value)
    if actual != FROZEN_DEFINITION_SHA256:
        raise ValueError("B2 V2 定义已偏离定版哈希；须另建策略版本")
    return {"strategy_id": STRATEGY_ID, "definition_sha256": actual, **value}


def register_definition(cursor, user_id: str, frozen: dict) -> str:
    cursor.execute(
        "SELECT definition_sha256 FROM automl_strategy_definition "
        "WHERE user_id=%s AND strategy_id=%s FOR UPDATE",
        (user_id, frozen["strategy_id"]),
    )
    existing = cursor.fetchone()
    if existing:
        if existing["definition_sha256"] != frozen["definition_sha256"]:
            raise ValueError("B2 V2 定义ID已存在且哈希不同；必须使用新版本ID")
        return "already_registered"
    cursor.execute(
        "INSERT INTO automl_strategy_definition "
        "(user_id,strategy_id,title,summary,training_method,selection_formula,"
        "execution_formula,formula_json,definition_sha256) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (user_id, frozen["strategy_id"], frozen["title"], frozen["summary"],
         frozen["training_method"], frozen["selection_formula"],
         frozen["execution_formula"],
         json.dumps(frozen["formula"], ensure_ascii=False, sort_keys=True),
         frozen["definition_sha256"]),
    )
    return "inserted"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-db", action="store_true")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--user-id", default="imac")
    args = parser.parse_args()
    frozen = payload()
    if not args.write_db:
        print(json.dumps({**frozen, "database_write": False}, ensure_ascii=False, indent=2))
        return
    from scripts.run_automl_1440_strategy import write_connection
    with write_connection(args.env_file) as db:
        try:
            with db.cursor() as cursor:
                action = register_definition(cursor, args.user_id, frozen)
            db.commit()
        except Exception:
            db.rollback()
            raise
    print(json.dumps({"user_id": args.user_id, "strategy_id": STRATEGY_ID,
                      "definition_sha256": frozen["definition_sha256"],
                      "database_action": action}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
