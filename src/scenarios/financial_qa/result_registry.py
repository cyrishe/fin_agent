from __future__ import annotations

import json
import re
from typing import Any, Dict, Mapping

from src.experiments.staged_data_protocol.phase2.call_parser import parse_api_call, rewrite_argument_text
from src.experiments.staged_data_protocol.phase2 import python_filter as pf
from src.experiments.staged_data_protocol.phase2.models import ResultHandle


_RESULT_NAME_RE = re.compile(r"r(\d+)")
_ASSIGNMENT_RE = re.compile(r"^(\s*)([A-Za-z_]\w*)(\s*=)", flags=re.DOTALL)
_RESULT_REF_RE = re.compile(r"\b(r\d+)\.([A-Za-z_]\w*)\b")
_FLOW_REF_RE = re.compile(r"\bstep(\d+)\.([A-Za-z_]\w*)\b", flags=re.IGNORECASE)


def _trim(value: Any) -> str:
    return str(value or "").strip()


def _rows(handle: ResultHandle) -> list[Mapping[str, Any]]:
    data = handle.data
    if isinstance(data, Mapping):
        values = data.get("rows")
    else:
        values = data
    if not isinstance(values, list):
        return []
    return [item for item in values if isinstance(item, Mapping)]


def _row_count(handle: ResultHandle, rows: list[Mapping[str, Any]]) -> int:
    if isinstance(handle.data, Mapping):
        try:
            return int(handle.data.get("row_count"))
        except (TypeError, ValueError):
            pass
    return len(rows)


def _is_populated(value: Any) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


