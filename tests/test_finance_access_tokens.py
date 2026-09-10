import json
import stat

import httpx
import pytest

from src.finance_api import access_tokens
from src.finance_api.auth import FinanceApiAuthError, FinanceApiKeyAuth
from scripts.create_finance_access_token import main

KEY = "test-finance-parent-key-01234567890123"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_default_four_hours_and_same_principal(monkeypatch):
    monkeypatch.setattr(access_tokens.time, "time", lambda: 1000)
    auth = FinanceApiKeyAuth({"eval": KEY})
    issued = auth.issue_temporary_token()
    assert issued["expires_in"] == 14400
    assert issued["expires_at"] == 15400
    assert auth.authenticate("Bearer " + issued["access_token"]).principal_id == "eval"
    assert auth.authenticate(x_api_key=issued["access_token"]).principal_id == "eval"
    assert auth.authenticate(x_api_key=KEY).principal_id == "eval"
    assert issued["access_token"] != auth.issue_temporary_token()["access_token"]


@pytest.mark.parametrize("when, valid", [(100, True), (159.9, True), (160, False), (99, False)])
def test_expiration_boundary_and_not_before(monkeypatch, when, valid):
    monkeypatch.setattr(access_tokens.time, "time", lambda: 100)
    auth = FinanceApiKeyAuth({"eval": KEY})
    token = auth.issue_temporary_token(ttl_seconds=60)["access_token"]
    monkeypatch.setattr(access_tokens.time, "time", lambda: when)
    if valid:
        assert auth.authenticate(x_api_key=token).principal_id == "eval"
    else:
        with pytest.raises(FinanceApiAuthError, match="invalid or expired"):
            auth.authenticate(x_api_key=token)


def test_tampered_principal_signature_and_parent_rotation():
    auth = FinanceApiKeyAuth({"eval": KEY, "other": KEY + "other"})
    token = auth.issue_temporary_token("eval")["access_token"]
    body = access_tokens._encode(json.dumps({"sub": "other", "iat": 0, "exp": 9999999999, "jti": "fake"}).encode())
    invalids = [token + "x", token.rsplit(".", 1)[0], f"{access_tokens.PREFIX}.{body}.{token.split('.')[-1]}",
                access_tokens.PREFIX + ".invalid.not-a-signature", access_tokens.PREFIX + "." + "x" * 3000]
    for invalid in invalids:
        with pytest.raises(FinanceApiAuthError):
            auth.authenticate(x_api_key=invalid)
    for rotated in [FinanceApiKeyAuth({"eval": KEY + "rotated"}), FinanceApiKeyAuth({"other": KEY})]:
        with pytest.raises(FinanceApiAuthError):
            rotated.authenticate(x_api_key=token)
    with pytest.raises(ValueError, match="principal"):
        auth.issue_temporary_token()


@pytest.mark.parametrize("ttl", [0, -1, True, 1.2, 2**31])
def test_invalid_ttl(ttl):
    with pytest.raises(ValueError):
        FinanceApiKeyAuth({"eval": KEY}).issue_temporary_token(ttl_seconds=ttl)


def test_cli_secret_file_permissions_no_overwrite_no_secret_logs(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("FINANCE_API_KEYS_JSON", raising=False)
    monkeypatch.setenv("FINANCE_API_KEY", KEY)
    monkeypatch.setenv("FINANCE_API_KEY_ID", "eval")
    output = tmp_path / "credential.json"
    assert main(["--output", str(output)]) == 0
    data = json.loads(output.read_text())
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert data["expires_in"] == 14400
    logs = capsys.readouterr()
    assert KEY not in logs.out + logs.err
    assert data["access_token"] not in logs.out + logs.err
    before = output.read_bytes()
    with pytest.raises(SystemExit):
        main(["--output", str(output)])
    assert output.read_bytes() == before


@pytest.mark.anyio
async def test_real_mcp_auth_middleware_rejects_expired_token_before_gateway(monkeypatch, tmp_path):
    from src.finance_api.app import create_app
    from src.finance_api.models import FinanceQueryResponse
    monkeypatch.setenv("FINANCE_API_ALLOWED_HOSTS", "testserver")
    monkeypatch.setenv("FINANCE_API_ROOT_PATH", "")

    class Gateway:
        calls = []

        def list_skills(self, *, principal_id):
            return {"skills": [], "revision": "test"}

        async def execute(self, request, *, principal_id, request_channel):
            self.calls.append((request, principal_id, request_channel))
            return FinanceQueryResponse.model_validate({
                "id": "fq_test", "created_at": "2026-09-08T00:00:00Z", "ok": True,
                "query": request.query, "runtime": "dsh", "response_mode": request.response_mode,
                "summary": "测试摘要" if request.response_mode == "both" else None,
                "data": {"format": "row-dict", "results": []}, "execution": {},
            })

    auth = FinanceApiKeyAuth({"eval": KEY})
    token = auth.issue_temporary_token(ttl_seconds=60)["access_token"]
    gateway = Gateway()
    app = create_app(auth=auth, gateway=gateway)
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "finance_data_query", "arguments": {"query": "测试", "runtime": "dsh", "response_mode": "data"}}}
    async with app.router.lifespan_context(app):
        from scripts.eval_finance_mcp import evaluate, load_cases
        records, _ = await evaluate(load_cases(queries=["测试data"]), url="http://testserver/mcp",
            token=auth.issue_temporary_token()["access_token"], output_dir=tmp_path / "data",
            transport=httpx.ASGITransport(app=app))
        assert records[0]["response"]["ok"] is True and not records[0]["problems"]
        records, _ = await evaluate(load_cases(queries=["测试both"]), url="http://testserver/mcp",
            token=auth.issue_temporary_token()["access_token"], output_dir=tmp_path / "both", response_mode="both",
            transport=httpx.ASGITransport(app=app))
        assert records[0]["response"]["summary"] == "测试摘要"
        assert all(call[0].conversation_id is None for call in gateway.calls)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            assert (await client.post("/mcp", json=call)).status_code == 401
            headers = {"Authorization": "Bearer " + token, "Accept": "application/json, text/event-stream"}
            result = await client.post("/mcp", json=call, headers=headers)
            assert result.status_code == 200
            assert result.json()["result"]["structuredContent"]["ok"] is True
            assert gateway.calls[0][1:] == ("eval", "mcp")
            issued = auth.issue_temporary_token(ttl_seconds=1)
            monkeypatch.setattr(access_tokens.time, "time", lambda: issued["expires_at"])
            headers["Authorization"] = "Bearer " + issued["access_token"]
            assert (await client.post("/mcp", json=call, headers=headers)).status_code == 401
            assert len(gateway.calls) == 3
