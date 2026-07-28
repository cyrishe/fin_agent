from __future__ import annotations
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping

from src.experiments.staged_data_protocol.phase2.models import ResultHandle, Step


PROMPT_CATALOG_PATH = Path("src/tools/finance_data/catalog/api_view_catalog.json")


@lru_cache(maxsize=1)
def _prompt_catalog() -> dict[str, Any]:
    return json.loads(PROMPT_CATALOG_PATH.read_text(encoding="utf-8"))


def _api_class_patterns() -> Mapping[str, Any]:
    patterns = _prompt_catalog().get("api_class_patterns") or {}
    return patterns if isinstance(patterns, Mapping) else {}


def _subject_config(subject: str) -> Mapping[str, Any]:
    subjects = _prompt_catalog().get("subjects") or {}
    current = subjects.get(subject) if isinstance(subjects, Mapping) else None
    return current if isinstance(current, Mapping) else {}


def _get_prompt_dataview(subject: str, dataview: str) -> Mapping[str, Any] | None:
    subject_cfg = _subject_config(subject)
    candidates = [dataview]
    if dataview == "base_info":
        candidates.append("basic_info")
    if dataview == "basic_info":
        candidates.append("base_info")
    for name in candidates:
        view = subject_cfg.get(name)
        if isinstance(view, Mapping):
            return view
    return None


def build_context_text(
    *,
    step: Step,
    previous_results: Mapping[str, ResultHandle],
    validation_feedback: Iterable[str] | None = None,
    result_id: str = "",
) -> str:
    sections = build_context_sections(
        step=step,
        previous_results=previous_results,
        validation_feedback=validation_feedback,
        result_id=result_id,
    )
    blocks = [
        "# Current Step\n" + sections["current_step"],
        "# Request Types\n" + sections["request_types"],
        "# Current Dataview\n" + sections["current_dataview"],
        "# Available APIs\n" + sections["available_apis"],
        "# Supported Metrics\n" + sections["supported_metrics"],
    ]
    if sections["previous_results"]:
        blocks.append("# Previous Results\n" + sections["previous_results"])
    if sections["validation_feedback"]:
        blocks.append("# Validation Feedback\n" + sections["validation_feedback"])
    blocks.append("# Required Result Id\n" + sections["required_result_id"])
    return "\n\n".join(blocks)


def build_context_sections(
    *,
    step: Step,
    previous_results: Mapping[str, ResultHandle],
    validation_feedback: Iterable[str] | None = None,
    result_id: str = "",
) -> dict[str, str]:
    view = _get_prompt_dataview(step.subject, step.dataview)
    if not view:
        return {
            "current_step": _code_block(step.raw),
            "request_types": "unavailable",
            "current_dataview": "unavailable",
            "available_apis": "unavailable",
            "supported_metrics": "none",
            "previous_results": _format_session_results(previous_results),
            "session_results": _format_session_results(previous_results),
            "validation_feedback": _format_feedback(validation_feedback),
            "required_result_id": f"`{result_id}`" if result_id else "none",
        }
    api_classes = _api_classes(step, view)
    request_type_lines: list[str] = []
    for index, class_name in enumerate(api_classes, start=1):
        if index > 1:
            request_type_lines.append("")
        request_type_lines.extend(_format_api_class(class_name, include_metrics=False))
    subject_meta = _subject_config(step.subject).get("_meta")
    current_dataview = [
        f"- subject: `{step.subject}`",
        f"- subject_desc: {_meta_text(subject_meta, 'desc')}",
        f"- dataview: `{step.dataview}`",
        f"- desc: {view.get('desc') or ''}",
        "- fields:",
        _code_block(_format_fields(view.get("fields"))),
    ]
    value_domains = view.get("value_domains")
    if isinstance(value_domains, Mapping):
        current_dataview.append("- value_domains:")
        current_dataview.append(_indent(_code_block(json.dumps(value_domains, ensure_ascii=False)), "  "))
    subject_rules = _meta_list(subject_meta, "rules")
    if subject_rules:
        current_dataview.append("- subject_rules:")
        current_dataview.extend(f"  - {item}" for item in subject_rules)
    if view.get("kd"):
        current_dataview.append(f"- kd_methods: {_format_kd_methods(view.get('kd'))}")
    computed = view.get("computed")
    if isinstance(computed, Mapping):
        current_dataview.append("- computed:")
        current_dataview.append(_indent(_code_block(json.dumps(computed, ensure_ascii=False)), "  "))
    api_lines: list[str] = []
    for row in _api_entries(step, view):
        if api_lines:
            api_lines.append("")
        api_lines.append(f"## `{row['api_name']}`")
        api_lines.append(f"- api_class: `{row['api_class']}`")
        api_lines.append(f"- api_function: {row['api_function']}")
    return {
        "current_step": _code_block(f"{step.step_id} | {step.subject} | {step.dataview} | {step.condition_desc}"),
        "request_types": "\n".join(request_type_lines) if request_type_lines else "none",
        "current_dataview": "\n".join(current_dataview),
        "available_apis": "\n".join(api_lines) if api_lines else "none",
        "supported_metrics": _format_supported_metrics(api_classes),
        "previous_results": _format_session_results(previous_results),
        "session_results": _format_session_results(previous_results),
        "validation_feedback": _format_feedback(validation_feedback),
        "required_result_id": f"`{result_id}`" if result_id else "none",
    }


