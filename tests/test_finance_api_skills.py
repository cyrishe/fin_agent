"""MCP/REST -> shared gateway -> authorized Skill snapshot, without network/model calls."""
import asyncio
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from src.finance_api.app import create_app
from src.finance_api.auth import FinanceApiKeyAuth
from src.finance_api.models import FinanceTaskRequest
from src.finance_api.service import FinanceApiGateway
from src.scenarios.financial_qa.service import FinancialQaCcService
from src.services.skill_candidate_store_service import SkillCandidateStoreError
from tests.test_finance_skill_first_integration import hub, Session  # Shared published/private fixture.


KEY_A = "skill-api-client-a-test-key-123456"
KEY_B = "skill-api-client-b-test-key-123456"


@pytest.fixture
def api(hub, monkeypatch):
    monkeypatch.setenv("FINANCE_API_ALLOWED_HOSTS", "testserver,127.0.0.1:*,localhost:*")
    monkeypatch.setenv("FINANCE_API_ALLOWED_ORIGINS", "")
    monkeypatch.setenv("FINANCE_API_ROOT_PATH", "")
    session = Session()
    engine = FinancialQaCcService(enabled=True, skill_hub_catalog_service=hub,
        session_service=session, dsh_session_service=session)
    gateway = FinanceApiGateway(engine=engine, principal_owner_ids={"client-a": "alice"})
    app = create_app(auth=FinanceApiKeyAuth({"client-a": KEY_A, "client-b": KEY_B}), gateway=gateway)
    with TestClient(app) as client:
        yield client, session, gateway


def mcp(client, name, arguments=None, key=KEY_A):
    response = client.post("/mcp", headers={"X-API-Key": key, "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": name, "arguments": arguments or {}}})
    assert response.status_code == 200
    return response.json()["result"]


