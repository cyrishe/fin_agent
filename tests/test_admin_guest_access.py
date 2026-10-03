import pytest
from fastapi.testclient import TestClient

from src.finance_api.app import create_app
from src.finance_api.auth import FinanceApiKeyAuth
from src.services.user_session_service import UserSessionService
from src.web import flask_app as web


@pytest.mark.parametrize("kind,status", [(None, 401), ("member", 403), ("guest", 403), ("admin", 422)])
def test_usage_requires_database_admin_not_api_key(monkeypatch, kind, status):
    monkeypatch.setattr(UserSessionService, "resolve_member_session", lambda self, **kw: {"user_type": kind} if kind else None)
    app = create_app(auth=FinanceApiKeyAuth({"test": "test-finance-api-key-1234567890"}))
    client = TestClient(app)
    client.cookies.set(UserSessionService.MEMBER_SESSION_COOKIE_NAME, "test-session")
    # Invalid days proves only an admin gets past authorization to the handler.
    assert client.get("/v1/usage/daily?days=100", headers={"X-API-Key": "test-finance-api-key-1234567890"}).status_code == status
    page = client.get("/status", follow_redirects=False)
    assert page.status_code == (200 if kind == "admin" else 303 if kind is None else 403)


@pytest.mark.parametrize("path", ["/api/chat/stream/start", "/api/custom_tool/stream/start"])
def test_guest_three_requests_across_threads(monkeypatch, path):
    identity = {"user_id": "guest_quota_test", "user_type": "guest", "session_token": "gs_test"}
    monkeypatch.setattr(web, "_resolve_current_guest_identity", lambda: identity)
    count = []
    def consume(**kw):
        assert kw["user_id"] == identity["user_id"]
        count.append(1)
        return len(count) <= 3
    monkeypatch.setattr(web.user_session_service, "consume_guest_question", consume)
    client = web.app.test_client()
    for n in range(3):
        response = client.post(path, json={"text": "贵州茅台最新行情", "thread_id": n + 1})
        assert response.status_code == 200
        web.custom_tool_stream_requests.pop(response.json["run_id"])
    denied = client.post(path, json={"text": "再查一次", "thread_id": 999})
    assert denied.status_code == 403
    assert denied.json["code"] == "guest_question_limit"


@pytest.mark.parametrize("kind", ["member", "admin"])
def test_members_and_admins_do_not_consume_guest_quota(monkeypatch, kind):
    monkeypatch.setattr(web.user_session_service, "consume_guest_question", lambda **kw: pytest.fail("member consumed guest quota"))
    with web.app.test_request_context():
        assert web._guest_question_denial({"user_type": kind}) is None


def test_guest_cannot_bypass_chat_via_direct_execution(monkeypatch):
    monkeypatch.setattr(web, "_resolve_current_member_identity", lambda: None)
    client = web.app.test_client()
    for path in ["/api/tools/test/run", "/api/skills/test/jobs", "/api/router/submit"]:
        response = client.post(path, json={})
        assert response.status_code == 403
        assert response.json["code"] == "login_required"


def test_sync_chat_also_checks_quota(monkeypatch):
    monkeypatch.setattr(web.runtime_conversation_service, "ensure_thread", lambda **kw: 1)
    monkeypatch.setattr(web, "_resolve_current_guest_identity", lambda: {"user_id": "guest_test", "user_type": "guest"})
    monkeypatch.setattr(web.attachment_service, "list_attachments", lambda *a, **kw: [])
    monkeypatch.setattr(web.user_session_service, "consume_guest_question", lambda **kw: False)
    response = web.app.test_client().post("/api/chat/dispatch", json={"text": "最新行情"})
    assert response.status_code == 403
    assert response.json["code"] == "guest_question_limit"
