import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _json_size(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False))
    except Exception:
        return -1


def _sample_value(value: Any, max_chars: int = 180) -> Any:
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, str):
        return value[:max_chars]
    if isinstance(value, list):
        return {
            "len": len(value),
            "first": _sample_value(value[0], max_chars=max_chars) if value else None,
        }
    if isinstance(value, dict):
        out = {}
        for idx, (key, child) in enumerate(value.items()):
            if idx >= 6:
                break
            out[key] = _sample_value(child, max_chars=max_chars)
        return out
    return str(value)[:max_chars]


def _kind_of(value: Any) -> str:
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float, bool)) or value is None:
        return "scalar"
    return type(value).__name__


def _walk(value: Any, path: str, depth: int, max_depth: int, out: List[Dict[str, Any]]) -> None:
    out.append(
        {
            "path": path,
            "kind": _kind_of(value),
            "size_chars": _json_size(value),
            "sample": _sample_value(value),
        }
    )
    if depth >= max_depth:
        return
    if isinstance(value, dict):
        for key, child in value.items():
            _walk(child, f"{path}.{key}", depth + 1, max_depth, out)
    elif isinstance(value, list) and value:
        _walk(value[0], f"{path}[0]", depth + 1, max_depth, out)


def _load_tool_spec(tool_name: str) -> Dict[str, Any]:
    path = Path("src/tools/specs") / f"{tool_name}.spec.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _strip_root_prefix(path: str) -> str:
    return path[5:] if path.startswith("root.") else path


def _get_by_path(value: Any, path: str) -> Any:
    normalized = _strip_root_prefix(path)
    if not normalized:
        return value

    def resolve(current: Any, parts: List[str]) -> Any:
        if not parts:
            return current
        part = parts[0]
        if "[" in part:
            key, index_part = part.split("[", 1)
            if not index_part.endswith("]"):
                return None
            if key:
                if not isinstance(current, dict) or key not in current:
                    return None
                current = current[key]
            index_text = index_part[:-1].strip()
            if not isinstance(current, list):
                return None
            if index_text == "":
                values = [resolve(item, parts[1:]) for item in current]
                return [item for item in values if item is not None]
            try:
                idx = int(index_text)
            except ValueError:
                return None
            if idx >= len(current):
                return None
            return resolve(current[idx], parts[1:])
        if not isinstance(current, dict) or part not in current:
            return None
        return resolve(current[part], parts[1:])

    return resolve(value, normalized.split("."))


def _extract_metrics(value: Any) -> Any:
    if isinstance(value, dict):
        metrics = {}
        for key, child in value.items():
            if isinstance(child, (int, float, bool)) or child is None:
                metrics[key] = child
            elif isinstance(child, str) and len(child) <= 80:
                metrics[key] = child
            elif isinstance(child, dict):
                nested = {k: v for k, v in child.items() if isinstance(v, (int, float, bool, str)) and len(str(v)) <= 80}
                if nested:
                    metrics[key] = nested
        return metrics
    return _sample_value(value, max_chars=180)


def _summarize_table(value: Any) -> Dict[str, Any]:
    if not isinstance(value, list):
        return {"kind": _kind_of(value), "sample": _sample_value(value)}
    return {
        "row_count": len(value),
        "first_rows": [_sample_value(item, max_chars=120) for item in value[:3]],
        "last_rows": [_sample_value(item, max_chars=120) for item in value[-2:]] if len(value) > 3 else [],
    }


def _flatten_numeric_candidates(value: Any) -> List[float]:
    nums: List[float] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, (int, float)):
                nums.append(float(item))
            elif isinstance(item, list):
                for child in item:
                    if isinstance(child, (int, float)):
                        nums.append(float(child))
            elif isinstance(item, dict):
                nums.extend(_flatten_numeric_candidates(item))
    elif isinstance(value, dict):
        for child in value.values():
            nums.extend(_flatten_numeric_candidates(child))
    return nums


def _summarize_timeseries(value: Any) -> Dict[str, Any]:
    nums = _flatten_numeric_candidates(value)
    return {
        "point_count": len(value) if isinstance(value, list) else None,
        "numeric_count": len(nums),
        "min": min(nums) if nums else None,
        "max": max(nums) if nums else None,
        "sample": _sample_value(value, max_chars=120),
    }


