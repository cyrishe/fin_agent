"""Catalog adapter for bounded historical indicators and numeric K-line scans."""
import ast
from datetime import date
import re

import pymysql

from src.experiments.staged_data_protocol.phase2 import python_filter as pf
from src.services.stock_technical_history import read_history


def execute_history_api(*, patterns=False, args, outputs):
    api = "stock.kline_patterns.query" if patterns else "stock.technical_series.query"
    selected = outputs or (["code", "name", "signal_date", "evidence", "evolution"] if patterns
                           else ["code", "trade_date", "close", "ma20", "volume_ratio5"])
    mapping = []
    for item in selected:
        parts = re.split(r"\s+as\s+", item.strip(), flags=re.IGNORECASE)
        field = parts[0].rsplit(".", 1)[-1]
        mapping.append((field, parts[-1] if len(parts) > 1 else field))
    columns = [alias for _, alias in mapping]
    try:
        codes = args.get("codes") or []
        if isinstance(codes, str):
            codes = ast.literal_eval(codes)
        result = read_history(codes, count=args.get("count", 10 if patterns else 20),
            as_of=date.fromisoformat(args["as_of"]) if args.get("as_of") else None, patterns=patterns)
        rows = [row for row in result["rows"] if pf.evaluate(pf.condition(args), row)]
        for term in reversed(str(args.get("order") or "").split(",")):
            if not term.strip():
                continue
            parts = term.split()
            if len(parts) > 2 or len(parts) == 2 and parts[1].lower() not in {"asc", "desc"}:
                raise ValueError("order requires field asc/desc")
            field = parts[0]
            present = [r for r in rows if r[field] is not None]
            rows = sorted(present, key=lambda r: r[field], reverse=len(parts) == 2 and parts[1].lower() == "desc") + [r for r in rows if r[field] is None]
        limit = args.get("limit", -1)
        if type(limit) is not int or limit == 0 or limit < -1:
            raise ValueError("limit must be -1 or a positive integer")
        if limit != -1:
            rows = rows[:limit]
        return {"status": "ok", "api": api, "columns": columns,
            "rows": [{alias: row[field] for field, alias in mapping} for row in rows],
            "row_count": len(rows), "evidence": result["evidence"]}
    except pymysql.MySQLError:
        reason = "historical indicator source query failed; check the configured market database"
    except (ValueError, KeyError, TypeError, SyntaxError, RuntimeError, OSError) as exc:
        reason = str(exc)
    return {"status": "provider_error", "api": api, "columns": columns, "rows": [], "row_count": 0, "reason": reason}
