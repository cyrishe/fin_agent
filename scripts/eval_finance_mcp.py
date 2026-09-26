"""Evaluate an existing authenticated finance MCP endpoint and export Excel.

Pure HTTP client: no server startup, DB credentials, model keys or auth bypass.
No request retries and no implicit conversation_id. See docs/finance_mcp_evaluation.md.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit

try:
    import httpx
except ImportError:
    httpx = None  # Offline report export does not need HTTP dependencies.
HTTP_ERROR = httpx.HTTPError if httpx is not None else RuntimeError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TOOL = "finance_task"
QUERY_TOOLS = ("finance_task", "finance_data_query")


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
            item.setdefault("case_id", item.get("id") or f"Q{len(cases) + 1:04d}")
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


def issue_evaluation_token(*, env_file=None, principal=None, ttl_hours=1):
    """Operator-only issuance using existing signing policy; never persist the key."""
    if not math.isfinite(ttl_hours) or not 1 <= ttl_hours * 3600 <= 2**31 - 1:
        raise ValueError("--ttl-hours must specify at least one second of validity.")
    if env_file:
        if not Path(env_file).is_file():
            raise ValueError("--env-file does not exist")
        from dotenv import load_dotenv
        load_dotenv(env_file, override=False)
    from src.finance_api.auth import FinanceApiKeyAuth
    credential = FinanceApiKeyAuth.from_env().issue_temporary_token(
        principal, ttl_seconds=int(ttl_hours * 3600))
    return credential["access_token"], {k: v for k, v in credential.items() if k != "access_token"}


def case_request(case, *, tool=None, skill_ids=None, detail=True, runtime="dsh",
                 response_mode="both", research_mode="auto", execution_mode="standard", max_rows=100):
    """CLI selection overrides case selection; omitted/empty IDs retain MCP auto routing."""
    selected_tool = tool or case.get("tool") or TOOL
    if selected_tool not in QUERY_TOOLS:
        raise ValueError(f"Unsupported MCP query tool: {selected_tool}")
    selected_skills = skill_ids if skill_ids is not None else case.get("skill_ids")
    if selected_skills is not None:
        if not isinstance(selected_skills, list) or any(not isinstance(s, str) or not s.strip() for s in selected_skills):
            raise ValueError("skill_ids must be a list of nonempty Skill IDs")
        selected_skills = list(dict.fromkeys(s.strip() for s in selected_skills))
    if selected_tool != TOOL and selected_skills:
        raise ValueError("Explicit Skills require --tool finance_task; use --auto to clear case Skills.")
    request = {"query": case["question"], "runtime": runtime, "response_mode": response_mode,
               "execution_mode": execution_mode, "research_mode": research_mode,
               "detail": detail, "max_rows": max_rows, "is_test": True}
    if selected_tool == TOOL and selected_skills:
        request["skill_ids"] = selected_skills
    return selected_tool, request


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
                    if isinstance(candidate, dict) and ("ok" in candidate or "skills" in candidate):
                        payload = candidate
                        break
                except (ValueError, TypeError):
                    pass
    error = wire.get("error")
    if result.get("isError"):
        error = error or (payload or {}).get("error") or {"code": "mcp_tool_error"}
    return payload if isinstance(payload, dict) else {}, error


def load_run(folder):
    """Read normal CLI runs and the earlier server-isolated report acceptance run."""
    folder = Path(folder)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if (folder / "results.json").is_file():
        return json.loads((folder / "results.json").read_text(encoding="utf-8")), manifest
    cases = load_cases([folder / "manifest.json"])
    timing = {r["id"]: r for r in manifest.get("results", [])}
    records = []
    for case in cases:
        cid = case["case_id"]
        path = folder / f"{cid}.json"
        if not path.is_file():
            path = folder / "results" / f"{cid}.json"
        if not path.is_file():
            records.append({"case": case, "error": "NoSavedResponse"})
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        if "case" in raw:
            records.append(raw)
        else:
            payload, error = unpack_result(raw.get("response") or {})
            records.append({"case": case, "request": raw.get("request", {}), "response": payload,
                "wire_response": raw.get("response"), "mcp_error": error, "tool": "finance_task",
                "elapsed_seconds": timing.get(cid, {}).get("seconds"), "source_file": str(path.resolve())})
    return records, manifest


def response_problems(payload, mode):
    problems = []
    if payload.get("ok") is not True:
        problems.append("接口未完成")
    else:
        if mode != "summary" and not isinstance((payload.get("data") or {}).get("results"), list):
            problems.append("缺少结构化数据集")
        if mode == "data" and payload.get("summary") is not None:
            problems.append("仅数据模式却返回summary")
        if mode in ("both", "summary") and not str(payload.get("summary") or "").strip():
            problems.append(f"{mode}模式缺少summary")
    return problems


async def evaluate(cases, *, url, token, output_dir, concurrency=2, response_mode="both",
                   execution_mode="standard", timeout=360, max_rows=100, transport=None,
                   tool=None, skill_ids=None, detail=True, research_mode="auto", runtime="dsh",
                   credential_info=None, revision=None):
    if httpx is None:
        raise RuntimeError("Install httpx to run API evaluations; offline export is available without it.")
    requests = {c["case_id"]: case_request(c, tool=tool, skill_ids=skill_ids, detail=detail,
        runtime=runtime, response_mode=response_mode, research_mode=research_mode,
        execution_mode=execution_mode, max_rows=max_rows) for c in cases}
    folder = Path(output_dir)
    # A new directory prevents accidental overwrite, replay or mixing configurations.
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "results").mkdir()
    manifest = {"url": url, "runtime": runtime, "execution_mode": execution_mode,
                "response_mode": response_mode, "research_mode": research_mode, "detail": detail,
                "tool": tool or TOOL, "case_tools": {cid: name for cid, (name, _) in requests.items()},
                "skill_ids": skill_ids, "credential": credential_info,
                "server_revision": revision, "revision_basis": "caller supplied; not independently verified",
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
        names = {t.get("name") for t in tools}
        if {name for name, _ in requests.values()} - names:
            raise ValueError("Requested MCP tool is unavailable; check deployment version.")
        for descriptor in tools:
            if descriptor.get("name") in {name for name, _ in requests.values()} and "inputSchema" in descriptor:
                if "is_test" not in descriptor["inputSchema"].get("properties", {}):
                    raise ValueError("Server does not support test accounting yet; deploy/restart before evaluating.")
        manifest["protocol_version"] = protocol
        manifest["available_tools"] = sorted(names)
        # Resolve explicit IDs against the same authenticated catalog before model calls.
        requested_skills = {s for _, request in requests.values() for s in request.get("skill_ids", [])}
        if "list_skills" in names:
            res = await client.post(url, json={"jsonrpc": "2.0", "id": "skills", "method": "tools/call",
                "params": {"name": "list_skills", "arguments": {}}})
            res.raise_for_status()
            catalog, error = unpack_result(decode_rpc(res, "skills"))
            if error:
                raise ValueError("Skill catalog preflight failed")
            manifest["skill_catalog"] = catalog
            if requested_skills - {s.get("id") for s in catalog.get("skills", [])}:
                raise ValueError("Requested Skill is unavailable to this API principal")
        elif requested_skills:
            raise ValueError("list_skills is unavailable; cannot verify explicit Skill selection")
        (folder / "manifest.json").write_text(json.dumps(redact(manifest, token), ensure_ascii=False, indent=2))

        async def one(case):
            cid = case["case_id"]
            selected_tool, request = requests[cid]
            record = {"case": case, "request": request, "tool": selected_tool, "transport": "mcp"}
            async with sem:
                if stop_auth.is_set():
                    record.update(error="SkippedAfterAuthenticationFailure", elapsed_seconds=None)
                else:
                    started = time.monotonic()
                    try:
                        res = await client.post(url, json={"jsonrpc": "2.0", "id": cid, "method": "tools/call",
                            "params": {"name": selected_tool, "arguments": request}})
                        record["http_status"] = res.status_code
                        if res.status_code in (401, 403):
                            stop_auth.set()
                        res.raise_for_status()
                        wire = decode_rpc(res, cid)
                        payload, error = unpack_result(wire)
                        record.update(response=payload, wire_response=wire, mcp_error=error,
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
    manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
    (folder / "manifest.json").write_text(json.dumps(redact(manifest, token), ensure_ascii=False, indent=2))
    return records, redact(manifest, token)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="Complete MCP URL, including /fin_agent/mcp when deployed behind nginx.")
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--token-env", default="FINANCE_ACCESS_TOKEN")
    parser.add_argument("--issue-token", action="store_true", help="Operator: mint a temporary token in memory, then run.")
    parser.add_argument("--env-file", type=Path, help="Parent API key environment; only used with --issue-token.")
    parser.add_argument("--principal", help="Existing API key principal for issuance.")
    parser.add_argument("--ttl-hours", type=float, default=1, help="Temporary token lifetime, default 1 hour.")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--skill", action="append", dest="skill_ids", help="Skill ID; repeat for ordered multi-Skill execution.")
    selection.add_argument("--auto", action="store_true", help="Clear case-level Skill selection and let MCP route.")
    parser.add_argument("--tool", choices=QUERY_TOOLS, help="MCP entry; default finance_task, or case-level tool.")
    parser.add_argument("--detail", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--runtime", choices=("dsh", "cc"), default="dsh")
    parser.add_argument("--research-mode", choices=("auto", "fast", "deep"), default="auto")
    parser.add_argument("--revision", help="Known server commit for evidence, not the local checkout revision.")
    parser.add_argument("--cases-file", type=Path, action="append", default=[])
    parser.add_argument("--query", action="append", default=[])
    parser.add_argument("--case-ids", nargs="+")
    parser.add_argument("--limit", type=int, help="Run only the first N selected cases, e.g. 3.")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--response-mode", choices=("data", "summary", "both"), default="both")
    parser.add_argument("--execution-mode", choices=("standard", "fast"), default="standard")
    parser.add_argument("--timeout", type=float, default=360)
    parser.add_argument("--max-rows", type=int, default=100, help="MCP returned rows per dataset, 1..100.")
    parser.add_argument("--output-dir", type=Path, help="New run directory; for --export-only may be a separate export directory.")
    parser.add_argument("--export-only", type=Path, help="Rebuild Excel from this run directory, without any API/model calls.")
    parser.add_argument("--review-file", type=Path, help="Optional human reviews keyed by case_id. Never auto-invent correctness judgments.")
    parser.add_argument("--allow-insecure-http", action="store_true", help="Explicitly allow a remote unencrypted HTTP endpoint.")
    args = parser.parse_args(argv)
    from scripts.finance_mcp_report import export_report
    token = ""
    try:
        if args.export_only:
            records, manifest = load_run(args.export_only)
            folder = args.output_dir or args.export_only
            if folder.resolve() != args.export_only.resolve():
                folder.mkdir(parents=True, exist_ok=False)
                (folder / "results.json").write_text(json.dumps(records, ensure_ascii=False, indent=2))
                (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
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
            args.output_dir = args.output_dir or Path("outputs") / ("mcp_eval_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
            if args.runtime == "cc" and args.execution_mode == "fast":
                raise ValueError("fast execution requires dsh")
            if args.issue_token and args.token_file:
                raise ValueError("Choose --issue-token or --token-file")
            if not args.issue_token and (args.env_file or args.principal):
                raise ValueError("--env-file/--principal requires --issue-token")
            credential_info = None
            if args.issue_token:
                token, credential_info = issue_evaluation_token(env_file=args.env_file,
                    principal=args.principal, ttl_hours=args.ttl_hours)
            else:
                token = load_token(args.token_file, args.token_env)
            records, manifest = asyncio.run(evaluate(cases, url=args.url, token=token,
                output_dir=args.output_dir, concurrency=args.concurrency, response_mode=args.response_mode,
                execution_mode=args.execution_mode, timeout=args.timeout, max_rows=args.max_rows,
                tool=args.tool or (TOOL if args.auto else None), skill_ids=[] if args.auto else args.skill_ids, detail=args.detail,
                research_mode=args.research_mode, runtime=args.runtime,
                credential_info=credential_info, revision=args.revision))
            folder = args.output_dir
        reviews = json.loads(args.review_file.read_text()) if args.review_file else {}
        path = export_report(records, manifest, folder, reviews=reviews)
        print(f"Excel: {path}")
        return 1 if any(r.get("error") or r.get("mcp_error") or r.get("problems") or
                        r.get("response", {}).get("ok") is not True for r in records) else 0
    except (ValueError, OSError, HTTP_ERROR, RuntimeError) as exc:
        print(f"Evaluation failed ({type(exc).__name__}). Check endpoint/auth/input/dependencies. Saved JSON is retained; no automatic replay.", file=sys.stderr)
        if isinstance(exc, (ValueError, RuntimeError)):
            print(redact(str(exc), token) if token else str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