def format_subject_dataviews_for_steps(steps: Iterable[Step]) -> str:
    subjects: list[str] = []
    used_dataviews: dict[str, set[str]] = {}
    for step in steps:
        if step.subject not in subjects:
            subjects.append(step.subject)
        used_dataviews.setdefault(step.subject, set()).add(step.dataview)
    if not subjects:
        return "none"

    blocks: list[str] = []
    for subject in subjects:
        subject_cfg = _subject_config(subject)
        if not subject_cfg:
            blocks.append(f"## `{subject}`\n- unavailable")
            continue
        subject_meta = subject_cfg.get("_meta")
        lines = [f"## `{subject}`"]
        desc = _meta_text(subject_meta, "desc")
        if desc:
            lines.append(f"- desc: {desc}")
        subject_rules = _meta_list(subject_meta, "rules")
        if subject_rules:
            lines.append("- rules:")
            lines.extend(f"  - {item}" for item in subject_rules)
        lines.append("- dataviews:")
        used_for_subject = used_dataviews.get(subject, set())
        for dataview, view in subject_cfg.items():
            if str(dataview).startswith("_") or not isinstance(view, Mapping):
                continue
            marker = " (used in previous steps)" if dataview in _dataview_aliases(used_for_subject) else ""
            lines.append(f"  - `{dataview}`{marker}: {view.get('desc') or ''}")
            field_names = _format_field_names(view.get("fields"))
            if field_names:
                lines.append(f"    fields: {field_names}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _api_classes(step: Step, view: Mapping[str, Any]) -> list[str]:
    rows: list[str] = []
    for item in _api_entries(step, view):
        class_name = str(item.get("api_class") or "").strip()
        if class_name and class_name not in rows:
            rows.append(class_name)
    return rows


def _format_api_class(class_name: str, *, include_metrics: bool = True) -> list[str]:
    cfg = _api_class_patterns().get(class_name)
    if not isinstance(cfg, Mapping):
        return [f"## `{class_name}`", "", "- unavailable"]
    rows = [
        f"## `{class_name}`",
        "",
        f"- purpose: {cfg.get('desc') or ''}",
        "- call_pattern:",
        _code_block(str(cfg.get("call_pattern") or "")),
    ]
    examples = [str(item) for item in (cfg.get("examples") or [])]
    if examples:
        rows.append("- examples:")
        for index, example in enumerate(examples, start=1):
            request, notes = _split_example_notes(example)
            rows.append(f"  - example {index}:")
            rows.append("    ```text")
            rows.append(f"    {request}")
            rows.append("    ```")
            for note in notes:
                rows.append(f"    - note: {note}")
    if cfg.get("output_rule"):
        rows.append(f"- output_rule: {cfg['output_rule']}")
    args = cfg.get("args")
    if isinstance(args, Mapping):
        rows.append("- args:")
        for key in ["required", "optional"]:
            values = args.get(key)
            if values:
                rows.append(f"  - {key}: {', '.join(str(item) for item in values)}")
    methods = cfg.get("methods")
    if methods:
        rows.append(f"- methods: {', '.join(str(item) for item in methods)}")
    rules = cfg.get("rules")
    if isinstance(rules, list) and rules:
        rows.append("- rules:")
        rows.extend(f"  - {item}" for item in rules)
    return rows


def _format_supported_metrics(api_classes: list[str]) -> str:
    rows: list[str] = []
    for class_name in api_classes:
        cfg = _api_class_patterns().get(class_name)
        methods = cfg.get("methods") if isinstance(cfg, Mapping) else None
        if not methods:
            continue
        if rows:
            rows.append("")
        rows.append(f"## `{class_name}`")
        rows.append(f"- methods: {', '.join(str(item) for item in methods)}")
    return "\n".join(rows) if rows else "none"


def _format_kd_methods(kd: Any) -> str:
    if isinstance(kd, Mapping):
        return "; ".join(f"{field}: {', '.join(str(method) for method in methods)}" for field, methods in kd.items())
    if isinstance(kd, list):
        return ", ".join(str(item) for item in kd)
    return str(kd or "")


def _format_session_results(previous_results: Mapping[str, ResultHandle]) -> str:
    if not previous_results:
        return ""
    lines: list[str] = []
    for handle in previous_results.values():
        if lines:
            lines.append("")
        step_label = _step_label(handle)
        lines.append(f"## {step_label} / `{handle.name}`")
        task = _result_task(handle)
        if task:
            lines.append(f"- task: {task}")
        lines.append(f"- api: `{handle.api}`")
        lines.append("- result_schema:")
        lines.append(_indent(_code_block(", ".join(handle.columns)), "  "))
        status = _result_status(handle)
        if status:
            lines.append(f"- status: {status}")
        row_count = _result_row_count(handle)
        if row_count is not None:
            lines.append(f"- row_count: {row_count}")
        preview_rows = _result_preview_rows(handle)
        if preview_rows:
            lines.append("- first_3_rows:")
            lines.append(_indent("```json\n" + json.dumps(preview_rows, ensure_ascii=False, default=str, indent=2) + "\n```", "  "))
    return "\n".join(lines)


def _step_label(handle: ResultHandle) -> str:
    if handle.step_id:
        match = re.search(r"(\d+)", handle.step_id)
        if match:
            return f"Step {match.group(1)}"
        return handle.step_id
    match = re.fullmatch(r"r(\d+)", handle.name)
    return f"Step {match.group(1)}" if match else "Step"


def _result_task(handle: ResultHandle) -> str:
    if handle.task:
        return handle.task
    if isinstance(handle.data, Mapping):
        return str(handle.data.get("step_task") or handle.data.get("task") or "")
    return ""


def _format_feedback(validation_feedback: Iterable[str] | None) -> str:
    feedback = [str(item).strip() for item in (validation_feedback or []) if str(item).strip()]
    if not feedback:
        return ""
    return "\n".join(f"- {item}" for item in feedback)


def _code_block(text: str) -> str:
    return "```text\n" + str(text or "").strip() + "\n```"


def _indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line if line else line for line in text.splitlines())


