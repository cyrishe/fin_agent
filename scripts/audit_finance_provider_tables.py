"""Read-only inventory of physical tables referenced by the finance providers."""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from scripts.sync_shareholder_table import connection


def audit():
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    refs = {}
    for path in (root / "src/experiments/staged_data_protocol/phase2").glob("*provider.py"):
        if path.name.startswith("."):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            for table in re.findall(r"\b(?:kcrp_\w+|aiia_\w+|reports|metric_fact|metric_def|hot_event_base_info|hot_event_state|hot_event_member)\b", node.value):
                refs.setdefault(table, set()).add(path.name)
    rows = []
    for schema in ("kingdomai", "stock_agent"):
        with connection("PLATFORM_DB_URL", ("47.94.1.2", 3312, schema), schema) as db:
            with db.cursor() as c:
                c.execute("SELECT TABLE_NAME,TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE()")
                tables = dict(c.fetchall())
                for table, providers in sorted(refs.items()):
                    if ("stock_agent" if table.startswith("hot_event_") else "kingdomai") != schema:
                        continue
                    row = {"database": schema, "table": table, "providers": sorted(providers),
                           "exists": table in tables, "estimated_rows": tables.get(table)}
                    if row["exists"]:
                        c.execute(f"SHOW COLUMNS FROM `{table}`")
                        row["columns"] = [r[0] for r in c.fetchall()]
                        c.execute(f"SELECT 1 FROM `{table}` LIMIT 1")
                        row["has_data"] = c.fetchone() is not None
                    rows.append(row)
    return {"scope": "Current phase2 Python providers: literal physical tables, existence, columns and SELECT access; not a freshness or semantic correctness audit.",
            "table_count": len(rows), "missing": [r["table"] for r in rows if not r["exists"]], "tables": rows}


if __name__ == "__main__":
    result = audit()
    if len(sys.argv) > 1:
        output = Path(sys.argv[1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "tables"}, ensure_ascii=False))
