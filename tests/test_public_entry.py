from src.web import flask_app as web


def test_public_and_auth_pages_serve_react_without_creating_guest(monkeypatch, tmp_path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text('<main id="root">public-spa</main>', encoding="utf-8")
    monkeypatch.setattr(web, "REACT_FRONTEND_DIST_DIR", dist)
    monkeypatch.setattr(
        web,
        "_resolve_current_guest_identity",
        lambda: (_ for _ in ()).throw(AssertionError("public pages must not create a guest")),
    )
    client = web.app.test_client()

    for path in ("/", "/login", "/register"):
        response = client.get(path)
        assert response.status_code == 200
        assert b"public-spa" in response.data
        assert not response.headers.getlist("Set-Cookie")


def test_home_redirects_authenticated_member_to_assistant(monkeypatch, tmp_path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text('<main id="root">public-spa</main>', encoding="utf-8")
    monkeypatch.setattr(web, "REACT_FRONTEND_DIST_DIR", dist)
    monkeypatch.setattr(
        web,
        "_resolve_current_member_identity",
        lambda: {"user_id": "member_test", "user_type": "member"},
    )
    client = web.app.test_client()
    client.set_cookie(web.UserSessionService.MEMBER_SESSION_COOKIE_NAME, "member-session")

    response = client.get("/", headers={"X-Forwarded-Prefix": "/fin_agent"})

    assert response.status_code == 302
    assert response.headers["Location"] == "/fin_agent/assistant"


def test_react_build_assets_are_available_outside_assistant_route(monkeypatch, tmp_path) -> None:
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (assets / "app.js").write_text("window.finAgentReact = true;", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg></svg>", encoding="utf-8")
    monkeypatch.setattr(web, "REACT_FRONTEND_DIST_DIR", dist)
    client = web.app.test_client()

    asset = client.get("/assets/app.js")
    favicon = client.get("/favicon.svg")

    assert asset.status_code == 200
    assert b"finAgentReact" in asset.data
    assert favicon.status_code == 200
