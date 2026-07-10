from __future__ import annotations

import re
from dataclasses import replace
from typing import Mapping

from src.experiments.staged_data_protocol.phase2.base_info_provider import execute_base_info_api
from src.experiments.staged_data_protocol.phase2.catalog import resolve_api
from src.experiments.staged_data_protocol.phase2.constitution_provider import (
    execute_constitution_agg_api,
    execute_constitution_api,
    execute_industry_base_info_api,
)
from src.experiments.staged_data_protocol.phase2.dynamic_cal_provider import execute_dynamic_quote_api
from src.experiments.staged_data_protocol.phase2.financial_provider import execute_financial_3_table_api
from src.experiments.staged_data_protocol.phase2.hot_event_provider import execute_hot_event_api
from src.experiments.staged_data_protocol.phase2.intraday_quote_provider import (
    execute_intraday_quote_agg_api,
    execute_intraday_quote_api,
    execute_kd_intraday_quote_api,
)
from src.experiments.staged_data_protocol.phase2.margin_provider import execute_kd_margin_api, execute_margin_api
from src.experiments.staged_data_protocol.phase2.moneyflow_provider import execute_kd_moneyflow_api, execute_moneyflow_api
from src.experiments.staged_data_protocol.phase2.models import ApiCall, ResultHandle
from src.experiments.staged_data_protocol.phase2.pricevalue_provider import execute_kd_pricevalue_api, execute_pricevalue_api
from src.experiments.staged_data_protocol.phase2.quote_provider import execute_kd_quote_api, execute_quote_agg_api, execute_quote_api
from src.experiments.staged_data_protocol.phase2.stock_corporate_provider import execute_stock_corporate_api


REF_RE = re.compile(r"\b(r\d+)\.([A-Za-z_]\w*)\b")


