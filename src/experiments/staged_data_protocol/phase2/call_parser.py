from __future__ import annotations

import re
from typing import Any, Dict, List

from src.experiments.staged_data_protocol.phase2.models import ApiCall


CALL_RE = re.compile(
    r"^\s*([A-Za-z_]\w*)\s*=\s*([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*){1,3})\s*\((.*)\)\s*->\s*(.+?)\s*$",
    flags=re.DOTALL,
)


def parse_api_call(text: str) -> ApiCall:
    raw = _strip_fence(text)
    match = CALL_RE.match(raw)
    if not match:
        raise ValueError(f"invalid API request string: {raw}")
    result_id, api, args_text, outputs_text = match.groups()
    return ApiCall(
        result_id=result_id,
        api=api,
        args=parse_args(args_text),
        outputs=[item.strip() for item in _split_top_level(outputs_text) if item.strip()],
        raw=raw,
    )


def parse_args(args_text: str) -> Dict[str, Any]:
    rows: Dict[str, Any] = {}
    last_key = ""
    for item in _split_top_level(args_text):
        if not item.strip():
            continue
        if "=" not in item:
            if last_key in {"group_by"}:
                rows[last_key] = f"{rows[last_key]}, {item.strip()}"
                continue
            raise ValueError(f"invalid argument: {item}")
        key, value = item.split("=", 1)
        last_key = key.strip()
        rows[last_key] = _parse_value(value.strip())
    return rows


def _parse_value(value: str) -> Any:
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    return value


def _split_top_level(text: str) -> List[str]:
    rows: List[str] = []
    current: List[str] = []
    quote = ""
    depth = 0
    for char in text:
        if quote:
            current.append(char)
            if char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
            current.append(char)
            continue
        if char in {"(", "["}:
            depth += 1
        elif char in {")",
            "]",
        } and depth:
            depth -= 1
        if char == "," and depth == 0:
            rows.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if current:
        rows.append("".join(current).strip())
    return rows


def _strip_fence(text: str) -> str:
    raw = str(text or "").strip()
    fenced = re.search(r"```(?:text)?\s*(.*?)```", raw, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()
    return raw