def _split_example_notes(example: str) -> tuple[str, list[str]]:
    request_lines: list[str] = []
    notes: list[str] = []
    for line in str(example or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("note:"):
            notes.append(stripped.split("note:", 1)[1].strip())
        elif stripped:
            request_lines.append(stripped)
    return " ".join(request_lines), notes


def _api_entries(step: Step, view: Mapping[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in view.get("api") or []:
        if not isinstance(item, Mapping):
            continue
        rows.append(
            {
                "api_name": str(item.get("api_name") or "").strip(),
                "api_function": str(item.get("api_function") or "").strip(),
                "api_class": str(item.get("api_class") or "").strip(),
            }
        )
    return [row for row in rows if row["api_name"] and row["api_class"]]


def _format_fields(fields: Any) -> str:
    if not isinstance(fields, Mapping):
        return ""
    rows: list[str] = []
    for field_name, aliases in fields.items():
        if isinstance(aliases, list) and aliases:
            rows.append(f"{field_name}: {', '.join(str(item) for item in aliases)}")
        else:
            rows.append(str(field_name))
    return "\n".join(rows)


def _format_field_names(fields: Any) -> str:
    if not isinstance(fields, Mapping):
        return ""
    return ", ".join(str(field_name) for field_name in fields.keys())


def _dataview_aliases(dataviews: set[str]) -> set[str]:
    aliases = set(dataviews)
    if "base_info" in aliases:
        aliases.add("basic_info")
    if "basic_info" in aliases:
        aliases.add("base_info")
    return aliases


def _meta_text(meta: Any, key: str) -> str:
    return str(meta.get(key) or "") if isinstance(meta, Mapping) else ""


def _meta_list(meta: Any, key: str) -> list[str]:
    if not isinstance(meta, Mapping):
        return []
    value = meta.get(key)
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _result_status(handle: ResultHandle) -> str:
    if isinstance(handle.data, Mapping):
        return str(handle.data.get("status") or "")
    return ""


def _result_row_count(handle: ResultHandle) -> int | None:
    if not isinstance(handle.data, Mapping):
        return None
    if "row_count" in handle.data:
        try:
            return int(handle.data["row_count"])
        except (TypeError, ValueError):
            return None
    rows = handle.data.get("rows")
    return len(rows) if isinstance(rows, list) else None


def _result_preview_rows(handle: ResultHandle) -> list[Mapping[str, Any]]:
    if not isinstance(handle.data, Mapping):
        return []
    rows = handle.data.get("rows")
    if not isinstance(rows, list):
        return []
    return [row for row in rows[:3] if isinstance(row, Mapping)]
