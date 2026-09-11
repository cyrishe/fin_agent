import asyncio
import json

import httpx
import pytest

from scripts.eval_finance_mcp import case_request, decode_rpc, evaluate, issue_evaluation_token, load_cases, load_run, load_token, unpack_result
from scripts.finance_mcp_report import cell_text, export_reading, report_sheets

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
            result = {"tools": [{"name": "finance_task"}, {"name": "finance_data_query"}]}
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
            result = {"tools": [{"name": "finance_task"}]}
        else:
            queries.append(body)
            return httpx.Response(401)
        return httpx.Response(200, json={"id": body["id"], "result": result})
    records, _ = await evaluate(load_cases(queries=["first", "second"]), url="https://mcp.example/mcp", token=TOKEN,
        output_dir=tmp_path / "run", concurrency=1, transport=httpx.MockTransport(handler))
    assert len(queries) == 1
    assert records[1]["error"] == "SkippedAfterAuthenticationFailure"


def test_report_not_false_accuracy_zero_distinct_from_failure_and_summary_complete():
    assert cell_text(0) == "0"
    summary = "完整摘要" * 400
    records = [{"case": {"case_id": "A", "question": "问题", "required_entries": ["stock.report"]},
        "elapsed_seconds": 5, "response": {"ok": True, "summary": summary,
        "data": {"results": [{"result_name": "r1", "row_count": 0, "rows": [], "schema": {}}]},
        "detail": {"turns": 2, "total_tokens": 9000,
        "tool_calls": [{"request": "r1 = stock.report(filter=...) -> code"}]}}},
        {"case": {"case_id": "B", "question": "失败"}, "error": "ReadTimeout"}]
    main, metrics, steps, calls, data = report_sheets(records, {})
    assert len(main["rows"]) == 2
    assert main["rows"][0][2] == summary
    assert main["rows"][0][10] == "未人工评审"
    assert main["rows"][1][9] == "未完成"
    assert main["rows"][1][6] is None
    assert data["rows"][0][3] == 0
    assert metrics["rows"][1][16] is None


def test_selection_defaults_order_and_compatible_case_inputs():
    case = {"case_id": "A", "question": "问题", "skill_ids": ["b", "a", "b"]}
    tool, request = case_request(case)
    assert tool == "finance_task" and request["skill_ids"] == ["b", "a"]
    assert request["detail"] is True and request["response_mode"] == "both"
    assert "skill_ids" not in case_request(case, skill_ids=[])[1]
    assert case_request(case, skill_ids=["c"])[1]["skill_ids"] == ["c"]
    assert case_request(case, tool="finance_data_query", skill_ids=[])[0] == "finance_data_query"
    assert case_request({**case, "tool": "finance_data_query", "skill_ids": []})[0] == "finance_data_query"
    with pytest.raises(ValueError, match="require"):
        case_request(case, tool="finance_data_query")
    with pytest.raises(ValueError, match="list"):
        case_request(case, skill_ids="not-a-list")


def test_issue_one_hour_in_memory_existing_principal(monkeypatch):
    from src.finance_api.auth import FinanceApiKeyAuth
    monkeypatch.delenv("FINANCE_API_KEYS_JSON", raising=False)
    monkeypatch.setenv("FINANCE_API_KEY", TOKEN)
    monkeypatch.setenv("FINANCE_API_KEY_ID", "eval")
    token, info = issue_evaluation_token()
    assert info["expires_in"] == 3600 and "access_token" not in info
    assert FinanceApiKeyAuth.from_env().authenticate("Bearer " + token).principal_id == "eval"
    for ttl in [0, float("nan"), -1]:
        with pytest.raises(ValueError):
            issue_evaluation_token(ttl_hours=ttl)


@pytest.mark.anyio
async def test_skill_preflight_mixed_routes_and_default_details(tmp_path):
    seen = []
    async def handler(request):
        b = json.loads(request.content)
        if b["method"] == "notifications/initialized":
            return httpx.Response(202)
        if b["method"] == "initialize": result = {"protocolVersion": "2025-11-25"}
        elif b["method"] == "tools/list": result = {"tools": [{"name": n} for n in ["finance_task", "finance_data_query", "list_skills"]]}
        elif b["params"]["name"] == "list_skills": result = {"structuredContent": {"skills": [{"id": "report"}], "revision": "r1"}}
        else:
            seen.append(b["params"])
            result = {"structuredContent": {"ok": True, "summary": "回答", "data": {"results": []}, "detail": {"turns": 2}}}
        return httpx.Response(200,json={"id":b["id"], "result":result})
    cases = [{"case_id":"A","question":"分析","skill_ids":["report"]},
             {"case_id":"B","question":"查数据","tool":"finance_data_query"},
             {"case_id":"C","question":"自动"}]
    records, manifest = await evaluate(cases,url="https://test/mcp",token=TOKEN,output_dir=tmp_path/"run",transport=httpx.MockTransport(handler))
    assert [s["name"] for s in seen] == ["finance_task","finance_data_query","finance_task"]
    assert seen[0]["arguments"]["skill_ids"] == ["report"]
    assert "skill_ids" not in seen[2]["arguments"]
    assert all(s["arguments"]["detail"] for s in seen)
    assert records[0]["wire_response"]["result"]["structuredContent"]["detail"]["turns"] == 2
    assert manifest["skill_catalog"]["revision"] == "r1"
    seen.clear()
    with pytest.raises(ValueError,match="unavailable"):
        await evaluate(cases,url="https://test/mcp",token=TOKEN,output_dir=tmp_path/"bad",tool="finance_task",skill_ids=["missing"],transport=httpx.MockTransport(handler))
    assert not seen