class FinanceResultRegistry:
    """Compact, addressable working set for financial data query results."""

    SELECTION_KEYS = (
        "k",
        "filter",
        "order",
        "limit",
        "mode",
        "agg",
        "group_by",
    )

    @classmethod
    def selection_applied(cls, call_args: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            key: call_args.get(key)
            for key in cls.SELECTION_KEYS
            if call_args.get(key) not in (None, "")
        }

    @classmethod
    def selection_from_request(cls, request: str) -> Dict[str, Any]:
        try:
            call = parse_api_call(request)
        except Exception:
            return {}
        return cls.selection_applied(call.args)

    @staticmethod
    def next_result_name(handles: Mapping[str, ResultHandle]) -> str:
        highest = 0
        for name in handles:
            match = _RESULT_NAME_RE.fullmatch(_trim(name))
            if match:
                highest = max(highest, int(match.group(1)))
        return f"r{highest + 1}"

    @staticmethod
    def assign_result_name(request: str, result_name: str) -> tuple[str, str]:
        """Parse one request and replace only its system-owned result identifier."""
        call = parse_api_call(request)
        match = _ASSIGNMENT_RE.match(call.raw)
        if not match:
            raise ValueError(f"invalid API request string: {call.raw}")
        canonical = (
            call.raw[: match.start(2)]
            + result_name
            + call.raw[match.end(2) :]
        )
        return canonical, call.result_id

    @staticmethod
    def dependencies(request: str) -> list[str]:
        try:
            call = parse_api_call(request)
            tree = pf.condition(call.args)
        except ValueError:
            tree = None
        if tree is not None:
            refs = {p["value"]["result"] for p in pf.predicates(tree) if isinstance(p.get("value"), dict)}
            for key, value in call.args.items():
                if key != "filter" and isinstance(value, str):
                    refs.update(name for name, _ in _RESULT_REF_RE.findall(value))
            return sorted(refs, key=FinanceResultRegistry._sort_key)
        return sorted(
            {result_name for result_name, _ in _RESULT_REF_RE.findall(request)},
            key=FinanceResultRegistry._sort_key,
        )

    @staticmethod
    def resolve_flow_refs(
        request: str,
        *,
        completed_steps: Mapping[int, str],
    ) -> str:
        def replace(match: re.Match[str]) -> str:
            step_number = int(match.group(1))
            result_name = _trim(completed_steps.get(step_number))
            if not result_name:
                raise ValueError(
                    f"FLOW_REF_ERROR: step{step_number} is not a completed earlier step"
                )
            return f"{result_name}.{match.group(2)}"

        call = parse_api_call(request)
        try:
            tree = pf.condition(call.args)
        except pf.FilterSyntaxError:
            tree = None  # Runtime validation owns malformed/legacy conditions.
        if tree is None:
            return _FLOW_REF_RE.sub(replace, request)
        def resolve(p):
            ref = p.get("value")
            if isinstance(ref, dict) and ref["result"].startswith("step"):
                step_number = int(ref["result"][4:])
                name = _trim(completed_steps.get(step_number))
                if not name:
                    raise ValueError(f"FLOW_REF_ERROR: step{step_number} is not a completed earlier step")
                return {**p, "value": {**ref, "result": name}}
            return p
        bound = pf.map_predicates(tree, resolve)
        def transform(key, value):
            if key == "filter":
                return json.dumps(pf.to_source(bound), ensure_ascii=False) if bound != tree else value
            return _FLOW_REF_RE.sub(replace, value)
        return rewrite_argument_text(request, transform)

    def entries(
        self,
        *,
        handles: Mapping[str, ResultHandle],
        metadata_by_name: Mapping[str, Mapping[str, Any]],
    ) -> list[Dict[str, Any]]:
        entries: list[Dict[str, Any]] = []
        for name in sorted(handles, key=self._sort_key):
            handle = handles[name]
            metadata = (
                metadata_by_name.get(name)
                if isinstance(metadata_by_name.get(name), Mapping)
                else {}
            )
            rows = _rows(handle)
            schema = (
                metadata.get("schema")
                if isinstance(metadata.get("schema"), Mapping)
                else {}
            )
            schema_columns = (
                schema.get("columns")
                if isinstance(schema.get("columns"), list)
                else []
            )
            types_by_name = {
                _trim(item.get("name")): _trim(item.get("type")) or "unknown"
                for item in schema_columns
                if isinstance(item, Mapping) and _trim(item.get("name"))
            }
            columns = []
            for column in handle.columns:
                columns.append(
                    {
                        "name": column,
                        "type": types_by_name.get(column, "unknown"),
                        "populated_count": sum(
                            1 for row in rows if _is_populated(row.get(column))
                        ),
                    }
                )
            sample = (
                metadata.get("sample")
                if isinstance(metadata.get("sample"), Mapping)
                else {}
            )
            sample_rows = (
                sample.get("rows")
                if isinstance(sample.get("rows"), list)
                else []
            )
            count = _row_count(handle, rows)
            entries.append(
                {
                    "result_name": name,
                    "goal": _trim(handle.task or metadata.get("goal")),
                    "api": _trim(handle.api or metadata.get("api")),
                    "selection_applied": dict(
                        metadata.get("selection_applied")
                        if isinstance(metadata.get("selection_applied"), Mapping)
                        else {}
                    ),
                    "row_count": count,
                    "observed_rows": len(rows),
                    "columns": columns,
                    "depends_on": [
                        _trim(item)
                        for item in metadata.get("depends_on") or []
                        if _trim(item)
                    ],
                    "result_ref": _trim(metadata.get("result_ref")),
                    "sample_complete": count <= len(sample_rows),
                }
            )
        return entries

    @staticmethod
    def step_evidence(
        entry: Mapping[str, Any],
        *,
        call_args: Mapping[str, Any],
    ) -> Dict[str, Any]:
        result_name = _trim(entry.get("result_name"))
        row_count = int(entry.get("row_count") or 0)
        columns = [
            item
            for item in entry.get("columns") or []
            if isinstance(item, Mapping) and _trim(item.get("name"))
        ]
        available = [
            _trim(item.get("name"))
            for item in columns
            if int(item.get("populated_count") or 0) > 0
        ]
        unavailable = [
            _trim(item.get("name"))
            for item in columns
            if int(item.get("populated_count") or 0) == 0
        ]
        selection = FinanceResultRegistry.selection_applied(call_args)
        available_refs = [f"{result_name}.{column}" for column in available]
        sample_complete = bool(entry.get("sample_complete"))
        if row_count == 0:
            guidance = (
                "执行成功，当前查询条件下未匹配记录；本步按空结果完成。"
                "保留已执行的对象、时间和筛选条件，如实说明已查范围与零行事实。"
            )
        elif unavailable:
            guidance = (
                f"执行成功并返回 {row_count} 行；"
                f"有值列 {', '.join(available) or '无'} 可直接使用，"
                f"无值列 {', '.join(unavailable)} 在本次结果中缺值。"
                "本步按返回结果完成，保留可用身份范围与实际缺口，继续尚未完成的目标或组织回答。"
            )
        else:
            guidance = (
                f"执行成功并返回 {row_count} 行，所请求列均有值。"
                "本步已完成；使用本结果继续尚未完成的目标，取数目标完成时组织回答。"
            )
        # Completeness is a fact in the adjacent field, not a second assertion
        # embedded in prose: a runtime may project the model-visible sample.
        return {
            "execution_completed": True,
            "selection_applied": selection,
            "populated_columns": available,
            "available_refs": available_refs,
            "unavailable_columns": unavailable,
            "sample_complete": sample_complete,
            "guidance": guidance,
        }

    def prompt_text(
        self,
        *,
        handles: Mapping[str, ResultHandle],
        metadata_by_name: Mapping[str, Mapping[str, Any]],
    ) -> str:
        payload = {
            "next_result_name": self.next_result_name(handles),
            "results": self.entries(
                handles=handles,
                metadata_by_name=metadata_by_name,
            ),
        }
        return json.dumps(payload, ensure_ascii=False, default=str, indent=2)

    @staticmethod
    def _sort_key(value: Any) -> tuple[int, str]:
        text = _trim(value)
        match = _RESULT_NAME_RE.fullmatch(text)
        return (int(match.group(1)), "") if match else (10**9, text)
