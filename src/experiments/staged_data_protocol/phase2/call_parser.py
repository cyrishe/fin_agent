from __future__ import annotations

import re
import ast
from typing import Any, Dict, List

from src.experiments.staged_data_protocol.phase2.models import ApiCall


CALL_RE = re.compile(
    r"^\s*([A-Za-z_]\w*)\s*=\s*([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*){1,3})\s*\("
)


def _argument_span(raw: str):
    match = CALL_RE.match(raw)
    if not match:
        raise ValueError(f"invalid API request string: {raw}")
    # Find the invocation's own closing parenthesis. A greedy regex swallowed
    # subsequent calls as arguments, producing misleading field/limit errors.
    quote = ""
    escaped = False
    depth = 1
    end = match.end()
    for end in range(match.end(), len(raw)):
        char = raw[end]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char in {"'", '"'}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                break
    if depth or quote:
        raise ValueError("invalid API request: unclosed argument list or quoted value")
    return match, end


def rewrite_argument_text(text: str, transform) -> str:
    """Rewrite one known argument at an integration boundary, not its literals."""
    raw = _strip_fence(text)
    match, end = _argument_span(raw)
    items = []
    for item in _split_top_level(raw[match.end():end]):
        key, separator, value = item.partition("=")
        items.append(f"{key}={transform(key.strip(), value)}" if separator else item)
    return raw[:match.end()] + ", ".join(items) + raw[end:]


def parse_api_call(text: str) -> ApiCall:
    raw = _strip_fence(text)
    match, end = _argument_span(raw)
    result_id, api = match.groups()
    args_text = raw[match.end():end]
    tail = raw[end + 1:].strip()
    if not tail.startswith("->"):
        raise ValueError("invalid API request: expected -> output fields after argument list")
    outputs_text = tail[2:].strip()
    if not outputs_text:
        raise ValueError("invalid API request: missing output fields")
    if re.search(r"\b[A-Za-z_]\w*\s*=\s*[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\s*\(", outputs_text):
        raise ValueError("Exactly one API call is allowed per request; put each call in a separate steps[].request and use stepN.column references.")
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
        try:
            return ast.literal_eval(value)
        except (SyntaxError, ValueError):
            return value[1:-1]  # Legacy unescaped inner quotes.
    return value


def _split_top_level(text: str) -> List[str]:
    rows: List[str] = []
    current: List[str] = []
    quote = ""
    escaped = False
    depth = 0
    for char in text:
        if quote:
            current.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
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
