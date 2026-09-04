from __future__ import annotations

from fastapi.testclient import TestClient

from src.finance_api.app import create_app
from src.finance_api.auth import FinanceApiKeyAuth
from src.finance_api.models import FinanceQueryResponse


KEY = "test-finance-api-key-1234567890"


class _Gateway:
    default_runtime = "dsh"

    def __init__(self) -> None:
        self.calls: list[tuple[object, str]] = []

    async def execute(self, request, *, principal_id: str):
        self.calls.append((request, principal_id))
        include_summary = request.response_mode in {"summary", "both"}
        include_data = request.response_mode in {"data", "both"}
        return FinanceQueryResponse.model_validate(
            {
                "id": "fq_test",
                "created_at": "2026-09-04T00:00:00+00:00",
                "ok": True,
                "query": request.query,
                "response_mode": request.response_mode,
                "runtime": request.runtime or "dsh",
                "conversation_id": request.conversation_id,
                "summary": "测试摘要" if include_summary else None,
                "data": {"format": "row-dict", "results": []} if include_data else None,
                "execution": {},
            }
        )

    def close(self) -> None:
        return None


def _client() -> tuple[TestClient, _Gateway]:
    gateway = _Gateway()
    app = create_app(
        auth=FinanceApiKeyAuth({"client-a": KEY}),
        gateway=gateway,
    )
    return TestClient(app), gateway


def test_public_health_catalog_and_data_map() -> None:
    client, _ = _client()
    with client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["authentication"]["configured"] is True

        catalog = client.get("/data-map/catalog.json")
        assert catalog.status_code == 200
        assert catalog.json()["subject_count"] == 7
        assert catalog.json()["dataview_count"] == 31

        assert client.get("/v1/finance/catalog").status_code == 401
        protected_catalog = client.get(
            "/v1/finance/catalog",
            headers={"X-API-Key": KEY},
        )
        assert protected_catalog.status_code == 200

        page = client.get("/data-map")
        assert page.status_code == 200
        assert "Fin Agent 数据体系" in page.text
        assert "data-action=\"expand\"" in page.text


def test_reverse_proxy_root_path_is_reflected_in_redirects_and_docs(monkeypatch) -> None:
    monkeypatch.setenv("FINANCE_API_ROOT_PATH", "/finance/")
    client, _ = _client()
    with client:
        root = client.get("/", follow_redirects=False)
        assert root.status_code == 307
        assert root.headers["location"] == "/finance/data-map"

        schema = client.get("/openapi.json").json()
        assert schema["servers"] == [{"url": "/finance"}]

        page = client.get("/data-map")
        assert 'href="docs"' in page.text
        assert "fetch('data-map/catalog.json')" in page.text


def test_protected_rest_query_and_answer_modes() -> None:
    client, gateway = _client()
    with client:
        assert client.post(
            "/v1/finance/query",
            json={"query": "贵州茅台行情"},
        ).status_code == 401

        headers = {"Authorization": f"Bearer {KEY}"}
        query = client.post(
            "/v1/finance/query",
            headers=headers,
            json={"query": "贵州茅台行情", "response_mode": "data"},
        )
        assert query.status_code == 200
        assert query.json()["data"] == {"format": "row-dict", "results": []}
        assert query.json()["summary"] is None

        answer = client.post(
            "/v1/finance/answer",
            headers={"X-API-Key": KEY},
            json={"query": "解释贵州茅台行情", "include_data": False},
        )
        assert answer.status_code == 200
        assert answer.json()["summary"] == "测试摘要"
        assert answer.json()["data"] is None
        assert all(principal == "client-a" for _, principal in gateway.calls)


def test_tool_discovery_exposes_schema_and_mcp_location() -> None:
    client, _ = _client()
    with client:
        response = client.get("/v1/tools", headers={"X-API-Key": KEY})
        assert response.status_code == 200
        tool = response.json()["tools"][0]
        assert tool["name"] == "finance_data_query"
        assert tool["inputSchema"]["properties"]["response_mode"]["enum"] == [
            "data",
            "summary",
            "both",
        ]
        assert tool["mcp"]["path"] == "/mcp"
        assert tool["annotations"]["readOnlyHint"] is True


def test_mcp_streamable_http_requires_key_and_calls_same_gateway() -> None:
    client, gateway = _client()
    with client:
        unauthorized = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            },
        )
        assert unauthorized.status_code == 401

        headers = {
            "Authorization": f"Bearer {KEY}",
            "Accept": "application/json, text/event-stream",
        }
        listed = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            },
        )
        assert listed.status_code == 200
        assert listed.json()["result"]["tools"][0]["name"] == "finance_data_query"

        called = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "finance_data_query",
                    "arguments": {
                        "query": "贵州茅台行情",
                        "response_mode": "data",
                    },
                },
            },
        )
        assert called.status_code == 200
        result = called.json()["result"]
        assert result["isError"] is False
        assert result["structuredContent"]["response_mode"] == "data"
        assert gateway.calls[-1][1] == "client-a"