def _summarize_news_list(value: Any) -> Dict[str, Any]:
    items = value if isinstance(value, list) else []
    summary_items = []
    for item in items[:5]:
        if isinstance(item, dict):
            summary_items.append(
                {
                    "title": str(item.get("title") or "").strip(),
                    "publish_time": str(item.get("publish_time") or "").strip(),
                    "site": str(item.get("site") or item.get("source") or "").strip(),
                    "snippet": str(item.get("snippet") or item.get("content") or "").strip()[:180],
                }
            )
    return {"count": len(items), "items": summary_items}


def _summarize_report_list(value: Any) -> Dict[str, Any]:
    items = value if isinstance(value, list) else []
    summary_items = []
    for item in items[:5]:
        if isinstance(item, dict):
            summary_items.append(
                {
                    "title": str(item.get("title") or "").strip(),
                    "publish_time": str(item.get("publish_time") or "").strip(),
                    "institution": str(item.get("institution") or "").strip(),
                    "report_type": str(item.get("report_type") or "").strip(),
                    "snippet": str(item.get("snippet") or item.get("content") or "").strip()[:180],
                }
            )
    return {"count": len(items), "items": summary_items}


def _reduce_with_method(value: Any, method: str) -> Any:
    if method == "extract_metrics":
        return _extract_metrics(value)
    if method == "summarize_table":
        return _summarize_table(value)
    if method == "summarize_timeseries":
        return _summarize_timeseries(value)
    if method == "summarize_news_list":
        return _summarize_news_list(value)
    if method == "summarize_report_list":
        return _summarize_report_list(value)
    return _sample_value(value)


def build_tool_result_profile(tool_name: str, result: Dict[str, Any], max_depth: int = 3) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    _walk(result, "root", 0, max_depth, rows)
    return {
        "tool_name": tool_name,
        "result_ok": bool(result.get("ok")),
        "root_size_chars": _json_size(result),
        "top_fields": sorted(rows, key=lambda item: item.get("size_chars", -1), reverse=True)[:30],
    }


def build_rule_retention_plan(tool_name: str, result: Dict[str, Any]) -> Dict[str, Any]:
    spec = _load_tool_spec(tool_name)
    guidance = (spec.get("output_guidance") if isinstance(spec, dict) else {}) or {}
    rules: List[Dict[str, Any]] = []
    field_policies = guidance.get("field_policies") if isinstance(guidance.get("field_policies"), dict) else {}

    for raw_path, policy in field_policies.items():
        if not isinstance(policy, dict):
            continue
        normalized_path = str(raw_path or "").strip()
        if not normalized_path:
            continue
        path = normalized_path if normalized_path.startswith("data") else f"data.{normalized_path}"
        strategy = str(policy.get("strategy") or "auto").strip()
        render_type = str(policy.get("render_type") or "").strip()
        extra = {"render_type": render_type} if render_type else {}
        if strategy == "render":
            rules.append(
                {
                    "path": f"root.{path}",
                    "action": "pass_through",
                    "target": ["render", "storage"],
                    "reason": "field_policy_render",
                    **extra,
                }
            )
        elif strategy == "reference":
            rules.append(
                {
                    "path": f"root.{path}",
                    "action": "pass_through",
                    "target": ["reference", "storage"],
                    "reason": "field_policy_reference",
                    **extra,
                }
            )
        elif strategy == "prompt_full":
            rules.append(
                {
                    "path": f"root.{path}",
                    "action": "reasoning",
                    "target": ["prompt"],
                    "reason": "field_policy_prompt_full",
                }
            )
        elif strategy == "prompt_summary":
            rules.append(
                {
                    "path": f"root.{path}",
                    "action": "compress",
                    "target": ["prompt"],
                    "method": "extract_key_points",
                    "reason": "field_policy_prompt_summary",
                }
            )
        elif strategy == "ignore":
            rules.append(
                {
                    "path": f"root.{path}",
                    "action": "drop",
                    "target": [],
                    "reason": "field_policy_ignore",
                }
            )

    for path in guidance.get("high_value_for_reasoning", []) or []:
        rules.append(
            {
                "path": f"root.{path}",
                "action": "reasoning",
                "target": ["prompt"],
                "reason": "high_value_for_reasoning",
            }
        )

    for path, method in (guidance.get("recommended_reducers") or {}).items():
        rules.append(
            {
                "path": f"root.{path}",
                "action": "compress",
                "target": ["prompt"],
                "method": method,
                "reason": "recommended_reducer",
            }
        )

    for path in guidance.get("high_value_for_render", []) or []:
        rules.append(
            {
                "path": f"root.{path}",
                "action": "pass_through",
                "target": ["render", "storage"],
                "reason": "high_value_for_render",
            }
        )

    for path in guidance.get("drop_default", []) or []:
        rules.append(
            {
                "path": f"root.{path}",
                "action": "drop",
                "target": [],
                "reason": "drop_default",
            }
        )

    deduped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    priority = {"drop": 0, "pass_through": 1, "reasoning": 2, "compress": 3}
    for rule in rules:
        key = (rule["path"], ",".join(sorted(rule["target"])))
        existing = deduped.get(key)
        if existing is None or priority.get(rule["action"], -1) > priority.get(existing["action"], -1):
            deduped[key] = rule

    return {
        "tool_name": tool_name,
        "rules": list(deduped.values()),
        "notes": list(spec.get("input_guidance", {}).get("notes", []) if isinstance(spec, dict) else []),
    }


