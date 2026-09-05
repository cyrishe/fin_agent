from types import SimpleNamespace

from src.web import flask_app as web


def _bundle(*, owner_id: str = "user-42") -> dict:
    return {
        "manifest": {
            "tool_name": "ct_demo",
            "display_name": "演示工具",
            "owner_id": owner_id,
            "visibility": "personal",
            "current_revision": 3,
        },
        "input_schema": {
            "type": "object",
            "required": ["stock_codes"],
            "properties": {
                "stock_codes": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string"},
                }
            },
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "required": ["summary"],
            "properties": {"summary": {"type": "string"}},
            "additionalProperties": True,
        },
    }


def test_custom_tool_test_runs_exact_revision_and_returns_process(monkeypatch) -> None:
    captured = {}

    class Store:
        def load_revision(self, tool_name, revision):
            captured["load"] = (tool_name, revision)
            return _bundle()

    class Runtime:
        python_runtime = object()
        max_finance_queries = 4
        finance_runtime = object()

    class TestRuntime:
        def __init__(self, **kwargs):
            captured["runtime_root"] = kwargs["runtime_root"]

        def run_revision(self, tool_name, revision, arguments, *, owner_ids, progress_sink):
            captured["run"] = {
                "tool_name": tool_name,
                "revision": revision,
                "arguments": arguments,
                "owner_ids": owner_ids,
            }
            progress_sink({"level": "info", "message": "批量读取完成", "data": {"rows": 2}})
            return {
                "ok": True,
                "data": {"summary": "完成"},
                "error": "",
                "meta": {
                    "diagnostics": {
                        "finance_query_count": 1,
                        "finance_bridge_rounds": 1,
                        "backend": "local_dev",
                    }
                },
            }

    monkeypatch.setattr(
        web,
        "custom_tool_agent_service",
        SimpleNamespace(store=Store(), runtime=Runtime()),
    )
    monkeypatch.setattr(web, "CustomToolRuntimeService", TestRuntime)
    monkeypatch.setattr(web, "_resolve_current_guest_identity", lambda: {"user_id": "user-42"})

    response = web.app.test_client().post(
        "/api/custom-tools/ct_demo/test",
        json={"revision": 3, "arguments": {"stock_codes": ["600519.SH"]}},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["test"]["status"] == "passed"
    assert payload["test"]["contract"] == {
        "input_valid": True,
        "runtime_ok": True,
        "business_ok": True,
        "output_valid": True,
        "output_error": {},
    }
    assert [item["stage"] for item in payload["test"]["process"]] == [
        "validation",
        "runtime",
        "result",
    ]
    assert captured["load"] == ("ct_demo", 3)
    assert captured["run"]["revision"] == 3
    assert captured["run"]["owner_ids"] == ["user-42"]


def test_custom_tool_test_rejects_invalid_input_before_execution(monkeypatch) -> None:
    class Store:
        def load_revision(self, _tool_name, _revision):
            return _bundle()

    monkeypatch.setattr(
        web,
        "custom_tool_agent_service",
        SimpleNamespace(store=Store(), runtime=SimpleNamespace()),
    )
    monkeypatch.setattr(web, "_resolve_current_guest_identity", lambda: {"user_id": "user-42"})

    response = web.app.test_client().post(
        "/api/custom-tools/ct_demo/test",
        json={"revision": 3, "arguments": {"stock_codes": []}},
    )

    assert response.status_code == 400
    assert response.get_json()["code"] == "arguments_schema_mismatch"


def test_custom_tool_test_enforces_owner_scope(monkeypatch) -> None:
    class Store:
        def load_revision(self, _tool_name, _revision):
            return _bundle(owner_id="another-user")

    monkeypatch.setattr(
        web,
        "custom_tool_agent_service",
        SimpleNamespace(store=Store(), runtime=SimpleNamespace()),
    )
    monkeypatch.setattr(web, "_resolve_current_guest_identity", lambda: {"user_id": "user-42"})

    response = web.app.test_client().post(
        "/api/custom-tools/ct_demo/test",
        json={"revision": 3, "arguments": {"stock_codes": ["600519.SH"]}},
    )

    assert response.status_code == 403
    assert response.get_json()["code"] == "custom_tool_test_forbidden"
