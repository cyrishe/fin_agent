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
