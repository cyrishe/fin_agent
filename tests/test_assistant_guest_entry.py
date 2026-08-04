from pathlib import Path
import datetime

from src.services.user_session_service import UserSessionStorageError
from src.services.runtime_conversation_service import RuntimeConversationAccessError
from src.web import flask_app as web


def test_guest_user_ids_are_random_not_browser_fingerprints() -> None:
    first = web.UserSessionService.build_guest_user_id(
        user_agent="same-browser",
        remote_addr="127.0.0.1",
    )
    second = web.UserSessionService.build_guest_user_id(
        user_agent="same-browser",
        remote_addr="127.0.0.1",
    )

    assert first.startswith("guest_")
    assert second.startswith("guest_")
    assert first != second


def test_turn_meta_measures_from_request_entry_instead_of_internal_turn_start() -> None:
    started_at = datetime.datetime.now() - datetime.timedelta(seconds=3.25)

    meta = web._turn_meta(started_at)

    assert meta["started_at"].startswith(started_at.strftime("%Y-%m-%dT%H:%M:%S"))
    assert 3_200 <= meta["duration_ms"] <= 3_500


def test_assistant_transparently_bootstraps_default_guest_without_login(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        web,
        "_resolve_current_guest_identity",
        lambda: {
            "user_id": "guest_default_test",
            "user_type": "guest",
            "session_token": "gs_default_test",
        },
    )
    monkeypatch.setattr(
        web,
        "_ui_application_context",
        lambda _name: {
            "application_name": "investment_workbench",
            "display_name": "Investment Workbench",
            "assistant_intro": "可以直接输入自然语言问题。",
            "workspace": {},
        },
    )
    monkeypatch.setattr(web, "REACT_FRONTEND_DIST_DIR", tmp_path / "missing-react-dist")

    client = web.app.test_client()
    response = client.get("/assistant", follow_redirects=False)

    assert response.status_code == 200
    assert response.headers.get("Location") is None
    cookies = response.headers.getlist("Set-Cookie")
    assert any(cookie.startswith("aiia_guest_user_id=guest_default_test") for cookie in cookies)
    assert any(cookie.startswith("aiia_guest_session_token=gs_default_test") for cookie in cookies)
    assert b'id="messages"' in response.data


def test_assistant_uses_react_build_when_available(monkeypatch, tmp_path) -> None:
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text('<div id="root"></div><script src="/assistant/assets/app.js"></script>', encoding="utf-8")
    (assets / "app.js").write_text("window.finAgentReact = true;", encoding="utf-8")
    monkeypatch.setattr(web, "REACT_FRONTEND_DIST_DIR", dist)
    monkeypatch.setattr(
        web,
        "_resolve_current_guest_identity",
        lambda: {"user_id": "guest_react_test", "session_token": "gs_react_test"},
    )

    client = web.app.test_client()
    page = client.get("/assistant")
    asset = client.get("/assistant/assets/app.js")

    assert page.status_code == 200
    assert b'id="root"' in page.data
    assert asset.status_code == 200
    assert b"finAgentReact" in asset.data
    assert any(cookie.startswith("aiia_guest_user_id=guest_react_test") for cookie in page.headers.getlist("Set-Cookie"))


def test_react_thread_bootstrap_returns_active_thread_and_guest_cookies(monkeypatch) -> None:
    monkeypatch.setattr(
        web,
        "_resolve_current_guest_identity",
        lambda: {"user_id": "guest_threads_test", "session_token": "gs_threads_test"},
    )
    monkeypatch.setattr(
        web.runtime_conversation_service,
        "list_threads",
        lambda **_kwargs: [{"thread_id": 12, "title": "测试会话"}],
    )
    client = web.app.test_client()
    client.set_cookie(web.UserSessionService.THREAD_COOKIE_NAME, "12")

    response = client.get("/api/assistant/threads")

    assert response.status_code == 200
    assert response.get_json()["active_thread_id"] == 12
    cookies = response.headers.getlist("Set-Cookie")
    assert any(cookie.startswith("aiia_guest_user_id=guest_threads_test") for cookie in cookies)
    assert any(cookie.startswith("aiia_guest_session_token=gs_threads_test") for cookie in cookies)


def test_existing_guest_cookies_are_reused_only_after_server_validation(monkeypatch) -> None:
    def unexpected_create(**_kwargs):
        raise AssertionError("existing guest cookies should be read without another database write")

    monkeypatch.setattr(web.user_session_service, "resolve_or_create_guest", unexpected_create)
    monkeypatch.setattr(
        web.user_session_service,
        "resolve_guest_session",
        lambda **kwargs: {
            "user_id": kwargs["user_id"],
            "user_type": "guest",
            "session_token": kwargs["session_token"],
        },
    )
    client = web.app.test_client()
    client.set_cookie(web.UserSessionService.GUEST_COOKIE_NAME, "guest_existing")
    client.set_cookie("aiia_guest_session_token", "gs_existing")

    with client.application.test_request_context(
        "/api/assistant/threads",
        headers={"Cookie": "aiia_guest_user_id=guest_existing; aiia_guest_session_token=gs_existing"},
    ):
        identity = web._resolve_current_guest_identity()

    assert identity == {
        "user_id": "guest_existing",
        "user_type": "guest",
        "session_token": "gs_existing",
    }


