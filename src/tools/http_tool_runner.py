from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any, Dict
from urllib.error import HTTPError
from urllib.request import Request, urlopen


TOOL_DEFINITIONS_DIR = Path("src/tools/definitions")


def _trim(value: Any) -> str:
    return str(value or "").strip()


def _load_definition(tool_name: str) -> Dict[str, Any]:
    path = TOOL_DEFINITIONS_DIR / f"{_trim(tool_name)}.tool.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _http_implementation(definition: Dict[str, Any]) -> Dict[str, Any]:
    profiles = definition.get("profiles") if isinstance(definition.get("profiles"), dict) else {}
    for profile_name in ("real", "api"):
        profile = profiles.get(profile_name)
        if not isinstance(profile, dict) or not profile.get("enabled", True):
            continue
        implementation = profile.get("implementation") if isinstance(profile.get("implementation"), dict) else {}
        if _trim(implementation.get("kind")).lower() == "http":
            return implementation
    return {}


def is_http_tool(tool_name: str) -> bool:
    return bool(_http_implementation(_load_definition(tool_name)))


def _env_key_name(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", _trim(value).upper()).strip("_")


def _resolve_api_key(tool_name: str, definition: Dict[str, Any]) -> str:
    meta = definition.get("meta") if isinstance(definition.get("meta"), dict) else {}
    app_id = _trim(meta.get("app_id"))
    candidates = [
        f"HTTP_TOOL_API_KEY_{_env_key_name(tool_name)}",
        f"SIMPLE_BI_API_KEY_{_env_key_name(app_id)}" if app_id else "",
        "SIMPLE_BI_API_KEY" if _trim(meta.get("source")) == "simple_bi" else "",
    ]
    for name in candidates:
        if name and _trim(os.getenv(name)):
            return _trim(os.getenv(name))
    return ""


def run_http_tool(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    definition = _load_definition(tool_name)
    implementation = _http_implementation(definition)
    url = _trim(implementation.get("url"))
    if not url:
        raise ValueError(f"http tool missing url: {tool_name}")
    method = _trim(implementation.get("method")).upper() or "POST"
    timeout = int(((definition.get("runtime_hints") or {}).get("timeout_seconds") or 30))
    payload = {"arguments": dict(args or {})}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    api_key = _resolve_api_key(tool_name, definition)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        auth = implementation.get("auth") if isinstance(implementation.get("auth"), dict) else {}
        header_name = _trim(auth.get("header"))
        if header_name:
            headers[header_name] = api_key
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass
        return {
            "tool": tool_name,
            "ok": False,
            "data": {},
            "error": raw or str(exc),
            "meta": {"status": exc.code},
        }
    parsed = json.loads(raw) if raw else {}
    if not isinstance(parsed, dict):
        return {"tool": tool_name, "ok": True, "data": {"value": parsed}, "error": "", "meta": {}}
    return parsed