def test_legacy_export_and_html_preserve_full_answers(tmp_path):
    answer = '<script>alert(1)</script>\n' + '长回答😀' * 9000
    (tmp_path/'manifest.json').write_text(json.dumps({"isolated":True,"cases":[{"id":"A","question":"问题"}],"results":[{"id":"A","seconds":1.25}]}))
    (tmp_path/'A.json').write_text(json.dumps({"request":{"detail":True},"response":{"result":{"structuredContent":{"ok":True,"summary":answer}}}}))
    records,manifest=load_run(tmp_path)
    assert records[0]['elapsed_seconds']==1.25
    assert records[0]['response']['summary']==answer
    assert len(cell_text(answer).encode('utf-16-le'))//2 <= 32767
    export_reading(records,manifest,tmp_path,{})
    text=(tmp_path/'完整阅读.html').read_text()
    assert '<script>' not in text and '&lt;script&gt;' in text
    assert '长回答😀' * 9000 in text


def test_one_command_issues_key_runs_summary_without_detail_and_exports(tmp_path, monkeypatch, capsys):
    import scripts.eval_finance_mcp as cli
    import scripts.finance_mcp_report as report
    from src.finance_api.auth import FinanceApiKeyAuth
    monkeypatch.delenv('FINANCE_API_KEYS_JSON', raising=False)
    monkeypatch.setenv('FINANCE_API_KEY', TOKEN)
    monkeypatch.setenv('FINANCE_API_KEY_ID', 'eval')
    auth=FinanceApiKeyAuth.from_env()
    requests=[]
    tokens=[]
    async def handler(request):
        assert auth.authenticate(request.headers['authorization']).principal_id=='eval'
        tokens.append(request.headers['authorization'].split()[1])
        b=json.loads(request.content)
        if b['method']=='notifications/initialized':return httpx.Response(202)
        if b['method']=='initialize':result={'protocolVersion':'2025-11-25'}
        elif b['method']=='tools/list':result={'tools':[{'name':'finance_task'}]}
        else:
            requests.append(b['params'])
            result={'structuredContent':{'ok':True,'summary':'完整回答','data':None,'detail':None}}
        return httpx.Response(200,json={'id':b['id'],'result':result})
    original=cli.evaluate
    async def connected(*args,**kwargs):
        return await original(*args,**kwargs,transport=httpx.MockTransport(handler))
    monkeypatch.setattr(cli,'evaluate',connected)
    exported=[]
    def capture(records,manifest,folder,**kwargs):
        exported.append((records,manifest))
        return folder/'report.xlsx'
    monkeypatch.setattr(report,'export_report',capture)
    assert cli.main(['--url','https://test/mcp','--issue-token','--query','问题','--response-mode','summary',
                     '--no-detail','--output-dir',str(tmp_path/'run')])==0
    assert requests[0]['arguments']['detail'] is False
    assert requests[0]['arguments']['response_mode']=='summary'
    assert exported[0][1]['credential']['expires_in']==3600
    assert exported[0][0][0]['response']['summary']=='完整回答'
    assert tokens and all(t.startswith('fa_tmp_v1.') for t in tokens)
    saved=''.join(p.read_text() for p in (tmp_path/'run').rglob('*.json'))
    logs=capsys.readouterr()
    assert all(t not in saved+logs.out+logs.err for t in tokens)
    assert TOKEN not in saved+logs.out+logs.err


@pytest.mark.anyio
async def test_unavailable_entry_does_not_silently_fallback(tmp_path):
    paid=[]
    async def handler(request):
        b=json.loads(request.content)
        if b['method']=='notifications/initialized':return httpx.Response(202)
        if b['method']=='initialize':result={'protocolVersion':'2025-11-25'}
        elif b['method']=='tools/list':result={'tools':[{'name':'finance_data_query'}]}
        else:paid.append(b);result={}
        return httpx.Response(200,json={'id':b['id'],'result':result})
    with pytest.raises(ValueError,match='unavailable'):
        await evaluate(load_cases(queries=['自动']),url='https://test/mcp',token=TOKEN,
                       output_dir=tmp_path/'run',transport=httpx.MockTransport(handler))
    assert not paid