def test_discovery_is_authorized_body_free_and_matches_mcp_schema(api):
    client, session, gateway = api
    assert client.get("/v1/skills").status_code == 401
    listing = mcp(client, "list_skills")["structuredContent"]
    rest = client.get("/v1/skills", headers={"X-API-Key": KEY_A}).json()
    assert rest == listing
    personal = next(x for x in listing["skills"] if x["id"] == "personal-report")
    assert personal["active_revision_no"] == 1
    assert len(listing["revision"]) == 64
    assert "PRIVATE_METHOD_BODY" not in json.dumps(listing)
    assert "PRIVATE_REFERENCE" not in json.dumps(listing)
    assert "owner" not in personal and "path" not in personal
    bob = mcp(client, "list_skills", key=KEY_B)["structuredContent"]
    assert "personal-report" not in {x["id"] for x in bob["skills"]}
    assert session.calls == []  # Discovery performs no model execution.
    schemas = client.get("/v1/tools", headers={"X-API-Key": KEY_A}).json()["tools"]
    tools = {x["name"]: x for x in schemas}
    assert set(tools) == {"finance_data_query", "finance_task", "list_skills"}
    assert tools["finance_task"]["inputSchema"]["required"] == ["query"]
    assert tools["finance_task"]["inputSchema"]["properties"]["skill_ids"]
    assert tools["finance_task"]["http"]["path"] == "/v1/finance/task"
    listed = client.post("/mcp", headers={"X-API-Key": KEY_A, "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}).json()["result"]["tools"]
    assert all(x["inputSchema"] == tools[x["name"]]["inputSchema"] for x in listed)


@pytest.mark.parametrize("selection", [{}, {"skill_ids": None}, {"skill_ids": []}])
def test_generic_task_leaves_selection_to_existing_agent(api, selection):
    client, session, _ = api
    result = mcp(client, "finance_task", {"query": "分析盈利质量", **selection})
    assert not result["isError"]
    assert result["structuredContent"]["summary"] == "有来源的回答"
    call = session.calls[-1]
    context = call["context"]
    assert call["user_text"] == "分析盈利质量"
    assert context["_finance_explicit_skill_ids"] == []
    assert "personal-report" in context["_finance_skill_snapshot"]["skills"]
    assert "personal-report" in context["_finance_skill_catalog_prompt"]
    assert context["_finance_execution_mode"] == "standard"
    assert context["_finance_isolated_request"] is True
    assert call["owner_id"] == "alice"


def test_explicit_order_normalization_shared_data_and_rest_equivalence(api):
    client, session, _ = api
    selection = [" personal-report ", "earnings-analysis", "personal-report"]
    body = {"query": "分析盈利质量", "skill_ids": selection, "detail": True}
    result = mcp(client, "finance_task", body)
    assert not result["isError"]
    context = session.calls[-1]["context"]
    assert context["_finance_explicit_skill_ids"] == ["personal-report", "earnings-analysis"]
    assert context["_finance_explicit_skill_prompt"].index("personal-report") < context["_finance_explicit_skill_prompt"].index("earnings-analysis")
    assert context["_finance_data_only"] is False
    assert context["allowed_agent_tools"] == []  # No supplementary web/custom-tool grants.
    assert result["structuredContent"]["data"] is not None
    assert result["structuredContent"]["detail"]["skill_catalog_revision"] == context["_finance_skill_catalog_revision"]
    assert "PRIVATE_METHOD_BODY" not in json.dumps(result)
    for path in ("/v1/finance/task", "/v1/finance/answer"):
        response = client.post(path, headers={"X-API-Key": KEY_A}, json=body)
        assert response.status_code == 200
        assert session.calls[-1]["context"]["_finance_explicit_skill_ids"] == ["personal-report", "earnings-analysis"]


@pytest.mark.parametrize("name", ["personal-report", "does-not-exist"])
def test_hidden_or_unknown_selection_rejected_before_runtime(api, name):
    client, session, _ = api
    body = {"query": "分析", "skill_ids": [name]}
    result = mcp(client, "finance_task", body, key=KEY_B)
    assert result["isError"]
    assert "无权" in result["content"][0]["text"]
    assert session.calls == []
    for path in ("/v1/finance/task", "/v1/finance/answer"):
        response = client.post(path, headers={"X-API-Key": KEY_B}, json=body)
        assert response.status_code == 422
    assert session.calls == []


@pytest.mark.parametrize("skill_ids", ["earnings-analysis", [None], [7], ["   "], [{"id": "earnings-analysis"}]])
def test_malformed_selection_is_rejected_in_both_transports(api, skill_ids):
    client, session, _ = api
    body = {"query": "分析", "skill_ids": skill_ids}
    assert mcp(client, "finance_task", body)["isError"]
    assert client.post("/v1/finance/task", headers={"X-API-Key": KEY_A}, json=body).status_code == 422
    assert session.calls == []


def test_body_cannot_supply_owner_or_access_private_methods(api):
    client, session, _ = api
    body = {"query": "分析", "skill_ids": ["personal-report"], "owner_id": "alice"}
    assert client.post("/v1/finance/task", headers={"X-API-Key": KEY_B}, json=body).status_code == 422
    assert mcp(client, "finance_task", body, key=KEY_B)["isError"]
    assert not session.calls


def test_publication_visibility_changes_apply_to_discovery_and_execution(api, hub):
    client, session, _ = api
    hub.set_visibility("personal-report", owner_id="alice", visibility="public", expected_active_revision=1)
    assert "personal-report" in {s["id"] for s in mcp(client, "list_skills", key=KEY_B)["structuredContent"]["skills"]}
    body = {"query": "分析", "skill_ids": ["personal-report"]}
    assert not mcp(client, "finance_task", body, key=KEY_B)["isError"]
    assert session.calls[-1]["owner_id"] == "finance-api:client-b"
    hub.set_visibility("personal-report", owner_id="alice", visibility="private", expected_active_revision=1)
    assert mcp(client, "finance_task", body, key=KEY_B)["isError"]
    assert len(session.calls) == 1


def test_registry_failure_falls_back_to_system_without_private_authorization(api, hub, monkeypatch):
    client, session, _ = api
    def unavailable(**kwargs):
        raise SkillCandidateStoreError("INTERNAL_DATABASE_LOCATION")
    monkeypatch.setattr(hub.candidate_store, "list_available", unavailable)
    listing = mcp(client, "list_skills")["structuredContent"]
    assert listing["note"]
    assert "personal-report" not in {s["id"] for s in listing["skills"]}
    assert "INTERNAL_DATABASE_LOCATION" not in json.dumps(listing)
    assert not mcp(client, "finance_task", {"query": "分析", "skill_ids": ["earnings-analysis"]})["isError"]
    assert mcp(client, "finance_task", {"query": "分析", "skill_ids": ["personal-report"]})["isError"]
    assert len(session.calls) == 1


def test_direct_data_tool_remains_available(api):
    client, session, _ = api
    result = mcp(client, "finance_data_query", {"query": "最新收盘价", "response_mode": "data"})
    assert not result["isError"]
    assert result["structuredContent"]["summary"] is None
    assert session.calls[-1]["context"]["_finance_data_only"] is True


def test_task_authentication_and_execution_failure_evidence(api):
    client, session, _ = api
    body = {"query": "分析", "detail": True}
    assert client.post("/v1/finance/task", json=body).status_code == 401
    assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "finance_task", "arguments": body}}).status_code == 401
    assert not session.calls
    session.result = {"result": "取证未完成", "result_refs": [], "error": "查询暂不可用", "assistant_message_count": 2}
    result = mcp(client, "finance_task", body)
    assert result["isError"]
    assert result["structuredContent"]["detail"]["turns"] == 2
    rest = client.post("/v1/finance/task", headers={"X-API-Key": KEY_A}, json=body)
    assert rest.status_code == 502
    assert rest.json()["detail"]["turns"] == 2


