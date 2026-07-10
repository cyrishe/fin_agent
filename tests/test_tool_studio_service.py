import json
from pathlib import Path

from src.services.finance_data_tool_catalog_service import FinanceDataToolCatalogService
from src.services.tool_studio_service import ToolStudioService


class _RuntimeArtifactSyncStub:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.calls = []

    def sync_tool(self, tool_name, *, source_type, changed_by):
        self.calls.append({"tool_name": tool_name, "source_type": source_type, "changed_by": changed_by})
        if self.fail:
            raise RuntimeError("db offline")
        return {"artifact_type": "tool", "name": tool_name, "artifact_id": 1}


def test_tool_studio_service_hides_disabled_theme_leaders_by_default():
    service = ToolStudioService()
    items = service.list_tools()
    names = [item["tool_name"] for item in items]

    assert "theme_leaders" not in names


def test_tool_studio_service_loads_theme_leaders_bundle():
    service = ToolStudioService()
    bundle = service.load_tool_bundle("theme_leaders")

    assert bundle["tool_name"] == "theme_leaders"
    assert bundle["files"]["definition"]["status"] == "disabled"
    assert bundle["files"]["definition"]["profiles"]["real"]["enabled"] is False
    assert bundle["files"]["tool_hub"] == {}


def test_tool_studio_service_hides_retired_hidden_stock_quote_by_default():
    service = ToolStudioService()
    names = [item["tool_name"] for item in service.list_tools()]

    assert "stock_quote" not in names
    assert "stock_realtime_quote" in names


def test_tool_studio_service_hides_retired_hidden_stock_funds_by_default():
    service = ToolStudioService()
    names = [item["tool_name"] for item in service.list_tools()]

    assert "stock_funds" not in names
    assert "stock_realtime_funds_flow" not in names
    assert "stock_history_funds_flow" not in names
    assert "stock_industry_funds_flow" not in names
    assert "equity_research_search" not in names


def test_tool_studio_service_loads_finance_data_catalog_tree():
    service = ToolStudioService()
    payload = service.load_finance_data_catalog(subject="stock", dataview="margin")

    assert payload["mode"] == "dataview"
    assert payload["subject"] == "stock"
    assert payload["dataview"]["name"] == "margin"
    assert any(item["api_name"] == "stock.margin" for item in payload["dataview"]["functions"])


def test_tool_studio_service_saves_one_finance_catalog_path(tmp_path: Path):
    source = Path(FinanceDataToolCatalogService.DEFAULT_CATALOG_PATH)
    catalog_path = tmp_path / "api_view_catalog.json"
    catalog_path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    service = ToolStudioService(finance_data_catalog_path=str(catalog_path))

    result = service.save_finance_data_catalog_node(
        node_type="",
        subject="",
        path=["subjects", "stock", "quote", "api", 0],
        node={"api_function": "updated quote function"},
    )

    assert result["mode"] == "path"
    saved = json.loads(catalog_path.read_text(encoding="utf-8"))
    function = saved["subjects"]["stock"]["quote"]["api"][0]
    assert function["api_function"] == "updated quote function"
    assert function["api_name"] == "stock.quote"


def test_tool_studio_service_save_roundtrip_updates_all_design_files(tmp_path):
    service = ToolStudioService(
        definitions_dir=str(tmp_path / "definitions"),
        schemas_dir=str(tmp_path / "schemas"),
        specs_dir=str(tmp_path / "specs"),
        tool_hub_path=str(tmp_path / "tool_hub.json"),
    )
    runtime_sync = _RuntimeArtifactSyncStub()
    service.runtime_artifacts = runtime_sync

    bundle = service.build_tool_template_bundle("demo_tool")
    definition = bundle["files"]["definition"]
    definition["status"] = "active"
    definition["identity"]["description"] = "updated description"
    definition["schemas"]["output"] = {"$ref": "src/tools/schemas/shared.schema.json"}
    output_schema = bundle["files"]["output_schema"]
    output_schema["properties"]["data"] = {"type": "object", "properties": {"answer": {"type": "string"}}}
    tool_spec = bundle["files"]["tool_spec"]
    tool_spec["purpose"] = "updated purpose"
    tool_spec["input_guidance"]["notes"] = ["use query as the user-facing search text"]
    tool_hub = bundle["files"]["tool_hub"]
    tool_hub["description"] = "updated hub description"

    saved = service.save_tool_bundle(
        tool_name="demo_tool",
        definition_text=bundle_json(definition),
        output_schema_text=bundle_json(output_schema),
        tool_spec_text=bundle_json(tool_spec),
        tool_hub_text=bundle_json(tool_hub),
    )

    assert saved["files"]["definition"]["identity"]["description"] == "updated description"
    assert saved["files"]["definition"]["schemas"]["output"] == {"$ref": "src/tools/schemas/demo_tool.schema.json"}
    assert saved["files"]["tool_spec"]["input_guidance"]["notes"] == ["use query as the user-facing search text"]
    assert saved["files"]["tool_hub"]["description"] == "updated hub description"
    assert saved["meta"]["runtime_artifact_sync"]["status"] == "synced"
    assert runtime_sync.calls == [{"tool_name": "demo_tool", "source_type": "ui", "changed_by": "tool_studio"}]

    reloaded = service.load_tool_bundle("demo_tool")
    assert reloaded["files"]["definition"]["identity"]["description"] == "updated description"
    assert reloaded["files"]["output_schema"]["properties"]["data"]["properties"]["answer"]["type"] == "string"
    assert reloaded["files"]["tool_spec"]["purpose"] == "updated purpose"
    assert reloaded["files"]["tool_hub"]["description"] == "updated hub description"


def test_tool_studio_service_save_keeps_files_when_runtime_artifact_sync_fails(tmp_path):
    service = ToolStudioService(
        definitions_dir=str(tmp_path / "definitions"),
        schemas_dir=str(tmp_path / "schemas"),
        specs_dir=str(tmp_path / "specs"),
        tool_hub_path=str(tmp_path / "tool_hub.json"),
    )
    service.runtime_artifacts = _RuntimeArtifactSyncStub(fail=True)
    bundle = service.build_tool_template_bundle("demo_tool")
    definition = bundle["files"]["definition"]
    definition["identity"]["description"] = "saved even if db sync fails"

    saved = service.save_tool_bundle(
        tool_name="demo_tool",
        definition_text=bundle_json(definition),
        output_schema_text=bundle_json(bundle["files"]["output_schema"]),
        tool_spec_text=bundle_json(bundle["files"]["tool_spec"]),
        tool_hub_text=bundle_json(bundle["files"]["tool_hub"]),
    )

    assert saved["meta"]["runtime_artifact_sync"]["status"] == "failed"
    assert "RuntimeError: db offline" in saved["meta"]["runtime_artifact_sync"]["error"]
    assert service.load_tool_bundle("demo_tool")["files"]["definition"]["identity"]["description"] == "saved even if db sync fails"


def bundle_json(payload):
    import json

    return json.dumps(payload, ensure_ascii=False, indent=2)
