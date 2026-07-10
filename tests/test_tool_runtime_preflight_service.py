import json
from pathlib import Path

from src.services.active_tool_registry_service import ActiveToolRegistryService
from src.services.tool_runtime_preflight_service import ToolRuntimePreflightService


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _tool_definition(
    name: str,
    *,
    status: str = "active",
    enabled: bool = True,
    target: str | None = None,
) -> dict:
    return {
        "name": name,
        "version": "v1",
        "status": status,
        "enabled": enabled,
        "availability": {
            "lifecycle": "active",
            "retrieval_mode": "retrievable",
            "visibility": "visible",
        },
        "identity": {
            "display_name": name,
            "description": f"{name} description",
            "domain": "stock",
            "owner": "tools",
        },
        "profiles": {
            "real": {
                "enabled": True,
                "implementation": {
                    "kind": "python_callable",
                    "target": target or f"src.tools.{name}_tool:run",
                },
            }
        },
        "schemas": {
            "input": {
                "type": "object",
                "required": ["query"],
                "properties": {"query": {"type": "string"}},
            },
            "output": {"type": "object"},
        },
    }


def _preflight(tmp_path: Path, implementation_targets: dict[str, str], aliases: dict[str, str] | None = None):
    registry = ActiveToolRegistryService(
        definitions_dir=str(tmp_path / "definitions"),
        specs_dir=str(tmp_path / "specs"),
        schemas_dir=str(tmp_path / "schemas"),
        tool_hub_path=str(tmp_path / "tool_hub.json"),
        implementation_targets=implementation_targets,
    )
    return ToolRuntimePreflightService(
        active_tool_registry_service=registry,
        tool_aliases=aliases or {},
    )


def test_tool_runtime_preflight_allows_active_tool_with_required_input(tmp_path: Path) -> None:
    _write_json(tmp_path / "definitions" / "active_quote.tool.json", _tool_definition("active_quote"))
    service = _preflight(tmp_path, {"active_quote": "src.tools.active_quote_tool:run"})

    result = service.validate_tool_call(tool_name="active_quote", arguments={"query": "贵州茅台"})

    assert result["ok"] is True
    assert result["status"] == "ready"
    assert result["tool_name"] == "active_quote"
    assert result["registry"]["sync_status"] == "synced"


def test_tool_runtime_preflight_blocks_missing_required_input(tmp_path: Path) -> None:
    _write_json(tmp_path / "definitions" / "active_quote.tool.json", _tool_definition("active_quote"))
    service = _preflight(tmp_path, {"active_quote": "src.tools.active_quote_tool:run"})

    result = service.validate_tool_call(tool_name="active_quote", arguments={})

    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert result["reason"] == "missing_required_input"
    assert result["details"]["missing_required"] == ["query"]


def test_tool_runtime_preflight_blocks_invalid_enum_input(tmp_path: Path) -> None:
    definition = _tool_definition("plate_rank_query")
    definition["schemas"]["input"] = {
        "type": "object",
        "required": [],
        "properties": {
            "sort_by": {
                "type": "string",
                "enum": ["rise_fall_rate", "amount", "main_net_inflow"],
            }
        },
    }
    _write_json(tmp_path / "definitions" / "plate_rank_query.tool.json", definition)
    service = _preflight(tmp_path, {"plate_rank_query": "src.tools.plate_rank_query_tool:run"})

    result = service.validate_tool_call(tool_name="plate_rank_query", arguments={"sort_by": "turnover"})

    assert result["ok"] is False
    assert result["reason"] == "invalid_argument_enum"
    assert result["details"]["field"] == "sort_by"
    assert result["details"]["allowed_values"] == ["rise_fall_rate", "amount", "main_net_inflow"]


def test_tool_runtime_preflight_blocks_disabled_tool(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "definitions" / "disabled_quote.tool.json",
        _tool_definition("disabled_quote", status="disabled", enabled=False),
    )
    service = _preflight(tmp_path, {"disabled_quote": "src.tools.disabled_quote_tool:run"})

    result = service.validate_tool_call(tool_name="disabled_quote", arguments={"query": "贵州茅台"})

    assert result["ok"] is False
    assert result["reason"] == "tool_not_active"
    assert result["details"]["status"] == "disabled"
    assert result["details"]["enabled"] is False


def test_tool_runtime_preflight_blocks_drifted_tool(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "definitions" / "drift_quote.tool.json",
        _tool_definition("drift_quote", target="src.tools.expected_quote_tool:run"),
    )
    service = _preflight(tmp_path, {"drift_quote": "src.tools.actual_quote_tool:run"})

    result = service.validate_tool_call(tool_name="drift_quote", arguments={"query": "贵州茅台"})

    assert result["ok"] is False
    assert result["reason"] == "tool_not_active"
    assert result["details"]["sync_status"] == "drift_detected"
    assert "implementation_target_mismatch" in result["details"]["sync_errors"]


def test_tool_runtime_preflight_resolves_alias_before_validation(tmp_path: Path) -> None:
    _write_json(tmp_path / "definitions" / "financial_news_search.tool.json", _tool_definition("financial_news_search"))
    service = _preflight(
        tmp_path,
        {"financial_news_search": "src.tools.financial_news_search_tool:run"},
        aliases={"company_news": "financial_news_search"},
    )

    result = service.validate_tool_call(tool_name="company_news", arguments={"query": "贵州茅台"})

    assert result["ok"] is True
    assert result["tool_name"] == "financial_news_search"
