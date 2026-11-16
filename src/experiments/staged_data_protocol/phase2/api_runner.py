from __future__ import annotations

import re
from dataclasses import replace
from typing import Mapping

from src.experiments.staged_data_protocol.phase2.catalog import resolve_api
from src.experiments.staged_data_protocol.phase2.models import ApiCall, ResultHandle
from src.experiments.staged_data_protocol.phase2.python_filter import condition, bind_references, certainly_empty, predicates
from src.experiments.staged_data_protocol.phase2.report_provider import execute_report_api, execute_report_metric_api, execute_report_agg_api


REF_RE = re.compile(r"\b(r\d+)\.([A-Za-z_]\w*)\b")
EMPTY_REF_VALUE = "__fin_agent_empty_reference__"
FILTER_ATOM_RE = re.compile(
    r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\s*"
    r"(?:in|==|=|!=|>=|<=|>|<)\s*"
    r"(?:\[[^\]]*\]|\([^)]*\)|[^()]+?)"
    r"(?="
    r"\s+(?:and|or)\s+\(*\s*[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\s*(?:in|==|=|!=|>=|<=|>|<)"
    r"|\s*\)"
    r"|[,;]"
    r"|$"
    r")",
    flags=re.IGNORECASE,
)
BOOLEAN_TOKEN_RE = re.compile(r"\btrue\b|\bfalse\b|\band\b|\bor\b|[()]", flags=re.IGNORECASE)


def execute_api_call(call: ApiCall, previous_results: Mapping[str, ResultHandle] | None = None) -> ResultHandle:
    """Stable execution boundary for phase-2 API calls.

    Supported providers return real rows behind the same ResultHandle shape.
    APIs without a provider still return a standard prepared result so the LLM,
    parser, and validator contracts do not change while providers are added.
    """

    previous_results = previous_results or {}
    expression = condition(call.args)
    if expression is not None:
        # Bind the tree, never interpolate result values into expression text.
        call = replace(call, args={**call.args, "_filter_expression": bind_references(expression, previous_results)})
    resolved = resolve_api(call.api)
    columns = [_output_column(item) for item in call.outputs]
    if expression is not None and certainly_empty(call.args["_filter_expression"]):
        refs = [f"{p['value']['result']}.{p['value']['field']}" for p in predicates(expression) if isinstance(p.get("value"), dict)]
        return ResultHandle(name=call.result_id, api=call.api, columns=columns, data={
            "status": "ok", "api": call.api, "arguments": call.args, "columns": columns,
            "rows": [], "row_count": 0, "empty_references": sorted(set(refs)),
            "reason": "dependent query skipped because its filter is false after reference binding",
        })
    skipped_ref_args = (
        {"metric", "agg"}
        if resolved and resolved.get("type") == "agg"
        else set()
    )
    if expression is not None:
        skipped_ref_args.update({"filter", "_filter_expression"})
    empty_refs_by_arg = _empty_reference_values_by_arg(call, previous_results)
    if expression is not None:
        empty_refs_by_arg.pop("filter", None)
        empty_refs_by_arg.pop("_filter_expression", None)
    filter_text = str(call.args.get("filter") or "")
    filter_resolution = (
        _filter_truth_with_empty_refs(
            filter_text,
            set(empty_refs_by_arg.get("filter") or []),
        )
        if empty_refs_by_arg.get("filter")
        else True
    )
    if empty_refs_by_arg.get("filter") and filter_resolution is not False:
        reason = (
            "filter boolean structure could not be resolved safely "
            "after an upstream reference returned no values"
            if filter_resolution is None
            else (
                "filter contains an alternative branch after an upstream "
                "reference returned no values; the current provider boundary "
                "cannot preserve that boolean expression safely"
            )
        )
        return ResultHandle(
            name=call.result_id,
            api=call.api,
            columns=columns,
            data={
                "status": "filter_reference_resolution_error",
                "api": call.api,
                "arguments": call.args,
                "columns": columns,
                "rows": [],
                "row_count": 0,
                "reason": reason,
                "empty_references": sorted(empty_refs_by_arg["filter"]),
            },
        )
    must_short_circuit = any(
        key in skipped_ref_args
        or key != "filter"
        or filter_resolution is False
        for key in empty_refs_by_arg
    )
    if empty_refs_by_arg and must_short_circuit:
        empty_refs = sorted(
            {
                ref
                for refs in empty_refs_by_arg.values()
                for ref in refs
            }
        )
        return ResultHandle(
            name=call.result_id,
            api=call.api,
            columns=columns,
            data={
                "status": "ok",
                "api": call.api,
                "arguments": call.args,
                "columns": columns,
                "rows": [],
                "row_count": 0,
                "reason": "dependent query skipped because upstream references have no values",
                "empty_references": empty_refs,
            },
        )
    call = _materialize_call_refs(
        call,
        previous_results,
        skip_keys=skipped_ref_args,
    )
    if resolved and resolved.get("subject") == "stock":
        view = resolved.get("dataview")
        if view in {"report", "report_metric"}:
            if resolved.get("type") == "agg":
                data = execute_report_agg_api(dataview=view, args=call.args, outputs=call.outputs)
            else:
                provider = execute_report_api if view == "report" else execute_report_metric_api
                data = provider(args=call.args, outputs=call.outputs)
            return ResultHandle(name=call.result_id, api=call.api,
                                columns=data.get("columns", columns), data=data)
    return ResultHandle(name=call.result_id, api=call.api, columns=columns,
                        data={"status": "unsupported", "api": call.api, "rows": [],
                              "columns": columns, "reason": "API not available"})


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


