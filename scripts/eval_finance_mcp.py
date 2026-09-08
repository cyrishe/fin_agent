"""Evaluate an existing authenticated finance MCP endpoint and export Excel.

Pure HTTP client: no server startup, DB credentials, model keys or auth bypass.
No request retries and no implicit conversation_id. See docs/finance_mcp_evaluation.md.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TOOL = "finance_data_query"


def load_cases(paths=(), queries=(), *, case_ids=None, limit=None):
    cases = []
    for path in paths:
        source = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(source, dict):
            source = source.get("cases", source.get("pilot"))
        if not isinstance(source, list):
            raise ValueError("Case file must contain a list, a cases list, or a pilot list.")
        for raw in source:
            if not isinstance(raw, (str, dict)):
                raise ValueError("Each case must be a question string or object.")
            item = {"question": raw} if isinstance(raw, str) else dict(raw)
            item["question"] = item.get("question") or item.get("query")
            item.setdefault("case_id", f"Q{len(cases) + 1:04d}")
            cases.append(item)
    offset = len(cases)
    cases.extend({"case_id": f"Q{offset + i + 1:04d}", "question": q}
                 for i, q in enumerate(queries))
    seen = set()
    for case in cases:
        cid, question = case.get("case_id"), case.get("question")
        if (not isinstance(cid, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", cid)
                or cid in seen):
            raise ValueError("Case IDs must be unique, filesystem-safe identifiers.")
        if not isinstance(question, str) or not question.strip() or len(question.strip()) > 4000:
            raise ValueError(f"Invalid query for {cid}")
        seen.add(cid)
    if case_ids:
        unknown = set(case_ids) - seen
        if unknown:
            raise ValueError(f"Unknown case IDs: {sorted(unknown)}")
        cases = [case for case in cases if case["case_id"] in case_ids]
    if limit is not None:
        cases = cases[:limit]
    if not cases:
        raise ValueError("Supply at least one --query or --cases-file.")
    return cases


def load_token(token_file=None, token_env="FINANCE_ACCESS_TOKEN"):
    if token_file:
        text = Path(token_file).read_text(encoding="utf-8").strip()
        if text.startswith("{"):
            credential = json.loads(text)
            if credential.get("expires_at") is not None and time.time() >= float(credential["expires_at"]):
                raise ValueError("Token file has expired; issue a new temporary token.")
            text = credential.get("access_token", "")
    else:
        text = os.environ.get(token_env, "").strip()
    if not isinstance(text, str) or not text or any(c.isspace() for c in text):
        raise ValueError("Provide --token-file or a nonempty token environment variable.")
    return text


def redact(value, token):
    if isinstance(value, str):
        return value.replace(token, "[REDACTED]")
    if isinstance(value, list):
        return [redact(item, token) for item in value]
    if isinstance(value, dict):
        return {redact(k, token): redact(v, token) for k, v in value.items()}
    return value


def decode_rpc(response, request_id):
    """Our server uses JSON; accept finite SSE responses as well."""
    if "text/event-stream" in response.headers.get("content-type", ""):
        messages = []
        for event in response.text.replace("\r\n", "\n").split("\n\n"):
            data = "\n".join(line[5:].lstrip() for line in event.splitlines() if line.startswith("data:"))
            if data and data != "[DONE]":
                messages.append(json.loads(data))
        wire = next((m for m in messages if m.get("id") == request_id), None)
    else:
        wire = response.json()
    if not isinstance(wire, dict) or wire.get("id") != request_id:
        raise ValueError("MCP response ID mismatch or missing response")
    return wire


def unpack_result(wire):
    result = wire.get("result") or {}
    payload = result.get("structuredContent")
    if not isinstance(payload, dict):
        for block in result.get("content") or []:
            if block.get("type") == "text":
                try:
                    candidate = json.loads(block.get("text", ""))
                    if isinstance(candidate, dict) and "ok" in candidate:
                        payload = candidate
                        break
                except (ValueError, TypeError):
                    pass
    error = wire.get("error")
    if result.get("isError"):
        error = error or (payload or {}).get("error") or {"code": "mcp_tool_error"}
    return payload if isinstance(payload, dict) else {}, error


def response_problems(payload, mode):
    problems = []
    if payload.get("ok") is not True:
        problems.append("接口未完成")
    else:
        if not isinstance((payload.get("data") or {}).get("results"), list):
            problems.append("缺少结构化数据集")
        if mode == "data" and payload.get("summary") is not None:
            problems.append("仅数据模式却返回summary")
        if mode == "both" and not str(payload.get("summary") or "").strip():
            problems.append("both模式缺少summary")
    return problems


async def evaluate(cases, *, url, token, output_dir, concurrency=2, response_mode="data",
                   execution_mode="standard", timeout=180, max_rows=100, transport=None):
    folder = Path(output_dir)
    # A new directory prevents accidental overwrite, replay or mixing configurations.
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "results").mkdir()
    manifest = {"url": url, "runtime": "dsh", "execution_mode": execution_mode,
                "response_mode": response_mode, "research_mode": "fast", "detail": True,
                "max_rows": max_rows, "concurrency": concurrency, "timeout_seconds": timeout,
                "conversation_id_supplied": False, "automatic_retries": 0,
                "created_at": datetime.now(timezone.utc).isoformat(), "cases": cases}
    (folder / "manifest.json").write_text(json.dumps(redact(manifest, token), ensure_ascii=False, indent=2))
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"}
    stop_auth = asyncio.Event()
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=False,
                                 limits=httpx.Limits(max_connections=concurrency), transport=transport) as client:
        # Initialize and discover before paid queries; an invalid token fails here.
        init = await client.post(url, json={"jsonrpc": "2.0", "id": "initialize", "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "fin-agent-eval", "version": "1.0"}}})
        init.raise_for_status()
        wire = decode_rpc(init, "initialize")
        if wire.get("error"):
            raise ValueError("MCP initialization failed")
        if init.headers.get("mcp-session-id"):
            # This finance endpoint is stateless. Do not silently share an unfamiliar server session.
            raise ValueError("Expected the stateless Fin Agent MCP endpoint")
        protocol = (wire.get("result") or {}).get("protocolVersion")
        if protocol:
            client.headers["MCP-Protocol-Version"] = protocol
        notification = await client.post(url, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        notification.raise_for_status()
        listing = await client.post(url, json={"jsonrpc": "2.0", "id": "list", "method": "tools/list", "params": {}})
        listing.raise_for_status()
        tools = (decode_rpc(listing, "list").get("result") or {}).get("tools") or []
        descriptor = next((t for t in tools if t.get("name") == TOOL), None)
        if not descriptor:
            raise ValueError("finance_data_query is not exposed by this MCP endpoint")

        async def one(case):
            cid = case["case_id"]
            request = {"query": case["question"], "runtime": "dsh", "response_mode": response_mode,
                       "execution_mode": execution_mode, "research_mode": "fast", "detail": True,
                       "max_rows": max_rows}
            record = {"case": case, "request": request, "transport": "mcp"}
            async with sem:
                if stop_auth.is_set():
                    record.update(error="SkippedAfterAuthenticationFailure", elapsed_seconds=None)
                else:
                    started = time.monotonic()
                    try:
                        res = await client.post(url, json={"jsonrpc": "2.0", "id": cid, "method": "tools/call",
                            "params": {"name": TOOL, "arguments": request}})
                        record["http_status"] = res.status_code
                        if res.status_code in (401, 403):
                            stop_auth.set()
                        res.raise_for_status()
                        payload, error = unpack_result(decode_rpc(res, cid))
                        record.update(response=payload, mcp_error=error,
                                      problems=response_problems(payload, response_mode))
                    except (httpx.HTTPError, ValueError, TypeError) as exc:
                        record["error"] = type(exc).__name__  # no headers/credentials in exception dumps
                    record["elapsed_seconds"] = round(time.monotonic() - started, 3)
                record["finished_at"] = datetime.now(timezone.utc).isoformat()
                record = redact(record, token)
                (folder / "results" / f"{cid}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
                print(json.dumps({"case_id": redact(cid, token), "ok": record.get("response", {}).get("ok"),
                                  "seconds": record["elapsed_seconds"], "error": record.get("error")}, ensure_ascii=False), flush=True)
                return record

        records = await asyncio.gather(*(one(case) for case in cases))
    (folder / "results.json").write_text(json.dumps(records, ensure_ascii=False, indent=2))
    return records, redact(manifest, token)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="Complete MCP URL, including /fin_agent/mcp when deployed behind nginx.")
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--token-env", default="FINANCE_ACCESS_TOKEN")
    parser.add_argument("--cases-file", type=Path, action="append", default=[])
    parser.add_argument("--query", action="append", default=[])
    parser.add_argument("--case-ids", nargs="+")
    parser.add_argument("--limit", type=int, help="Run only the first N selected cases, e.g. 3.")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--response-mode", choices=("data", "both"), default="data")
    parser.add_argument("--execution-mode", choices=("standard", "fast"), default="standard")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--max-rows", type=int, default=100, help="MCP rows per dataset, 1..100; Excel previews only first two.")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs") / ("mcp_eval_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")))
    parser.add_argument("--export-only", type=Path, help="Rebuild Excel from this run directory, without any API/model calls.")
    parser.add_argument("--review-file", type=Path, help="Optional human reviews keyed by case_id. Never auto-invent correctness judgments.")
    parser.add_argument("--allow-insecure-http", action="store_true", help="Explicitly allow a remote unencrypted HTTP endpoint.")
    args = parser.parse_args(argv)
    from scripts.finance_mcp_report import export_report
    try:
        if args.export_only:
            folder = args.export_only
            records = json.loads((folder / "results.json").read_text())
            manifest = json.loads((folder / "manifest.json").read_text())
        else:
            if (args.concurrency < 1 or not 0 < args.timeout < float("inf") or
                    not 1 <= args.max_rows <= 100 or (args.limit is not None and args.limit < 1)):
                raise ValueError("Invalid concurrency, timeout, max-rows or limit.")
            parts = urlsplit(args.url or "")
            if (parts.scheme not in ("https", "http") or not parts.hostname or parts.username
                    or parts.password or parts.query or parts.fragment):
                raise ValueError("--url must be an HTTP(S) endpoint without credentials/query/fragment.")
            if parts.scheme == "http" and parts.hostname not in ("localhost", "127.0.0.1", "::1") and not args.allow_insecure_http:
                raise ValueError("Use HTTPS or explicitly opt in with --allow-insecure-http.")
            cases = load_cases(args.cases_file, args.query, case_ids=args.case_ids, limit=args.limit)
            token = load_token(args.token_file, args.token_env)
            records, manifest = asyncio.run(evaluate(cases, url=args.url, token=token,
                output_dir=args.output_dir, concurrency=args.concurrency, response_mode=args.response_mode,
                execution_mode=args.execution_mode, timeout=args.timeout, max_rows=args.max_rows))
            folder = args.output_dir
        reviews = json.loads(args.review_file.read_text()) if args.review_file else {}
        path = export_report(records, manifest, folder, reviews=reviews)
        print(f"Excel: {path}")
        return 1 if any(r.get("error") or r.get("mcp_error") or r.get("problems") or
                        r.get("response", {}).get("ok") is not True for r in records) else 0
    except (ValueError, OSError, httpx.HTTPError, RuntimeError) as exc:
        print(f"Evaluation failed ({type(exc).__name__}). Check endpoint/auth/input/dependencies. Saved JSON is retained; no automatic replay.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
