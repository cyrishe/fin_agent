import json
from importlib import import_module
from pathlib import Path
from typing import Any, Dict

from src.services.runtime_execution_service import RuntimeExecutionService
from src.services.custom_tool_service import CustomToolRuntimeService, CustomToolStoreService
from src.tools.http_tool_runner import is_http_tool, run_http_tool


TOOL_REGISTRY: Dict[str, str] = {
    "equity_research_search": "src.tools.stock_reports_tool:run",
    "stock_funds": "src.tools.stock_funds_tool:run",
    "stock_realtime_funds_flow": "src.tools.stock_funds_split_tools:run_realtime_funds_flow",
    "stock_history_funds_flow": "src.tools.stock_funds_split_tools:run_history_funds_flow",
    "stock_industry_funds_flow": "src.tools.stock_funds_split_tools:run_industry_funds_flow",
    "capital_flow_query": "src.tools.capital_flow_query_tool:run",
    "stock_capital_flow_query": "src.tools.stock_capital_flow_query_tool:run",
    "plate_capital_flow_query": "src.tools.plate_capital_flow_query_tool:run",
    "stock_quote": "src.tools.stock_quote_tool:run",
    "stock_realtime_quote": "src.tools.stock_realtime_quote_tool:run",
    "stock_history_kline": "src.tools.stock_quote_tool:run_history_kline",
    "stock_intraday_kline": "src.tools.stock_quote_tool:run_intraday_kline",
    "stock_daily_kline_query": "src.tools.stock_daily_kline_query_tool:run",
    "stock_intraday_kline_query": "src.tools.stock_intraday_kline_query_tool:run",
    "stock_valuation_query": "src.tools.stock_valuation_query_tool:run",
    "stock_fundamental_snapshot": "src.tools.stock_fundamental_snapshot_tool:run",
    "stock_financial_statement_query": "src.tools.stock_financial_statement_query_tool:run",
    "fund_daily_market_query": "src.tools.fund_daily_market_query_tool:run",
    "fund_profile_query": "src.tools.fund_profile_query_tool:run",
    "index_daily_market_query": "src.tools.index_daily_market_query_tool:run",
    "stock_announcement_query": "src.tools.stock_announcement_query_tool:run",
    "finance_data_query": "src.tools.finance_data_query_tool:run",
    "stock_protocol_data_query": "src.tools.stock_protocol_data_query_tool:run",
    "security_universe_query": "src.tools.security_universe_query_tool:run",
    "financial_news_search": "src.tools.company_news_tool:run",
    "market_realtime_breadth": "src.tools.market_snapshot_tools:run_market_realtime_breadth",
    "market_history_amount": "src.tools.market_snapshot_tools:run_market_history_amount",
    "market_minute_amount_series": "src.tools.market_snapshot_tools:run_market_minute_amount_series",
    "get_hot_industries_and_leaders": "src.tools.mock_market_taxonomy_tools:get_hot_industries_and_leaders",
    "get_hot_sectors_and_leaders": "src.tools.mock_market_taxonomy_tools:get_hot_sectors_and_leaders",
    "get_hot_concepts_and_leaders": "src.tools.mock_market_taxonomy_tools:get_hot_concepts_and_leaders",
    "plate_rank_query": "src.tools.plate_rank_query_tool:run",
    "plate_member_query": "src.tools.plate_member_query_tool:run",
    "plate_members_query": "src.tools.plate_members_query_tool:run",
    "stock_plate_membership_query": "src.tools.stock_plate_membership_query_tool:run",
    "theme_leaders": "src.tools.theme_leaders_tool:run",
    "get_company_taxonomy_profile": "src.tools.company_taxonomy_profile_tool:run",
    "大盘情绪指标": "src.tools.market_sentiment_indicator_tool:run",
    "大盘整体情况": "src.tools.market_overview_tool:run",
    "indicator_series_query": "src.tools.indicator_series_tool:run",
    "个股动量排名": "src.tools.stock_momentum_ranking_tool:run_unified",
    "实时个股动量排名": "src.tools.stock_momentum_ranking_tool:run",
    "实时行情排名查询": "src.tools.realtime_market_ranking_tool:run",
    "涨跌停列表查询": "src.tools.limit_board_list_tool:run",
    "file_read_excel": "src.tools.file_intake_tools:run_excel",
    "file_read_csv": "src.tools.file_intake_tools:run_csv",
    "file_read_word": "src.tools.file_intake_tools:run_word",
    "file_io": "src.tools.file_io_tool:run",
    "quant_data_provider": "src.tools.quant_data_provider_tool:run",
    "quant_factor_screening": "src.tools.quant_factor_screening_tool:run",
}