def test_conversation_scope_includes_authenticated_principal_and_binding(api):
    _, session, gateway = api
    request = FinanceTaskRequest(query="分析", conversation_id="same-client-id")
    for principal in ("client-a", "client-b", "client-a"):
        asyncio.run(gateway.execute(request, principal_id=principal))
    assert session.calls[0]["thread_id"] == session.calls[2]["thread_id"]
    assert session.calls[0]["thread_id"] != session.calls[1]["thread_id"]
    legacy_digest = hashlib.sha256(b"client-b:same-client-id").hexdigest()[:32]
    assert session.calls[1]["thread_id"] == f"finance-api-{legacy_digest}"
    rebound = FinanceApiGateway(engine=gateway.engine, principal_owner_ids={"client-a": "bob"})
    asyncio.run(rebound.execute(request, principal_id="client-a"))
    assert session.calls[3]["thread_id"] != session.calls[0]["thread_id"]
    # Caller-controlled colons cannot imitate an owner binding boundary.
    unbound = FinanceApiGateway(engine=gateway.engine, principal_owner_ids={})
    asyncio.run(unbound.execute(FinanceTaskRequest(query="分析", conversation_id="alice:same-client-id"), principal_id="client-a"))
    assert session.calls[-1]["thread_id"] != session.calls[0]["thread_id"]


@pytest.mark.parametrize("value", ['[]', '{"a":null}', '{"a":" "}', '{broken'])
def test_invalid_server_identity_binding_fails_at_startup(monkeypatch, value):
    monkeypatch.setenv("FINANCE_API_OWNER_IDS_JSON", value)
    with pytest.raises(ValueError, match="FINANCE_API_OWNER_IDS_JSON"):
        FinanceApiGateway(engine=object())


def test_default_gateway_wires_published_registry(monkeypatch):
    from src.finance_api import service as module
    seen = {}
    def engine(**kwargs):
        seen.update(kwargs)
        return object()
    monkeypatch.setenv("FINANCE_API_OWNER_IDS_JSON", '{"client-a":"alice"}')
    monkeypatch.setattr(module, "FinancialQaCcService", engine)
    gateway = module.FinanceApiGateway()
    assert seen["skill_hub_catalog_service"] is not None
    assert gateway.owner_id("client-a") == "alice"
    assert gateway.owner_id("client-b") == "finance-api:client-b"
