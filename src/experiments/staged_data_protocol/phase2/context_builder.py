from __future__ import annotations
import json
import re
from typing import Any, Iterable, Mapping

from src.experiments.staged_data_protocol.phase2.catalog import catalog_source, OPERATION_DESCRIPTIONS
from src.experiments.staged_data_protocol.phase2.models import ResultHandle, Step
from src.services.finance_data_tool_catalog_service import FinanceDataToolCatalogService


_MODEL_CATALOG = FinanceDataToolCatalogService()


def _prompt_catalog() -> Mapping[str, Any]:
    return catalog_source()


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
    # Legacy staged execution uses the same assembled contracts as CC/DSH.
    view_name = "basic_info" if step.dataview == "base_info" and "basic_info" in _subject_config(step.subject) else step.dataview
    model = _MODEL_CATALOG.get_model_dataview(step.subject, view_name)
    functions = model.pop("functions")
    current_dataview = {"subject": step.subject, **model}
    api_lines: list[str] = []
    for row in functions:
        if api_lines:
            api_lines.append("")
        api_lines.append(f"## `{row['api_name']}`")
        api_lines.append(_code_block(json.dumps(row, ensure_ascii=False)))
    return {
        "current_step": _code_block(f"{step.step_id} | {step.subject} | {step.dataview} | {step.condition_desc}"),
        "request_types": "\n".join(f"- {name}: {desc}" for name, desc in OPERATION_DESCRIPTIONS.items()),
        "current_dataview": _code_block(json.dumps(current_dataview, ensure_ascii=False)),
        "available_apis": "\n".join(api_lines) if api_lines else "none",
        "supported_metrics": "",  # Methods are included in each complete API contract.
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
    if not isinstance(value, (list, tuple)):
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