def reduce_tool_result_for_runtime(tool_name: str, result: Dict[str, Any]) -> Dict[str, Any]:
    profile = build_tool_result_profile(tool_name, result)
    plan = build_rule_retention_plan(tool_name, result)

    prompt_context: Dict[str, Any] = {
        "tool": tool_name,
        "ok": bool(result.get("ok")),
        "error": str(result.get("error") or ""),
        "reasoning": {},
        "compressed": {},
    }
    render_artifacts: Dict[str, Any] = {}
    reference_artifacts: Dict[str, Any] = {}
    render_preferences: Dict[str, Dict[str, Any]] = {}
    reference_preferences: Dict[str, Dict[str, Any]] = {}

    for rule in plan.get("rules", []):
        path = rule.get("path", "")
        value = _get_by_path(result, path)
        if value is None:
            continue
        action = rule.get("action")
        target = set(rule.get("target") or [])
        if action == "reasoning" and "prompt" in target:
            prompt_context["reasoning"][_strip_root_prefix(path)] = value
        elif action == "compress" and "prompt" in target:
            prompt_context["compressed"][_strip_root_prefix(path)] = _reduce_with_method(value, str(rule.get("method") or "extract_key_points"))
        elif action == "pass_through":
            normalized_path = _strip_root_prefix(path)
            if "render" in target:
                render_artifacts[normalized_path] = value
                render_preferences[normalized_path] = {
                    "render_type": str(rule.get("render_type") or "").strip() or "auto",
                    "strategy": "render",
                }
            if "reference" in target:
                reference_artifacts[normalized_path] = value
                reference_preferences[normalized_path] = {
                    "render_type": str(rule.get("render_type") or "").strip() or "auto",
                    "strategy": "reference",
                }

    data = result.get("data") if isinstance(result.get("data"), dict) else None
    if data and not plan.get("rules"):
        # Dynamic/custom tools may not have a static Tool Spec. Preserve their
        # declared business output instead of reducing a successful call to an
        # empty execution acknowledgement.
        prompt_context["compressed"]["data"] = _sample_value(data, max_chars=500)
        for key, value in data.items():
            render_path = f"data.{key}"
            render_artifacts[render_path] = value
            render_preferences[render_path] = {
                "render_type": "auto",
                "strategy": "render",
            }

    return {
        "tool_name": tool_name,
        "profile": profile,
        "retention_plan": plan,
        "prompt_context": prompt_context,
        "render_artifacts": render_artifacts,
        "reference_artifacts": reference_artifacts,
        "render_preferences": render_preferences,
        "reference_preferences": reference_preferences,
    }