TOOL_ALIASES: Dict[str, str] = {
    "公司研报查询": "equity_research_search",
    "stock_reports": "equity_research_search",
    "company_news": "financial_news_search",
    "指标序列查询": "indicator_series_query",
}

_runtime_execution_service = RuntimeExecutionService()
_TOOL_DEFINITIONS_DIR = Path("src/tools/definitions")
_INTERNAL_COMPAT_ALLOWED_FIELDS: Dict[str, set[str]] = {
    "equity_research_search": {"since_time", "limit", "refresh", "persist", "dedupe"},
}
_DISABLED_TOOL_STATUSES = {"disabled", "archived", "retired", "deprecated"}


def _load_tool_definition(tool_name: str) -> Dict[str, Any]:
    canonical_name = canonicalize_tool_name(tool_name)
    path = _TOOL_DEFINITIONS_DIR / f"{canonical_name}.tool.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def is_tool_definition_disabled(tool_name: str) -> bool:
    definition = _load_tool_definition(tool_name)
    if not definition:
        return False
    if definition.get("enabled") is False:
        return True
    status = str(definition.get("status") or "").strip().lower()
    return status in _DISABLED_TOOL_STATUSES


def _get_tool_input_contract(tool_name: str) -> Dict[str, Any]:
    definition = _load_tool_definition(tool_name)
    schemas = definition.get("schemas") if isinstance(definition.get("schemas"), dict) else {}
    input_schema = schemas.get("input") if isinstance(schemas.get("input"), dict) else {}
    if not input_schema and CustomToolStoreService().exists(tool_name):
        try:
            bundle = CustomToolStoreService().load(tool_name)
            input_schema = bundle.get("input_schema") if isinstance(bundle.get("input_schema"), dict) else {}
        except Exception:
            input_schema = {}
    properties = input_schema.get("properties") if isinstance(input_schema.get("properties"), dict) else {}
    defaults = definition.get("defaults") if isinstance(definition.get("defaults"), dict) else {}
    required_fields = [
        str(item).strip()
        for item in (input_schema.get("required") or [])
        if str(item).strip()
    ]
    return {
        "properties": properties,
        "allowed_fields": set(properties.keys()),
        "required_fields": required_fields,
        "defaults": defaults,
    }


