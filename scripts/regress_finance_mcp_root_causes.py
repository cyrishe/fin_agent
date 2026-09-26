"""Replay original pilot questions through the real MCP ASGI transport and model.

Run in an isolated checkout with deployment .env. Does not bind a public port,
restart production or charge evaluation requests to production usage statistics.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import secrets
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(source: Path, output: Path, ids: list[str]):
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
    os.environ.update(FINANCE_API_ALLOWED_HOSTS="testserver,127.0.0.1:*,localhost:*",
                      FINANCE_API_ALLOWED_ORIGINS="", FINANCE_API_ROOT_PATH="",
                      FINANCE_DSH_PREWARM_ON_START="0", FINANCE_STATUS_ENABLED="0",
                      FINANCE_DSH_WORKERS="2")
    from fastapi.testclient import TestClient
    from src.finance_api.app import create_app
    from src.finance_api.auth import FinanceApiKeyAuth
    from src.finance_api.service import FinanceApiGateway
    from src.scenarios.financial_qa.service import FinancialQaCcService
    engine = FinancialQaCcService()
    gateway = FinanceApiGateway(engine=engine)
    token = secrets.token_urlsafe(32)
    app = create_app(gateway=gateway, auth=FinanceApiKeyAuth({"root-cause-regression": token}))
    output.mkdir(parents=True, exist_ok=True)
    with TestClient(app) as client:
        def one(case_id):
            original = json.loads((source / f"{case_id}.json").read_text())
            request = original["request"]
            started = time.monotonic()
            response = client.post("/mcp", headers={"X-API-Key": token, "Accept": "application/json, text/event-stream"},
                                   json={"jsonrpc": "2.0", "id": case_id, "method": "tools/call",
                                         "params": {"name": "finance_data_query", "arguments": request}})
            wire = response.json()
            payload = wire.get("result", {}).get("structuredContent", {})
            result = {"case": original["case"], "request": request, "response": payload,
                      "http_status": response.status_code, "elapsed_seconds": round(time.monotonic() - started, 3),
                      "transport": "real MCP tools/call over in-process ASGI; live deployment LLM and DB",
                      "mcp_error": wire.get("error") or (wire.get("result", {}).get("content") if wire.get("result", {}).get("isError") else None)}
            (output / f"{case_id}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
            print(json.dumps({"id": case_id, "ok": payload.get("ok"), "rows": payload.get("execution", {}).get("total_rows"),
                              "seconds": result["elapsed_seconds"], "turns": payload.get("detail", {}).get("turns"),
                              "error": payload.get("error") or result["mcp_error"]}, ensure_ascii=False), flush=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(one, ids))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("outputs/finance_root_cause_20260907/regression"))
    parser.add_argument("--ids", nargs="+", default=["BUS008", "BUS116", "BUS157", "BUS018", "BUS168", "BUS159", "RTEF112", "BUS042"])
    args = parser.parse_args()
    run(args.source, args.output, args.ids)
