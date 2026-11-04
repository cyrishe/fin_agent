from src.web import flask_app as web


def test_with_script_root_works_without_request_context():
    original_root = web.app.config.get("APPLICATION_ROOT")
    try:
        web.app.config["APPLICATION_ROOT"] = "/"
        with web.app.app_context():
            assert web._with_script_root("/router/studio") == "/router/studio"
    finally:
        web.app.config["APPLICATION_ROOT"] = original_root


def test_with_script_root_uses_forwarded_prefix_in_request_context():
    with web.app.test_request_context(headers={"X-Forwarded-Prefix": "/fin"}):
        assert web._with_script_root("/router/studio") == "/fin/router/studio"
