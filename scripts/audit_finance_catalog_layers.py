#!/usr/bin/env python3
"""Capture deterministic before/after catalog disclosure without model/data calls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


CAPTURE = r'''
import json
from pathlib import Path
from src.services.finance_data_tool_catalog_service import FinanceDataToolCatalogService
s = FinanceDataToolCatalogService()
raw = s.load_raw_catalog()
packs = {}
for subject, view, op in [
    ("stock", "quote", "query"), ("stock", "quote", "window"),
    ("stock", "report_metric", "aggregate"),
    ("plate", "constitution", "aggregate"),
    ("plate", "constitution", "query"),
]:
    packs[f"{subject}.{view}/{op}"] = s.get_model_dataview(subject, view, op)
views = [v for sub in raw["subjects"].values() for name, v in sub.items() if not name.startswith("_")]
contracts = {}
for view in views:
    for api in view["api"]:
        cls = raw["api_class_patterns"][api["api_class"]]
        contracts[api["api_name"]] = {
            "class": api["api_class"],
            "fields": sorted(view["fields"]),
            "args": {k: sorted(v.split("(", 1)[0] for v in values) for k, values in cls["args"].items()},
            "methods": cls.get("methods", []),
            "kd": view.get("kd"),
            "computed": view.get("computed"),
            "aggregate_fields": view.get("aggregate_fields"),
        }
files = ["src/scenarios/financial_qa/" + n for n in ("system.md", "data_query.md", "finance_api_protocol.md", "dsh_system.md")]
print(json.dumps({
    "catalog_revision": s.catalog_revision(), "subject_count": len(raw["subjects"]),
    "view_count": len(views), "api_count": len(contracts), "template_count": len(raw["api_class_patterns"]),
    "packs": packs, "contracts": contracts,
    "pack_characters": {k:len(json.dumps(v, ensure_ascii=False,separators=(",", ":"))) for k,v in packs.items()},
    "prompt_characters": {f:len(Path(f).read_text()) for f in files},
}, ensure_ascii=False))
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    captures = {
        name: json.loads(subprocess.check_output([sys.executable, "-c", CAPTURE], cwd=root, text=True))
        for name, root in (("before", args.before), ("after", args.after))
    }
    captures["execution_contracts_unchanged"] = captures["before"]["contracts"] == captures["after"]["contracts"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(captures, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "execution_contracts_unchanged": captures["execution_contracts_unchanged"],
        **{name: {k: v for k, v in value.items() if k not in {"packs", "contracts"}} for name, value in captures.items() if isinstance(value, dict)},
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