def _apply_tool_argument_aliases(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(args or {})
    tool = canonicalize_tool_name(tool_name)
    stock_tools = {
        "stock_daily_kline_query",
        "stock_intraday_kline_query",
        "stock_realtime_quote",
        "stock_valuation_query",
        "stock_capital_flow_query",
        "stock_fundamental_snapshot",
        "stock_financial_statement_query",
        "stock_announcement_query",
        "stock_plate_membership_query",
    }
    if tool in stock_tools and "stock" not in normalized:
        for legacy_key in ("subject", "stock", "stock_code", "code", "name", "query"):
            legacy_value = str(normalized.get(legacy_key) or "").strip()
            if legacy_value:
                normalized["stock"] = legacy_value
                break
    if tool in {"fund_daily_market_query", "fund_profile_query"} and "fund" not in normalized:
        for legacy_key in ("subject", "fund", "fund_code", "fund_name", "code", "name", "query"):
            legacy_value = str(normalized.get(legacy_key) or "").strip()
            if legacy_value:
                normalized["fund"] = legacy_value
                break
    if tool == "index_daily_market_query" and "index" not in normalized:
        for legacy_key in ("subject", "index", "index_code", "idx_code", "index_name", "code", "name", "query"):
            legacy_value = str(normalized.get(legacy_key) or "").strip()
            if legacy_value:
                normalized["index"] = legacy_value
                break
    if tool in {"plate_members_query", "plate_rank_query"} and "plate" not in normalized:
        for legacy_key in ("subject", "plate", "plate_code", "plate_name", "name", "query"):
            legacy_value = str(normalized.get(legacy_key) or "").strip()
            if legacy_value:
                normalized["plate"] = legacy_value
                break
    if tool == "get_company_taxonomy_profile" and "stock" not in normalized:
        for legacy_key in ("query", "company", "subject", "stock", "code", "name"):
            legacy_value = str(normalized.get(legacy_key) or "").strip()
            if legacy_value:
                normalized["stock"] = legacy_value
                break
        if "as_of" not in normalized and str(normalized.get("query_date") or "").strip():
            normalized["as_of"] = str(normalized.get("query_date") or "").strip()
    if tool == "financial_news_search":
        if "query" not in normalized and str(normalized.get("keyword") or "").strip():
            normalized["query"] = normalized.get("keyword")
    if tool == "equity_research_search":
        if "company" not in normalized:
            for legacy_key in ("company", "query", "code", "stock_name", "stock_code", "name"):
                legacy_value = str(normalized.get(legacy_key) or "").strip()
                if legacy_value:
                    normalized["company"] = legacy_value
                    break
        if "since_time" not in normalized:
            for legacy_key in ("begin_date", "start_date"):
                legacy_value = normalized.get(legacy_key)
                if legacy_value not in {None, ""}:
                    normalized["since_time"] = legacy_value
                    break
    if tool == "indicator_series_query":
        if "indicator_ids" not in normalized:
            indicator_ids = []
            for legacy_key in ("indicator_ids", "indicator_names"):
                raw_value = normalized.get(legacy_key)
                if isinstance(raw_value, (list, tuple)):
                    indicator_ids.extend(raw_value)
            if not indicator_ids and str(normalized.get("indicator_name") or "").strip():
                indicator_ids.append(str(normalized.get("indicator_name") or "").strip())
            if indicator_ids:
                normalized["indicator_ids"] = indicator_ids
        if "subject_codes" not in normalized:
            subject_codes = []
            raw_subject_codes = normalized.get("subject_codes")
            if isinstance(raw_subject_codes, (list, tuple)):
                subject_codes.extend(raw_subject_codes)
            for legacy_key in ("subject_code", "subject_name", "subject"):
                legacy_value = str(normalized.get(legacy_key) or "").strip()
                if legacy_value:
                    subject_codes.append(legacy_value)
                    break
            if subject_codes:
                normalized["subject_codes"] = subject_codes
    if tool == "theme_leaders":
        if "query" not in normalized and str(normalized.get("theme_name") or "").strip():
            normalized["query"] = str(normalized.get("theme_name") or "").strip()
    if tool == "实时行情排名查询":
        if "sort_by" not in normalized:
            sort_target = str(normalized.get("sort_target") or normalized.get("ranking_target") or "").strip().lower()
            if sort_target == "volume":
                normalized["sort_by"] = "成交量"
            elif sort_target == "amount":
                normalized["sort_by"] = "成交额"
            elif sort_target in {"rise", "chg_ratio", "change_percent"}:
                normalized["sort_by"] = "涨幅"
            elif sort_target in {"fall", "drop"}:
                normalized["sort_by"] = "跌幅"
    if tool == "涨跌停列表查询":
        if "limit_type" not in normalized:
            filter_condition = str(normalized.get("filter_condition") or normalized.get("board_type") or "").strip().lower()
            if filter_condition in {"limit_down", "down", "跌停"}:
                normalized["limit_type"] = "跌停"
            elif filter_condition:
                normalized["limit_type"] = "涨停"
    return normalized


def normalize_tool_args_for_definition(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    tool_name = canonicalize_tool_name(name)
    runtime_payload = args.get("_runtime") if isinstance(args, dict) and isinstance(args.get("_runtime"), dict) else {}
    raw_args = dict(args or {})
    raw_args.pop("_runtime", None)
    aliased_args = _apply_tool_argument_aliases(tool_name, raw_args)
    contract = _get_tool_input_contract(tool_name)
    allowed_fields = contract.get("allowed_fields") if isinstance(contract.get("allowed_fields"), set) else set()
    compat_allowed_fields = _INTERNAL_COMPAT_ALLOWED_FIELDS.get(tool_name) or set()
    defaults = contract.get("defaults") if isinstance(contract.get("defaults"), dict) else {}
    required_fields = contract.get("required_fields") if isinstance(contract.get("required_fields"), list) else []

    if not allowed_fields:
        normalized_args = dict(aliased_args)
        if runtime_payload:
            normalized_args["_runtime"] = runtime_payload
        return {
            "arguments": normalized_args,
            "dropped_fields": [],
            "missing_required": [],
            "used_defaults": {},
        }

    normalized_args: Dict[str, Any] = {}
    dropped_fields = []
    used_defaults: Dict[str, Any] = {}

    for key, value in aliased_args.items():
        if key in allowed_fields or key in compat_allowed_fields:
            normalized_args[key] = value
        else:
            dropped_fields.append(key)

    for key, value in defaults.items():
        if key in allowed_fields and key not in normalized_args:
            normalized_args[key] = value
            used_defaults[key] = value

    missing_required = [
        field
        for field in required_fields
        if field not in normalized_args or _is_missing_required_value(normalized_args.get(field))
    ]
    if runtime_payload:
        normalized_args["_runtime"] = runtime_payload
    return {
        "arguments": normalized_args,
        "dropped_fields": dropped_fields,
        "missing_required": missing_required,
        "used_defaults": used_defaults,
    }


def _normalize_tool_args(args: Any) -> Dict[str, Any]:
    if isinstance(args, dict):
        return args
    if args is None:
        return {}
    if isinstance(args, str):
        return {"query": args}
    if isinstance(args, (list, tuple)):
        return {"items": list(args)}
    return {"value": args}


def _is_missing_required_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not str(value).strip()
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) == 0
    return False


def canonicalize_tool_name(name: str) -> str:
    raw_name = str(name or "").strip()
    return TOOL_ALIASES.get(raw_name, raw_name)


def list_tools() -> list[str]:
    static_tools = [name for name in TOOL_REGISTRY.keys() if not is_tool_definition_disabled(name)]
    try:
        custom_tools = [str(item.get("tool_name") or "").strip() for item in CustomToolStoreService().list_tools()]
    except Exception:
        custom_tools = []
    return sorted({name for name in [*static_tools, *custom_tools] if name})


def run_tool(
    name: str,
    args: Dict[str, Any],
    *,
    runtime_ctx: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    tool_name = canonicalize_tool_name(name)
    is_remote_http = tool_name not in TOOL_REGISTRY and is_http_tool(tool_name)
    if tool_name not in TOOL_REGISTRY and not is_remote_http:
        if CustomToolStoreService().exists(tool_name):
            normalized_args = _normalize_tool_args(args)
            normalized_args.pop("_runtime", None)
            owner_ids = [
                str(item or "").strip()
                for item in ((runtime_ctx or {}).get("custom_tool_owner_ids") or [])
                if str(item or "").strip()
            ]
            return CustomToolRuntimeService().run(
                tool_name,
                normalized_args,
                owner_ids=owner_ids,
                allow_inactive=False,
            )
        raise KeyError(f"unknown tool: {tool_name}")
    if not is_remote_http and is_tool_definition_disabled(tool_name):
        raise ValueError(f"tool '{tool_name}' is disabled")
    if is_remote_http:
        func = lambda payload: run_http_tool(tool_name, payload)
    else:
        module_path, func_name = TOOL_REGISTRY[tool_name].split(":", 1)
        module = import_module(module_path)
        func = getattr(module, func_name)
    normalized_args = _normalize_tool_args(args)
    contract_normalized = normalize_tool_args_for_definition(tool_name, normalized_args)
    return _runtime_execution_service.execute_tool(
        tool_name=tool_name,
        args=contract_normalized.get("arguments") if isinstance(contract_normalized.get("arguments"), dict) else normalized_args,
        executor=func,
    )