def _materialize_call_refs(call: ApiCall, previous_results: Mapping[str, ResultHandle], *, skip_keys: set[str] | None = None) -> ApiCall:
    if not previous_results:
        return call
    skipped = skip_keys or set()
    args = {key: value if key in skipped else _materialize_value(value, previous_results) for key, value in call.args.items()}
    return replace(call, args=args)


def _empty_reference_values_by_arg(
    call: ApiCall,
    previous_results: Mapping[str, ResultHandle],
) -> dict[str, list[str]]:
    empty: dict[str, list[str]] = {}
    for key, value in call.args.items():
        if not isinstance(value, str):
            continue
        for result_id, column in REF_RE.findall(value):
            handle = previous_results.get(result_id)
            if handle is None:
                continue
            if isinstance(handle.data, Mapping):
                rows = handle.data.get("rows")
            else:
                rows = handle.data
            if not isinstance(rows, list) or not any(
                isinstance(row, Mapping)
                and _has_reference_value(row.get(column))
                for row in rows
            ):
                empty.setdefault(key, []).append(f"{result_id}.{column}")
    return {
        key: sorted(set(refs))
        for key, refs in empty.items()
    }


def _has_reference_value(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _filter_truth_with_empty_refs(
    filter_text: str,
    empty_refs: set[str],
) -> bool | None:
    """Evaluate only the boolean shape, treating empty-ref atoms as false.

    Other predicates are treated as true. False proves that no alternative
    branch can match; True means a valid alternative branch remains. None means
    the structure could not be parsed safely and must not reach a more
    permissive provider parser.
    """

    if not filter_text or not empty_refs:
        return True
    parts: list[str] = []
    cursor = 0
    seen_empty_refs: set[str] = set()
    for match in FILTER_ATOM_RE.finditer(filter_text):
        parts.append(filter_text[cursor:match.start()])
        atom = match.group(0)
        atom_refs = {
            f"{result_id}.{column}"
            for result_id, column in REF_RE.findall(atom)
        }
        atom_empty_refs = atom_refs & empty_refs
        seen_empty_refs.update(atom_empty_refs)
        parts.append(" false " if atom_empty_refs else " true ")
        cursor = match.end()
    parts.append(filter_text[cursor:])
    if seen_empty_refs != empty_refs:
        return None
    expression = "".join(parts)
    residue = BOOLEAN_TOKEN_RE.sub("", expression)
    if residue.strip():
        return None
    tokens = [
        match.group(0).lower()
        for match in BOOLEAN_TOKEN_RE.finditer(expression)
    ]
    return _evaluate_boolean_tokens(tokens)


def _evaluate_boolean_tokens(tokens: list[str]) -> bool | None:
    position = 0

    def parse_factor() -> bool:
        nonlocal position
        if position >= len(tokens):
            raise ValueError("missing boolean factor")
        token = tokens[position]
        position += 1
        if token == "true":
            return True
        if token == "false":
            return False
        if token != "(":
            raise ValueError(f"unexpected boolean token={token}")
        value = parse_or()
        if position >= len(tokens) or tokens[position] != ")":
            raise ValueError("missing closing parenthesis")
        position += 1
        return value

    def parse_and() -> bool:
        nonlocal position
        value = parse_factor()
        while position < len(tokens) and tokens[position] == "and":
            position += 1
            right = parse_factor()
            value = value and right
        return value

    def parse_or() -> bool:
        nonlocal position
        value = parse_and()
        while position < len(tokens) and tokens[position] == "or":
            position += 1
            right = parse_and()
            value = value or right
        return value

    try:
        value = parse_or()
        return value if position == len(tokens) else None
    except ValueError:
        return None


def _materialize_value(value: object, previous_results: Mapping[str, ResultHandle]) -> object:
    if not isinstance(value, str) or not REF_RE.search(value):
        return value

    def repl(match: re.Match[str]) -> str:
        result_id, column = match.group(1), match.group(2)
        handle = previous_results.get(result_id)
        if handle and isinstance(handle.data, Mapping):
            rows = handle.data.get("rows")
        elif handle and isinstance(handle.data, list):
            rows = handle.data
        else:
            rows = None
        if not isinstance(rows, list):
            return f"[{EMPTY_REF_VALUE}]"
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
        if not values:
            return f"[{EMPTY_REF_VALUE}]"
        return "[" + ",".join(values) + "]"

    return REF_RE.sub(repl, value)
