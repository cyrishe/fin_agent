from pathlib import Path

from src.services.file_artifact_service import FileArtifactService
from src.services.runtime_execution_service import RuntimeExecutionService
from src.services.session_variable_store_service import SessionVariableStoreService


def test_session_variable_store_registers_finance_table_result(tmp_path: Path) -> None:
    store = SessionVariableStoreService(data_root=tmp_path / "data")
    result = {
        "tool": "finance_data_query",
        "ok": True,
        "data": {
            "result": {
                "name": "r1",
                "api": "stock.basic_info",
                "columns": ["code", "name"],
                "data": {
                    "rows": [{"code": "600519", "name": "贵州茅台"}],
                    "row_count": 1,
                },
            }
        },
    }

    variable = store.register_tool_result(
        session_id="conv-1",
        tool_name="finance_data_query",
        task="查询贵州茅台",
        result=result,
    )

    assert variable is not None
    assert variable["var_id"] == "v1"
    assert variable["local_alias"] == "r1"
    assert variable["schema"]["columns"] == [{"name": "code", "type": "string"}, {"name": "name", "type": "string"}]
    assert variable["sample"]["rows"] == [{"code": "600519", "name": "贵州茅台"}]
    assert variable["data_ref"] == "session://conv-1/vars/v1"

    manifest, rows = FileArtifactService(data_root=tmp_path / "data").read_table_preview(variable["artifact_ref"])
    assert manifest["created_by_tool"] == "finance_data_query"
    assert rows == [{"code": "600519", "name": "贵州茅台"}]
    resolved = store.resolve_data_ref(variable["data_ref"])
    assert resolved["artifact_ref"] == variable["artifact_ref"]
    assert resolved["schema"]["columns"][0]["name"] == "code"
    assert "## v1" in store.format_variables_for_prompt(session_id="conv-1")


def test_runtime_execution_attaches_session_variable_for_table_result(tmp_path: Path) -> None:
    store = SessionVariableStoreService(data_root=tmp_path / "data")
    runtime = RuntimeExecutionService(session_variable_store=store)

    result = runtime.execute_tool(
        tool_name="demo_table_tool",
        args={"_runtime": {"conversation_id": "conv-table"}, "query": "demo"},
        executor=lambda args: {
            "tool": "demo_table_tool",
            "ok": True,
            "data": {
                "data": {
                    "header": ["name", "score"],
                    "rows": [["A", 1], ["B", 2]],
                }
            },
            "error": "",
        },
    )

    variable = result["meta"]["session_variable"]
    assert variable["var_id"] == "v1"
    assert variable["tool_name"] == "demo_table_tool"
    assert variable["sample"]["rows"] == [{"name": "A", "score": 1}, {"name": "B", "score": 2}]
    assert store.list_variables(session_id="conv-table")[0]["data_ref"] == "session://conv-table/vars/v1"


def test_runtime_session_variable_registration_failure_does_not_fail_tool() -> None:
    class FailingStore:
        def register_tool_result(self, **kwargs):  # noqa: ANN001, ANN003 - tiny test double.
            raise RuntimeError("store unavailable")

    runtime = RuntimeExecutionService(session_variable_store=FailingStore())

    result = runtime.execute_tool(
        tool_name="demo_tool",
        args={"_runtime": {"conversation_id": "conv-fail"}},
        executor=lambda args: {"tool": "demo_tool", "ok": True, "data": {"value": 1}, "error": ""},
    )

    assert result["ok"] is True
    assert result["data"] == {"value": 1}
    assert result["meta"]["session_variable_error"] == "store unavailable"


def test_session_variable_store_keeps_full_non_table_result_outside_prompt_summary(tmp_path: Path) -> None:
    store = SessionVariableStoreService(data_root=tmp_path / "data")
    large_text = "x" * 250_000
    variable = store.register_tool_result(
        session_id="owner/thread-1",
        tool_name="demo_object_tool",
        result={"ok": True, "data": {"payload": large_text}},
    )

    assert variable is not None
    assert large_text not in store.format_variables_for_prompt(session_id="owner/thread-1")
    loaded = store.load_data_ref(
        session_id="owner/thread-1",
        data_ref=variable["data_ref"],
        offset=249_900,
        limit=200,
    )
    assert loaded["page"]["total"] > 250_000
    assert "x" * 50 in loaded["text"]