def execute_api_call(call: ApiCall, previous_results: Mapping[str, ResultHandle] | None = None) -> ResultHandle:
    """Stable execution boundary for phase-2 API calls.

    Supported providers return real rows behind the same ResultHandle shape.
    APIs without a provider still return a standard prepared result so the LLM,
    parser, and validator contracts do not change while providers are added.
    """

    previous_results = previous_results or {}
    resolved = resolve_api(call.api)
    call = _materialize_call_refs(call, previous_results, skip_keys={"metric"} if resolved and resolved.get("type") == "agg" else set())
    columns = [_output_column(item) for item in call.outputs]
    if resolved and resolved.get("type") == "base" and resolved.get("dataview") == "quote":
        subject = str(resolved.get("subject") or "")
        if subject == "stock" and _quote_realtime(call.args, resolved):
            data = execute_intraday_quote_api(args=call.args, outputs=call.outputs)
        else:
            data = execute_quote_api(subject=subject, args=call.args, outputs=call.outputs)
        return ResultHandle(
            name=call.result_id,
            api=call.api,
            columns=data.get("columns", columns),
            data=data,
        )
    if resolved and resolved.get("type") == "base" and resolved.get("dataview") == "moneyflow":
        data = execute_moneyflow_api(
            subject=str(resolved.get("subject") or ""),
            args=call.args,
            outputs=call.outputs,
        )
        return ResultHandle(
            name=call.result_id,
            api=call.api,
            columns=data.get("columns", columns),
            data=data,
        )
    if resolved and resolved.get("type") == "base" and resolved.get("dataview") == "margin":
        data = execute_margin_api(
            subject=str(resolved.get("subject") or ""),
            args=call.args,
            outputs=call.outputs,
        )
        return ResultHandle(
            name=call.result_id,
            api=call.api,
            columns=data.get("columns", columns),
            data=data,
        )
    if resolved and resolved.get("type") == "base" and resolved.get("dataview") == "pricevalue":
        data = execute_pricevalue_api(
            subject=str(resolved.get("subject") or ""),
            args=call.args,
            outputs=call.outputs,
        )
        return ResultHandle(
            name=call.result_id,
            api=call.api,
            columns=data.get("columns", columns),
            data=data,
        )
    if resolved and resolved.get("type") == "base" and resolved.get("dataview") == "financial_3_table":
        data = execute_financial_3_table_api(
            subject=str(resolved.get("subject") or ""),
            args=call.args,
            outputs=call.outputs,
        )
        return ResultHandle(
            name=call.result_id,
            api=call.api,
            columns=data.get("columns", columns),
            data=data,
        )
    if resolved and resolved.get("type") == "base" and resolved.get("dataview") == "base_info" and resolved.get("subject") in {
        "stock",
        "index",
        "plate",
        "fund",
        "bond",
    }:
        data = execute_base_info_api(
            subject=str(resolved.get("subject") or ""),
            args=call.args,
            outputs=call.outputs,
        )
        return ResultHandle(
            name=call.result_id,
            api=call.api,
            columns=data.get("columns", columns),
            data=data,
        )
    if resolved and resolved.get("type") == "base" and resolved.get("subject") == "industry" and resolved.get("dataview") == "base_info":
        data = execute_industry_base_info_api(
            args=call.args,
            outputs=call.outputs,
        )
        return ResultHandle(
            name=call.result_id,
            api=call.api,
            columns=data.get("columns", columns),
            data=data,
        )
    if resolved and resolved.get("type") == "base" and resolved.get("subject") == "stock" and resolved.get("dataview") in {
        "shareholder",
        "pledge",
        "corporate_action",
        "performance_notice",
        "business_segment",
    }:
        data = execute_stock_corporate_api(
            dataview=str(resolved.get("dataview") or ""),
            args=call.args,
            outputs=call.outputs,
        )
        return ResultHandle(
            name=call.result_id,
            api=call.api,
            columns=data.get("columns", columns),
            data=data,
        )
    if resolved and resolved.get("type") == "base" and resolved.get("dataview") == "constitution":
        data = execute_constitution_api(
            subject=str(resolved.get("subject") or ""),
            args=call.args,
            outputs=call.outputs,
        )
        return ResultHandle(
            name=call.result_id,
            api=call.api,
            columns=data.get("columns", columns),
            data=data,
        )
    if resolved and resolved.get("type") == "base" and resolved.get("subject") == "hot_event":
        data = execute_hot_event_api(
            dataview=str(resolved.get("dataview") or ""),
            args=call.args,
            outputs=call.outputs,
        )
        return ResultHandle(
            name=call.result_id,
            api=call.api,
            columns=data.get("columns", columns),
            data=data,
        )
    if resolved and resolved.get("type") == "agg" and resolved.get("dataview") == "constitution":
        data = execute_constitution_agg_api(
            subject=str(resolved.get("subject") or ""),
            args=call.args,
            outputs=call.outputs,
            previous_results=previous_results,
        )
        return ResultHandle(
            name=call.result_id,
            api=call.api,
            columns=data.get("columns", columns),
            data=data,
        )
    if resolved and resolved.get("type") == "agg" and resolved.get("subject") == "stock" and resolved.get("dataview") == "quote":
        if _quote_realtime(call.args, resolved):
            data = execute_intraday_quote_agg_api(
                args=call.args,
                outputs=call.outputs,
            )
        else:
            data = execute_quote_agg_api(
                subject="stock",
                args=call.args,
                outputs=call.outputs,
            )
        return ResultHandle(
            name=call.result_id,
            api=call.api,
            columns=data.get("columns", columns),
            data=data,
        )
    if resolved and resolved.get("type") == "kd" and resolved.get("dataview") == "quote":
        subject = str(resolved.get("subject") or "")
        if subject == "stock" and _quote_realtime(call.args, resolved):
            data = execute_kd_intraday_quote_api(
                field=str(resolved.get("field") or ""),
                method=str(resolved.get("method") or ""),
                args=call.args,
                outputs=call.outputs,
            )
        else:
            data = execute_kd_quote_api(
                subject=subject,
                field=str(resolved.get("field") or ""),
                method=str(resolved.get("method") or ""),
                args=call.args,
                outputs=call.outputs,
            )
        return ResultHandle(
            name=call.result_id,
            api=call.api,
            columns=data.get("columns", columns),
            data=data,
        )
    if resolved and resolved.get("type") == "dynamic_cal" and resolved.get("dataview") == "quote":
        data = execute_dynamic_quote_api(
            subject=str(resolved.get("subject") or ""),
            args=call.args,
            outputs=call.outputs,
        )
        return ResultHandle(
            name=call.result_id,
            api=call.api,
            columns=data.get("columns", columns),
            data=data,
        )
    if resolved and resolved.get("type") == "kd" and resolved.get("dataview") == "moneyflow":
        data = execute_kd_moneyflow_api(
            subject=str(resolved.get("subject") or ""),
            field=str(resolved.get("field") or ""),
            method=str(resolved.get("method") or ""),
            args=call.args,
            outputs=call.outputs,
        )
        return ResultHandle(
            name=call.result_id,
            api=call.api,
            columns=data.get("columns", columns),
            data=data,
        )
    if resolved and resolved.get("type") == "kd" and resolved.get("dataview") == "margin":
        data = execute_kd_margin_api(
            subject=str(resolved.get("subject") or ""),
            field=str(resolved.get("field") or ""),
            method=str(resolved.get("method") or ""),
            args=call.args,
            outputs=call.outputs,
        )
        return ResultHandle(
            name=call.result_id,
            api=call.api,
            columns=data.get("columns", columns),
            data=data,
        )
    if resolved and resolved.get("type") == "kd" and resolved.get("dataview") == "pricevalue":
        data = execute_kd_pricevalue_api(
            subject=str(resolved.get("subject") or ""),
            field=str(resolved.get("field") or ""),
            method=str(resolved.get("method") or ""),
            args=call.args,
            outputs=call.outputs,
        )
        return ResultHandle(
            name=call.result_id,
            api=call.api,
            columns=data.get("columns", columns),
            data=data,
        )

    return ResultHandle(
        name=call.result_id,
        api=call.api,
        columns=columns,
        data={
            "status": "prepared",
            "api": call.api,
            "arguments": call.args,
            "columns": columns,
            "rows": [],
            "provider": "pending_real_api_adapter",
        },
    )


