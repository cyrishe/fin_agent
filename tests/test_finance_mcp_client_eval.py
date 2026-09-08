import asyncio
import json

import httpx
import pytest

from scripts.eval_finance_mcp import decode_rpc, evaluate, load_cases, load_token, unpack_result
from scripts.finance_mcp_report import chunks, report_sheets

TOKEN = "test-eval-token-123456789012345"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_case_formats_and_selection(tmp_path):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"pilot": [{"case_id": "BUS01", "question": "问题"}]}))
    cases = load_cases([path], ["第二题", "第三题"])
    assert [c["case_id"] for c in cases] == ["BUS01", "Q0002", "Q0003"]
    assert len(load_cases([path], case_ids=["BUS01"], limit=1)) == 1
    with pytest.raises(ValueError, match="Unknown"):
        load_cases([path], case_ids=["missing"])
    path.write_text(json.dumps([{"case_id": "../bad", "question": "问题"}]))
    with pytest.raises(ValueError, match="safe"):
        load_cases([path])


def test_token_inputs_and_expired_file(tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_TEST_TOKEN", TOKEN)
    assert load_token(token_env="EVAL_TEST_TOKEN") == TOKEN
    path = tmp_path / "token.json"
    path.write_text(json.dumps({"access_token": TOKEN, "expires_at": 1}))
    with pytest.raises(ValueError, match="expired"):
        load_token(path)
    path.write_text(TOKEN)
    assert load_token(path) == TOKEN


def test_sse_and_error_structured_payload_preserved():
    wire = {"id": 3, "result": {"isError": True, "structuredContent": {"ok": False, "detail": {"turns": 3}}}}
    response = httpx.Response(200, headers={"content-type": "text/event-stream"}, text="event: message\ndata: " + json.dumps(wire) + "\n\n")
    payload, error = unpack_result(decode_rpc(response, 3))
    assert payload["detail"]["turns"] == 3
    assert error["code"] == "mcp_tool_error"
    with pytest.raises(ValueError):
        decode_rpc(response, 4)


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["data", "both"])
async def test_concurrent_client_modes_order_isolation_and_redaction(tmp_path, mode):
    active = peak = 0
    seen = []

    async def handler(request):
        nonlocal active, peak
        assert request.headers["authorization"] == "Bearer " + TOKEN
        body = json.loads(request.content)
        method = body["method"]
        if method == "initialize":
            result = {"protocolVersion": "2025-03-26"}
        elif method == "notifications/initialized":
            return httpx.Response(202)
        elif method == "tools/list":
            result = {"tools": [{"name": "finance_data_query"}]}
        else:
            args = body["params"]["arguments"]
            seen.append(args)
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(.01)
            active -= 1
            result = {"structuredContent": {"ok": True, "query": args["query"], "summary": TOKEN if mode == "both" else None,
                      "data": {"results": [{"result_name": "r1", "row_count": 0, "rows": []}]}}}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    cases = load_cases(queries=["first", "second", "third"])
    folder = tmp_path / "run"
    records, manifest = await evaluate(cases, url="https://mcp.example/mcp", token=TOKEN, output_dir=folder,
        concurrency=2, response_mode=mode, transport=httpx.MockTransport(handler))
    assert peak == 2
    assert [r["case"]["question"] for r in records] == ["first", "second", "third"]
    assert len(seen) == 3 and all("conversation_id" not in a and a["runtime"] == "dsh" for a in seen)
    assert all(a["response_mode"] == mode for a in seen)
    assert all(not r["problems"] for r in records)
    for file in folder.rglob("*.json"):
        assert TOKEN not in file.read_text()
    with pytest.raises(FileExistsError):
        await evaluate(cases, url="https://mcp.example/mcp", token=TOKEN, output_dir=folder)


@pytest.mark.anyio
async def test_auth_failure_preflight_no_paid_queries(tmp_path):
    seen = []
    async def handler(request):
        seen.append(json.loads(request.content)["method"])
        return httpx.Response(401, json={"error": "unauthorized"})
    with pytest.raises(httpx.HTTPStatusError):
        await evaluate(load_cases(queries=["test"]), url="https://mcp.example/mcp", token=TOKEN,
            output_dir=tmp_path / "run", transport=httpx.MockTransport(handler))
    assert seen == ["initialize"]


@pytest.mark.anyio
async def test_auth_expiration_stops_pending_without_retry(tmp_path):
    queries = []
    async def handler(request):
        body = json.loads(request.content)
        method = body["method"]
        if method == "initialize":
            result = {"protocolVersion": "2025-03-26"}
        elif method == "notifications/initialized":
            return httpx.Response(202)
        elif method == "tools/list":
            result = {"tools": [{"name": "finance_data_query"}]}
        else:
            queries.append(body)
            return httpx.Response(401)
        return httpx.Response(200, json={"id": body["id"], "result": result})
    records, _ = await evaluate(load_cases(queries=["first", "second"]), url="https://mcp.example/mcp", token=TOKEN,
        output_dir=tmp_path / "run", concurrency=1, transport=httpx.MockTransport(handler))
    assert len(queries) == 1
    assert records[1]["error"] == "SkippedAfterAuthenticationFailure"


def test_report_not_false_accuracy_zero_distinct_from_failure_and_summary_complete():
    assert chunks(0) == ["0"]
    summary = "完整摘要" * 400
    records = [{"case": {"case_id": "A", "question": "问题", "required_entries": ["stock.report"]},
        "elapsed_seconds": 5, "response": {"ok": True, "summary": summary,
        "data": {"results": [{"result_name": "r1", "row_count": 0, "rows": [], "schema": {}}]},
        "detail": {"turns": 2, "total_tokens": 9000,
        "tool_calls": [{"request": "r1 = stock.report(filter=...) -> code"}]}}},
        {"case": {"case_id": "B", "question": "失败"}, "error": "ReadTimeout"}]
    main, detail = report_sheets(records, {})
    assert main["rows"][0][4] == "r1：0条"
    assert main["rows"][0][5] == 1
    assert main["rows"][0][-1] == "未人工评审"
    assert main["rows"][1][4] == "未返回数据集"
    assert main["rows"][1][3] is None
    assert "".join(r[5] for r in detail["rows"] if r[0].startswith("A")) == summary
