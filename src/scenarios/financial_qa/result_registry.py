from __future__ import annotations

import json
import re
from typing import Any, Dict, Mapping

from src.experiments.staged_data_protocol.phase2.call_parser import parse_api_call
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
        "realtime",
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

        return _FLOW_REF_RE.sub(replace, request)

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
                "闭环判断：执行成功并返回零行。若上方 API、selection_applied、输出字段"
                "和时间与 goal 一致，本步已经完成，直接如实回答零行；不得放宽、换 API、"
                "切实时模式或拆分对象。只有能明确说出 goal 与请求的具体偏差时才修正。"
            )
        elif unavailable:
            guidance = (
                f"闭环判断：执行成功并返回 {row_count} 行；"
                f"有值列 {', '.join(available) or '无'} 可直接使用，"
                f"无值列 {', '.join(unavailable)} 就是当前数据源未提供。"
                "若上方 API、selection_applied、输出字段和时间与 goal 一致，本步已经完成："
                "保留身份范围并如实回答缺值，不换 API、切实时模式、放宽条件或查询原始明细。"
                "只有能明确说出 goal 与请求的具体偏差时才修正。"
            )
        else:
            guidance = (
                f"闭环判断：执行成功并返回 {row_count} 行，所请求列均有值。"
                "若上方 API、selection_applied、输出字段和时间与 goal 一致，本步已经完成，"
                "进入不同目标或回答；只有具体语义偏差才允许修正。"
            )
        if sample_complete:
            guidance += " sample_complete=true，禁止再分页加载。"
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