def _output_column(output: str) -> str:
    text = str(output or "").strip()
    if " as " in text:
        return text.split(" as ", 1)[1].strip()
    if "(" in text and ")" in text:
        inner = text.split("(", 1)[1].split(")", 1)[0].strip()
        return inner or text
    if "." in text:
        return text.rsplit(".", 1)[-1]
    return text


def _quote_realtime(args: Mapping[str, object], resolved: Mapping[str, object]) -> bool:
    field = str(resolved.get("field") or "")
    if field.startswith("minute_"):
        return True
    if resolved.get("type") == "kd" and "realtime" not in args:
        return False
    raw = args.get("realtime", resolved.get("default_realtime", 1))
    if raw in (None, ""):
        return True
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return int(raw) == 1
    text = str(raw).strip().lower()
    if text in {"0", "false", "no", "history", "historical"}:
        return False
    return True


def _materialize_call_refs(call: ApiCall, previous_results: Mapping[str, ResultHandle], *, skip_keys: set[str] | None = None) -> ApiCall:
    if not previous_results:
        return call
    skipped = skip_keys or set()
    args = {key: value if key in skipped else _materialize_value(value, previous_results) for key, value in call.args.items()}
    return replace(call, args=args)


def _materialize_value(value: object, previous_results: Mapping[str, ResultHandle]) -> object:
    if not isinstance(value, str) or not REF_RE.search(value):
        return value

    def repl(match: re.Match[str]) -> str:
        result_id, column = match.group(1), match.group(2)
        handle = previous_results.get(result_id)
        rows = handle.data.get("rows") if handle and isinstance(handle.data, Mapping) else None
        if not isinstance(rows, list):
            return "[]"
        values: list[str] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            raw = row.get(column)
            if raw in (None, ""):
                continue
            text = str(raw).strip()
            if text and text not in seen:
                values.append(text)
                seen.add(text)
        return "[" + ",".join(values) + "]"

    return REF_RE.sub(repl, value)