def test_forged_guest_cookies_are_replaced_with_a_new_server_session(monkeypatch) -> None:
    monkeypatch.setattr(web.user_session_service, "resolve_guest_session", lambda **_kwargs: None)
    monkeypatch.setattr(
        web.user_session_service,
        "resolve_or_create_guest",
        lambda **kwargs: {
            "user_id": "guest_replacement",
            "user_type": "guest",
            "session_token": "gs_replacement",
            "received_cookie_user_id": kwargs["cookie_user_id"],
            "received_cookie_session_token": kwargs["cookie_session_token"],
        },
    )
    client = web.app.test_client()
    client.set_cookie(web.UserSessionService.GUEST_COOKIE_NAME, "guest_forged")
    client.set_cookie(web.UserSessionService.GUEST_SESSION_COOKIE_NAME, "gs_forged")

    with client.application.test_request_context(
        "/api/assistant/threads",
        headers={"Cookie": "aiia_guest_user_id=guest_forged; aiia_guest_session_token=gs_forged"},
    ):
        identity = web._resolve_current_guest_identity()

    assert identity["user_id"] == "guest_replacement"
    assert identity["received_cookie_user_id"] == ""
    assert identity["received_cookie_session_token"] == ""


def test_member_storage_failure_never_falls_back_to_guest(monkeypatch) -> None:
    def unavailable_member_session(**_kwargs):
        raise UserSessionStorageError("database unavailable")

    monkeypatch.setattr(
        web.user_session_service,
        "resolve_member_session",
        unavailable_member_session,
    )

    def unexpected_guest(**_kwargs):
        raise AssertionError("member requests must fail closed, not become guest")

    monkeypatch.setattr(web.user_session_service, "resolve_or_create_guest", unexpected_guest)
    client = web.app.test_client()
    client.set_cookie(web.UserSessionService.MEMBER_SESSION_COOKIE_NAME, "ms_existing")

    response = client.get("/api/assistant/threads")

    assert response.status_code == 503
    assert response.get_json()["code"] == "identity_storage_unavailable"


def test_thread_history_retains_react_renderable_legacy_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        web,
        "_resolve_current_guest_identity",
        lambda: {"user_id": "guest_history_test", "session_token": "gs_history_test"},
    )
    monkeypatch.setattr(
        web.runtime_conversation_service,
        "get_thread",
        lambda **_kwargs: {"thread_id": 23, "owner_type": "user", "owner_id": "guest_history_test"},
    )
    monkeypatch.setattr(web.runtime_conversation_service, "get_thread_context", lambda **_kwargs: {})
    list_turn_calls = []
    monkeypatch.setattr(
        web.runtime_conversation_service,
        "list_turns",
        lambda **kwargs: list_turn_calls.append(kwargs) or [{
            "turn_id": 1,
            "output_payload": {
                "mode": "tools_catalog",
                "message": "当前共有 1 个 tools。",
                "items": [{"tool_name": "finance_data_query"}],
                "workspace": {"url": "/tools"},
                "diagnostic_trace": {"path": "/private/trace"},
            },
        }],
    )

    response = web.app.test_client().get("/api/assistant/threads/23")
    output = response.get_json()["turns"][0]["output_payload"]

    assert response.status_code == 200
    assert output["items"] == [{"tool_name": "finance_data_query"}]
    assert output["workspace"] == {"url": "/tools"}
    assert "diagnostic_trace" not in output
    assert list_turn_calls[0]["history_payload_only"] is True


def test_chat_dispatch_rejects_foreign_thread_before_creating_turn(monkeypatch) -> None:
    monkeypatch.setattr(
        web,
        "_resolve_current_guest_identity",
        lambda: {
            "user_id": "guest_b",
            "user_type": "guest",
            "session_token": "gs_b",
        },
    )
    monkeypatch.setattr(
        web.attachment_service,
        "list_attachments",
        lambda *_args, **_kwargs: [],
    )

    def reject_foreign_thread(**kwargs):
        assert kwargs["thread_id"] == 7
        assert kwargs["owner_id"] == "guest_b"
        raise RuntimeConversationAccessError("无权访问该 thread")

    monkeypatch.setattr(
        web.runtime_conversation_service,
        "ensure_thread",
        reject_foreign_thread,
    )
    monkeypatch.setattr(
        web.runtime_conversation_service,
        "create_turn",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("foreign thread must fail before create_turn")
        ),
    )

    response = web.app.test_client().post(
        "/api/chat/dispatch",
        json={"text": "贵州茅台现在多少钱？", "thread_id": 7},
    )

    assert response.status_code == 403
    assert response.get_json() == {"ok": False, "error": "无权访问该 thread"}


def test_assistant_sidebar_uses_compact_titles_without_rendering_message_content() -> None:
    template = Path("src/web/templates/conversation_workbench.html").read_text(encoding="utf-8")

    assert "compactSessionTitle(item)" in template
    assert "formatSessionTime(item.last_event_at || item.updated_at" in template
    assert '<div class="session-snippet">' not in template
    assert "characters.slice(0, 18)" in template
