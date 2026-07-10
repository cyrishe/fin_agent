from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping

from src.experiments.staged_data_protocol.phase2.call_parser import parse_api_call
from src.experiments.staged_data_protocol.phase2.call_validator import validate_call
from src.experiments.staged_data_protocol.phase2.context_builder import (
    build_context_sections,
    build_context_text,
    format_subject_dataviews_for_steps,
)
from src.experiments.staged_data_protocol.phase2.api_runner import execute_api_call
from src.experiments.staged_data_protocol.phase2.models import ApiCall, ResultHandle, Step
from src.experiments.staged_data_protocol.phase2.step_parser import parse_steps


PROMPT_PATH = Path("phase2_api_prompt.md")
FINAL_CHECK_PROMPT_PATH = Path("phase2_final_check_prompt.md")
PHASE1_REPAIR_APPENDIX_PATH = Path("phase1_check_repair_appendix.md")


def run_phase2_steps(
    *,
    question: str,
    step_lines: Iterable[str],
    use_llm: bool = False,
    max_iterations: int | None = None,
    enable_final_check: bool = False,
    max_final_checks: int = 3,
) -> Dict[str, Any]:
    steps = parse_steps(step_lines)
    previous_results: Dict[str, ResultHandle] = {}
    feedback_by_index: Dict[int, List[str]] = {}
    attempts_by_index: Dict[int, int] = {}
    rows: List[Dict[str, Any]] = []
    final_checks: List[Dict[str, Any]] = []
    index = 0
    iterations = 0
    final_check_enabled = bool(enable_final_check and use_llm)
    base_max_loops = max(len(steps) * 4, 4)
    final_check_budget = max(0, int(max_final_checks or 0)) * max(len(steps) + 1, 1) if final_check_enabled else 0
    max_loops = max_iterations or (base_max_loops + final_check_budget)
    while iterations < max_loops:
        iterations += 1
        if index >= len(steps):
            if not final_check_enabled or not steps or len(final_checks) >= max(0, int(max_final_checks or 0)):
                break
            if not all(f"r{step_index}" in previous_results for step_index in range(1, len(steps) + 1)):
                break
            check_prompt = build_final_check_prompt(
                question=question,
                steps=steps,
                calls=rows,
                previous_results=previous_results,
            )
            raw_check = _call_final_check_llm(check_prompt)
            check = parse_final_check_response(raw_check)
            check_row = {
                "status": check["status"],
                "feedback": check.get("feedback") or "",
                "raw_response": raw_check,
                "prompt": check_prompt,
            }
            final_checks.append(check_row)
            if check["status"] == "OK":
                break
            repair_prompt = build_phase1_repair_prompt(
                question=question,
                steps=steps,
                calls=rows,
                previous_results=previous_results,
                final_check_feedback=check.get("feedback") or "",
            )
            raw_repair = _call_phase1_repair_llm(repair_prompt)
            repair = parse_phase1_repair_response(raw_repair)
            check_row["repair_prompt"] = repair_prompt
            check_row["repair_raw_response"] = raw_repair
            check_row["repair_steps"] = repair.get("steps") or []
            check_row["repair_raw_steps"] = repair.get("raw_steps") or repair.get("steps") or []
            check_row["repair_step_flags"] = repair.get("step_flags") or []
            if repair.get("status") != "OK":
                check_row["repair_error"] = repair.get("feedback") or "phase1 repair failed"
                break
            repaired_steps = parse_steps(repair["steps"])
            changed_index = _first_flagged_step_index(repair.get("step_flags") or [])
            if changed_index is None:
                changed_index = _first_changed_step_index(steps, repaired_steps)
            if changed_index is None:
                changed_index = _final_check_target_index(steps)
                check_row["repair_note"] = "phase1 repair kept steps unchanged; rerun target step with final check feedback"
                repaired_steps = steps
            steps = repaired_steps
            _drop_results_from(previous_results, changed_index + 1)
            _drop_calls_from(rows, changed_index + 1)
            feedback_by_index = {key: value for key, value in feedback_by_index.items() if key < changed_index}
            attempts_by_index = {key: value for key, value in attempts_by_index.items() if key < changed_index}
            feedback_by_index.setdefault(changed_index, []).append(f"FINAL_CHECK_FEEDBACK: {check.get('feedback') or ''}")
            index = changed_index
            continue

        step = steps[index]
        result_id = f"r{index + 1}"
        feedback = feedback_by_index.get(index, [])
        context_sections = build_context_sections(
            step=step,
            previous_results=previous_results,
            validation_feedback=feedback,
            result_id=result_id,
        )
        context = build_context_text(
            step=step,
            previous_results=previous_results,
            validation_feedback=feedback,
            result_id=result_id,
        )
        phase2_prompt = build_phase2_prompt(question=question, context_sections=context_sections)
        raw_response = (
            _call_llm(question=question, context_sections=context_sections)
            if use_llm
            else _rule_call(result_id, step, previous_results)
        )
        response = parse_phase2_response(raw_response)
        row: Dict[str, Any]
        if response["status"] == "roll_back":
            issue = str(response.get("res") or "").strip() or "phase2 requested rollback"
            invalid_rollback_feedback = _invalid_rollback_feedback(issue, previous_results)
            if invalid_rollback_feedback:
                row = {
                    "raw_call": raw_response,
                    "phase2_response": response,
                    "call": None,
                    "validation": {
                        "ok": False,
                        "errors": [f"ROLLBACK_RULE_ERROR: {issue}"],
                        "warnings": [],
                        "feedback": [invalid_rollback_feedback],
                    },
                    "execution_result": None,
                }
                row.update(
                    {
                        "step": step.raw,
                        "api_context": context,
                        "api_context_sections": context_sections,
                        "phase2_prompt": phase2_prompt,
                    }
                )
                rows.append(row)
                attempts_by_index[index] = attempts_by_index.get(index, 0) + 1
                feedback_by_index.setdefault(index, []).append(invalid_rollback_feedback)
                if attempts_by_index[index] >= 2:
                    index += 1
                continue
            target_index = max(index - 1, 0)
            feedback_by_index.setdefault(target_index, []).append(f"ROLLBACK_FROM_{result_id}: {issue}")
            _drop_results_from(previous_results, target_index + 1)
            row = {
                "raw_call": raw_response,
                "phase2_response": response,
                "call": None,
                "validation": {"ok": False, "errors": [f"ROLLBACK: {issue}"], "warnings": []},
                "execution_result": None,
                "rollback_to": f"S{target_index + 1}",
            }
            row.update(
                {
                    "step": step.raw,
                    "api_context": context,
                    "api_context_sections": context_sections,
                    "phase2_prompt": phase2_prompt,
                }
            )
            rows.append(row)
            index = target_index
            continue

        row = _parse_validate_execute(response.get("res"), previous_results, expected_result_id=result_id)
        row.update(
            {
                "step": step.raw,
                "api_context": context,
                "api_context_sections": context_sections,
                "phase2_prompt": phase2_prompt,
                "phase2_response": response,
            }
        )
        if row["validation"]["ok"]:
            previous_results[row["call"]["result_id"]] = ResultHandle(
                name=row["execution_result"]["name"],
                api=row["execution_result"]["api"],
                columns=row["execution_result"]["columns"],
                data=row["execution_result"]["data"],
                step_id=step.step_id,
                task=step.condition_desc,
            )
            feedback_by_index.pop(index, None)
            index += 1
        else:
            attempts_by_index[index] = attempts_by_index.get(index, 0) + 1
            feedback_by_index.setdefault(index, []).extend(_static_validation_feedback(row))
            if attempts_by_index[index] >= 2:
                index += 1
        rows.append(row)
    exhausted = index < len(steps)
    all_results_ready = all(f"r{step_index}" in previous_results for step_index in range(1, len(steps) + 1))
    final_check_status = final_checks[-1]["status"] if final_checks else ("missing" if final_check_enabled else "disabled")
    final_check_ok = final_check_status in {"disabled", "OK"}
    return {
        "question": question,
        "use_llm": use_llm,
        "steps": [step.raw for step in steps],
        "calls": rows,
        "final_checks": final_checks,
        "final_check_status": final_check_status,
        "loop_exhausted": exhausted,
        "valid": (not exhausted) and all_results_ready and final_check_ok,
    }


def parse_phase2_response(raw_response: str) -> Dict[str, Any]:
    text = _strip_fence(str(raw_response or "").strip())
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"status": "ok", "res": text, "raw": raw_response}
    if not isinstance(parsed, Mapping):
        return {"status": "ok", "res": text, "raw": raw_response}
    status = str(parsed.get("status") or "ok").strip().lower()
    if status not in {"ok", "roll_back"}:
        status = "ok"
    return {"status": status, "res": parsed.get("res") or "", "raw": raw_response}


def parse_final_check_response(raw_response: str) -> Dict[str, str]:
    text = _strip_fence(str(raw_response or "").strip())
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"status": "need_check", "feedback": f"final check did not return valid JSON: {text[:200]}"}
    if not isinstance(parsed, Mapping):
        return {"status": "need_check", "feedback": "final check response is not an object"}
    status = str(parsed.get("status") or "").strip().lower().replace(" ", "_")
    if status == "ok":
        return {"status": "OK", "feedback": str(parsed.get("feedback") or "").strip()}
    return {"status": "need_check", "feedback": str(parsed.get("feedback") or "").strip()}


def parse_phase1_repair_response(raw_response: str) -> Dict[str, Any]:
    text = _strip_fence(str(raw_response or "").strip())
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"status": "error", "feedback": f"phase1 repair did not return valid JSON: {text[:200]}", "steps": []}
    if not isinstance(parsed, Mapping):
        return {"status": "error", "feedback": "phase1 repair response is not an object", "steps": []}
    steps = parsed.get("steps")
    if not isinstance(steps, list) or not all(isinstance(item, str) and item.strip() for item in steps):
        return {"status": "error", "feedback": "phase1 repair response requires a non-empty string steps list", "steps": []}
    normalized_steps, step_flags = _normalize_repair_steps(steps)
    try:
        parse_steps(normalized_steps)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "feedback": f"phase1 repair steps are invalid: {exc}", "steps": steps}
    return {
        "status": "OK",
        "feedback": str(parsed.get("analyze") or parsed.get("feedback") or "").strip(),
        "steps": normalized_steps,
        "raw_steps": steps,
        "step_flags": step_flags,
    }


def _normalize_repair_steps(steps: Iterable[str]) -> tuple[list[str], list[int | None]]:
    normalized_steps: list[str] = []
    step_flags: list[int | None] = []
    for step in steps:
        parts = [part.strip() for part in str(step or "").strip().split("|")]
        flag: int | None = None
        if len(parts) >= 5 and parts[-1] in {"0", "1"}:
            flag = int(parts.pop())
        normalized_steps.append(" | ".join(parts))
        step_flags.append(flag)
    return normalized_steps, step_flags


def _strip_fence(text: str) -> str:
    fenced = re.search(r"```(?:json|text)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    return fenced.group(1).strip() if fenced else text


def _drop_results_from(previous_results: Dict[str, ResultHandle], step_number: int) -> None:
    for name in list(previous_results):
        match = re.fullmatch(r"r(\d+)", name)
        if match and int(match.group(1)) >= step_number:
            previous_results.pop(name, None)


def _parse_validate_execute(
    request_payload: Any,
    previous_results: Mapping[str, ResultHandle],
    *,
    expected_result_id: str = "",
) -> Dict[str, Any]:
    try:
        call = _api_call_from_payload(request_payload)
    except Exception as exc:  # noqa: BLE001 - experiment should preserve parse errors.
        return {
            "raw_call": request_payload,
            "call": None,
            "validation": {"ok": False, "errors": [f"PARSE_ERROR: {exc}"], "warnings": []},
            "execution_result": None,
        }
    validation = validate_call(call, previous_results)
    if expected_result_id and call.result_id != expected_result_id:
        validation.ok = False
        validation.errors.append(f"RESULT_ID_ERROR: expected {expected_result_id}, got {call.result_id}")
    result = execute_api_call(call, previous_results=previous_results) if validation.ok else None
    validation_errors = list(validation.errors)
    validation_feedback = [
        _format_static_validation_error(error, call={"result_id": call.result_id, "api": call.api, "args": call.args, "outputs": call.outputs})
        for error in validation_errors
    ]
    return {
        "raw_call": call.raw,
        "call": {"result_id": call.result_id, "api": call.api, "args": call.args, "outputs": call.outputs},
        "validation": {
            "ok": validation.ok,
            "errors": validation_errors,
            "warnings": validation.warnings,
            "feedback": validation_feedback,
        },
        "execution_result": {
            "name": result.name,
            "api": result.api,
            "columns": result.columns,
            "data": result.data,
        }
        if result
        else None,
    }


def _static_validation_feedback(row: Mapping[str, Any]) -> List[str]:
    validation = row.get("validation") if isinstance(row.get("validation"), Mapping) else {}
    existing_feedback = validation.get("feedback") if isinstance(validation.get("feedback"), list) else []
    if existing_feedback:
        return [str(item).strip() for item in existing_feedback if str(item).strip()]
    call = row.get("call") if isinstance(row.get("call"), Mapping) else {}
    return [
        _format_static_validation_error(str(error), call=call)
        for error in (validation.get("errors") or [])
        if str(error).strip()
    ]


def _format_static_validation_error(error: str, *, call: Mapping[str, Any] | None = None) -> str:
    text = str(error or "").strip()
    current_call = call if isinstance(call, Mapping) else {}
    api = str(current_call.get("api") or "").strip()
    prefix = "STATIC_VALIDATION_FEEDBACK"

    match = re.search(r"^PARSE_ERROR:\s*(.+)$", text)
    if match:
        return (
            f"{prefix}: The response is not a valid API request string. "
            "Return exactly one string like `rN = subject.dataview(args) -> field1, field2` in `res`."
        )

    match = re.search(r"^API_ERROR:\s*unsupported api=(.+)$", text)
    if match:
        bad_api = match.group(1).strip()
        return f"{prefix}: `{bad_api}` is not an available API for this step. Choose one API listed in AVAILABLE APIS."

    match = re.search(r"^API_ERROR:\s*(.+?) unsupported\. available=(.+)$", text)
    if match:
        bad_api = match.group(1).strip()
        available = match.group(2).strip()
        return f"{prefix}: `{bad_api}` is not a supported k-day API. Use one supported method from this list: {available}."

    match = re.search(r"^API_ERROR:\s*agg=(.+?) unsupported for metric=(.+)$", text)
    if match:
        agg = match.group(1).strip()
        metric = match.group(2).strip()
        return f"{prefix}: `{metric}` is a standard metric only with its listed aggregate methods; `{agg}` is not supported for it."

    match = re.search(r"^API_ERROR:\s*metric=(.+?) unsupported$", text)
    if match:
        metric = match.group(1).strip()
        hint = " PE/PB/value metrics should usually use `stock.pricevalue.<field>`, not `stock.quote.<field>`." if ".quote." in metric else ""
        return f"{prefix}: metric `{metric}` is not a valid dataview field path. Use `subject.dataview.field` from the API catalog.{hint}"

    if "agg api requires agg like" in text:
        return f"{prefix}: agg APIs require `agg = method(metric)`, for example `avg(stock.quote.pct)`."

    if "constitution.agg requires group_by" in text:
        return f"{prefix}: `constitution.agg` requires `group_by`; use fields from CURRENT DATAVIEW, such as subject code and name."

    match = re.search(r"metric result=(r\d+) must include code or stock_code", text)
    if match:
        return f"{prefix}: `{match.group(1)}` cannot be used as constitution.agg metric because it has no `code` or `stock_code` join column."

    match = re.search(r"group_by field=(.+?) not in constitution fields; available=(.+)$", text)
    if match:
        return f"{prefix}: group_by field `{match.group(1).strip()}` is invalid. Use only constitution fields: {match.group(2).strip()}."

    if "agg output must include" in text:
        return f"{prefix}: aggregate output should include the group fields and one alias field, for example `industry_code, industry_name, avg_pct`."

    match = re.search(r"aggregate output metric=(.+?) expected (.+)$", text)
    if match:
        return f"{prefix}: aggregate output must use metric column `{match.group(2).strip()}`, not `{match.group(1).strip()}`."

    if "kd api requires positive integer k" in text:
        return f"{prefix}: k-day APIs require `k` as a positive integer, for example `k = 5`."

    if "dynamic_cal requires task" in text:
        return f"{prefix}: `dynamic_cal` requires a concise natural-language `task` argument describing the calculation."

    match = re.search(r"dynamic_cal fields contains unknown field=(.+)$", text)
    if match:
        field_name = match.group(1).strip()
        return f"{prefix}: `{field_name}` is not a standard quote field. Use only fields listed in CURRENT DATAVIEW."

    match = re.search(r"limit must be an integer, got=(.+)$", text)
    if match:
        value = match.group(1).strip()
        return f"{prefix}: `limit` must be an integer; `{value}` is invalid. Omit limit if no exact number is required."

    match = re.search(r"^REF_ERROR:\s*unknown result=(r\d+)$", text)
    if match:
        result_id = match.group(1)
        return f"{prefix}: `{result_id}` is not available in PREVIOUS RESULTS. Only reference listed previous results."

    if "must use rN.column refs, not SQL subquery refs" in text:
        return f"{prefix}: Do not use SQL subqueries for dependencies. Reference previous results as `rN.column`."

    match = re.search(r"^COLUMN_ERROR:\s*(r\d+) has no column=(.+?); available=(.+)$", text)
    if match:
        result_id = match.group(1).strip()
        column = match.group(2).strip()
        available = match.group(3).strip()
        return f"{prefix}: `{column}` is not a column of `{result_id}`. Use only these previous-result columns: {available}."

    match = re.search(r"^OUTPUT_ERROR:\s*field=(.+?) not in api=(.+?); available=(.+)$", text)
    if match:
        field_name = match.group(1).strip()
        error_api = match.group(2).strip()
        available = match.group(3).strip()
        return f"{prefix}: `{field_name}` is not a standard output field of `{error_api}`. Choose output fields from CURRENT DATAVIEW: {available}."

    if "request must declare output fields" in text:
        return f"{prefix}: The request must declare at least one output field after `->`."

    match = re.search(r"^RESULT_ID_ERROR:\s*expected\s+(r\d+),\s*got\s+(.+)$", text)
    if match:
        expected = match.group(1).strip()
        got = match.group(2).strip()
        return f"{prefix}: The result id must be `{expected}` for this step; `{got}` is invalid."

    if api:
        return f"{prefix}: `{api}` failed static validation: {text}"
    return f"{prefix}: {text}"


def _invalid_rollback_feedback(issue: str, previous_results: Mapping[str, ResultHandle]) -> str:
    if not previous_results:
        return ""
    text = str(issue or "").strip().lower()
    if not text:
        return ""
    empty_result_signals = [
        "row_count=0",
        "row_count = 0",
        "0 rows",
        "empty",
        "no rows",
        "no data available",
        "no industry data",
        "no industry list",
        "no values are available",
    ]
    if not any(signal in text for signal in empty_result_signals):
        return ""
    return (
        "STATIC_VALIDATION_FEEDBACK: Do not roll_back only because a previous result has zero rows, "
        "prepared status, or a pending provider. If the previous result schema lists the required "
        "column, generate the current request using `rN.column`."
    )


def _api_call_from_payload(payload: Any) -> ApiCall:
    if isinstance(payload, str):
        return parse_api_call(payload)
    if not isinstance(payload, Mapping):
        raise ValueError("request payload must be an object")
    result_id = str(payload.get("result_id") or "").strip()
    api = str(payload.get("api") or "").strip()
    args = payload.get("args")
    outputs = payload.get("outputs")
    if not result_id or not api:
        raise ValueError("request object requires result_id and api")
    if args is None:
        args = {}
    if not isinstance(args, Mapping):
        raise ValueError("request object args must be an object")
    if not isinstance(outputs, list) or not all(isinstance(item, str) and item.strip() for item in outputs):
        raise ValueError("request object outputs must be a non-empty string array")
    clean_args = {str(key): value for key, value in args.items() if value not in (None, "")}
    clean_outputs = [str(item).strip() for item in outputs]
    return ApiCall(
        result_id=result_id,
        api=api,
        args=clean_args,
        outputs=clean_outputs,
        raw=_request_string(result_id=result_id, api=api, args=clean_args, outputs=clean_outputs),
    )


def _request_string(*, result_id: str, api: str, args: Mapping[str, Any], outputs: list[str]) -> str:
    args_text = ", ".join(f"{key} = {_format_arg_value(value)}" for key, value in args.items())
    return f"{result_id} = {api}({args_text}) -> {', '.join(outputs)}"


def _format_arg_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def _rule_call(result_id: str, step: Step, previous_results: Mapping[str, ResultHandle]) -> str:
    desc = step.condition_desc
    api = f"{step.subject}.{step.dataview}"
    filter_text = _default_filter(desc, previous_results)
    limit = _extract_limit(desc)
    order = "none"
    outputs = _default_outputs(step.subject, step.dataview)
    if step.dataview == "constitution" and _needs_agg(desc):
        metric, agg, alias = _agg_metric(desc)
        group_by = f"{step.subject}_code, {step.subject}_name"
        return (
            f'{result_id} = {api}.agg(filter = "{filter_text}", agg = {agg}({metric}), '
            f'group_by = "{group_by}", order = "{alias} desc", limit = -1) -> '
            f"{step.subject}_code, {step.subject}_name, {alias}"
        )
    k = _extract_k(desc)
    if k and step.dataview == "quote" and _mentions_pct(desc):
        api = f"{api}.kd_pct_sum"
        order = "value desc" if any(word in desc for word in ["最高", "前", "最大"]) else "none"
        return f'{result_id} = {api}(k = {k}, filter = "{filter_text}", order = "{order}", limit = {limit}) -> code, name, value as pct_sum_{k}d'
    return f'{result_id} = {api}(filter = "{filter_text}", order = "{order}", limit = {limit}) -> {", ".join(outputs)}'


def _call_llm(*, question: str, context_sections: Mapping[str, str]) -> str:
    from src.utils.ai_service import chat_qwen_flash

    prompt = build_phase2_prompt(question=question, context_sections=context_sections)
    raw, _usage = chat_qwen_flash([{"role": "user", "content": prompt}], enable_think=False)
    return str(raw or "").strip()


def _call_final_check_llm(prompt: str) -> str:
    from src.utils.ai_service import chat_qwen_flash

    raw, _usage = chat_qwen_flash([{"role": "user", "content": prompt}], enable_think=False)
    return str(raw or "").strip()


def _call_phase1_repair_llm(prompt: str) -> str:
    from src.utils.ai_service import chat_qwen_flash

    raw, _usage = chat_qwen_flash([{"role": "user", "content": prompt}], enable_think=False)
    return str(raw or "").strip()


def build_phase2_prompt(*, question: str, context_sections: Mapping[str, str]) -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    replacements = {"question": question, **{key: str(value) for key, value in context_sections.items()}}
    prompt = template
    for key, value in replacements.items():
        prompt = prompt.replace("{{" + key + "}}", value)
    prompt = prompt.replace("{{api_context}}", "\n\n".join(context_sections.values()))
    prompt = _remove_empty_optional_section(prompt, "SESSION RESULTS")
    prompt = _remove_empty_optional_section(prompt, "PREVIOUS RESULTS")
    prompt = _remove_empty_optional_section(prompt, "VALIDATION FEEDBACK")
    return prompt


def build_phase1_repair_prompt(
    *,
    question: str,
    steps: Iterable[Step],
    calls: Iterable[Mapping[str, Any]],
    previous_results: Mapping[str, ResultHandle],
    final_check_feedback: str,
) -> str:
    steps = list(steps)
    prompt = PHASE1_REPAIR_APPENDIX_PATH.read_text(encoding="utf-8")
    has_subject_dataview_placeholder = "{{subject_data_views}}" in prompt or "{{subject_and_data_views}}" in prompt
    has_question_placeholder = "{{question}}" in prompt
    subject_dataviews = format_subject_dataviews_for_steps(steps)
    replacements = {
        "question": question,
        "subject_data_views": subject_dataviews,
        "subject_and_data_views": subject_dataviews,
        "previous_steps": _format_final_check_steps(steps),
        "result_schemas": _format_final_check_results(previous_results),
        "final_check_feedback": final_check_feedback,
    }
    for key, value in replacements.items():
        prompt = prompt.replace("{{" + key + "}}", value)
    if not has_subject_dataview_placeholder:
        prompt = _inject_subject_dataview_section(prompt, subject_dataviews)
    if not has_question_placeholder:
        prompt = _ensure_repair_prompt_question(prompt, question)
    prompt = _ensure_phase1_repair_output_contract(prompt)
    return prompt


def build_final_check_prompt(
    *,
    question: str,
    steps: Iterable[Step],
    calls: Iterable[Mapping[str, Any]],
    previous_results: Mapping[str, ResultHandle],
) -> str:
    template = FINAL_CHECK_PROMPT_PATH.read_text(encoding="utf-8")
    replacements = {
        "question": question,
        "steps": _format_final_check_steps(steps),
        "result_schemas": _format_final_check_results(previous_results),
    }
    prompt = template
    for key, value in replacements.items():
        prompt = prompt.replace("{{" + key + "}}", value)
    return prompt


def _format_final_check_steps(steps: Iterable[Step]) -> str:
    return "\n".join(f"- {step.raw}" for step in steps)


def _inject_subject_dataview_section(prompt: str, content: str) -> str:
    if not str(content or "").strip():
        return prompt
    if "{{subject_data_views}}" in prompt or "{{subject_and_data_views}}" in prompt:
        return prompt
    pattern = re.compile(r"(?im)^(#\s*subject\s+and\s+data_views\s*)$")
    return pattern.sub(lambda match: f"{match.group(1).rstrip()}\n\n{content.strip()}", prompt, count=1)


def _ensure_repair_prompt_question(prompt: str, question: str) -> str:
    if "{{question}}" in prompt or re.search(r"(?im)^#\s*Original Question\b", prompt):
        return prompt
    return prompt.rstrip() + "\n\n# Original Question\n\n" + str(question or "").strip()


def _ensure_phase1_repair_output_contract(prompt: str) -> str:
    if re.search(r"(?im)^#\s*Output\b", prompt):
        return prompt
    return (
        prompt.rstrip()
        + "\n\n# Output Format\n\n"
        + "Output JSON only. No markdown. Return the full revised `steps` list.\n\n"
        + "```json\n"
        + '{"analyze":"简短说明你如何根据反馈修复步骤","steps":["S1 | subject | dataview | condition desc"]}'
        + "\n```"
    )


def _format_final_check_requests(calls: Iterable[Mapping[str, Any]]) -> str:
    rows: list[dict[str, Any]] = []
    for call in calls:
        validation = call.get("validation") if isinstance(call.get("validation"), Mapping) else {}
        parsed = call.get("call") if isinstance(call.get("call"), Mapping) else {}
        rows.append(
            {
                "step": call.get("step"),
                "request": call.get("raw_call"),
                "api": parsed.get("api") if isinstance(parsed, Mapping) else "",
                "outputs": parsed.get("outputs") if isinstance(parsed, Mapping) else [],
                "validation_ok": bool(validation.get("ok")),
                "validation_errors": validation.get("errors") or [],
            }
        )
    return "```json\n" + json.dumps(rows, ensure_ascii=False, default=str, indent=2) + "\n```"


def _format_final_check_results(previous_results: Mapping[str, ResultHandle]) -> str:
    rows: list[dict[str, Any]] = []
    for handle in previous_results.values():
        data = handle.data if isinstance(handle.data, Mapping) else {}
        result_rows = data.get("rows") if isinstance(data, Mapping) else None
        preview = result_rows[:3] if isinstance(result_rows, list) else []
        row_count = data.get("row_count") if isinstance(data, Mapping) else None
        if row_count is None and isinstance(result_rows, list):
            row_count = len(result_rows)
        rows.append(
            {
                "result_id": handle.name,
                "step_id": handle.step_id,
                "task": handle.task,
                "api": handle.api,
                "schema": handle.columns,
                "status": data.get("status") if isinstance(data, Mapping) else "",
                "provider": data.get("provider") if isinstance(data, Mapping) else "",
                "row_count": row_count,
                "first_3_rows": preview,
            }
        )
    return "```json\n" + json.dumps(rows, ensure_ascii=False, default=str, indent=2) + "\n```"


def _final_check_target_index(steps: List[Step]) -> int:
    for index in range(len(steps) - 1, -1, -1):
        if "(output)" in steps[index].raw or "（output）" in steps[index].raw:
            return index
    return max(len(steps) - 1, 0)


def _first_changed_step_index(old_steps: List[Step], new_steps: List[Step]) -> int | None:
    max_common = min(len(old_steps), len(new_steps))
    for index in range(max_common):
        if _step_signature(old_steps[index]) != _step_signature(new_steps[index]):
            return index
    if len(old_steps) != len(new_steps):
        return max_common
    return None


def _first_flagged_step_index(flags: Iterable[int | None]) -> int | None:
    for index, flag in enumerate(flags):
        if flag == 1:
            return index
    return None


def _step_signature(step: Step) -> tuple[str, str, str]:
    return (step.subject, step.dataview, step.condition_desc)


def _drop_calls_from(rows: List[Dict[str, Any]], step_number: int) -> None:
    kept: list[Dict[str, Any]] = []
    for row in rows:
        step = str(row.get("step") or "")
        match = re.match(r"^S(\d+)\s*\|", step)
        if match and int(match.group(1)) >= step_number:
            continue
        kept.append(row)
    rows[:] = kept


def _remove_empty_optional_section(prompt: str, title: str) -> str:
    return re.sub(
        rf"\n# {re.escape(title)}\s*\n\s*(?=\n# [A-Z])",
        "\n",
        prompt,
        flags=re.IGNORECASE,
    )


def _default_filter(desc: str, previous_results: Mapping[str, ResultHandle]) -> str:
    if previous_results:
        last = list(previous_results.values())[-1]
        if "code" in last.columns:
            return f"code in {last.name}.code"
        if "stock_code" in last.columns:
            return f"stock_code in {last.name}.stock_code"
    return desc or "all"


def _extract_limit(desc: str) -> int:
    match = re.search(r"(?:前|top)\s*(\d+)", desc, flags=re.IGNORECASE)
    return int(match.group(1)) if match else -1


def _extract_k(desc: str) -> int | None:
    match = re.search(r"近\s*(\d+)\s*(?:个)?(?:交易日|日)", desc)
    return int(match.group(1)) if match else None


def _mentions_pct(desc: str) -> bool:
    return "涨幅" in desc or "涨跌幅" in desc


def _needs_agg(desc: str) -> bool:
    return any(word in desc for word in ["总", "汇总", "统计", "中位数", "均值", "最大", "最小"])


def _agg_metric(desc: str) -> tuple[str, str, str]:
    if "成交额" in desc:
        return "stock.quote.amount", "sum", "total_amount"
    if "成交量" in desc:
        return "stock.quote.volumn", "sum", "total_volumn"
    if "涨幅" in desc or "涨跌幅" in desc:
        if "中位数" in desc:
            return "stock.quote.pct", "median", "median_pct"
        if "平均" in desc or "均值" in desc:
            return "stock.quote.pct", "avg", "avg_pct"
        return "stock.quote.pct", "max", "max_pct"
    if "ROE" in desc or "roe" in desc:
        return "stock.financial_3_table.roe", "median", "median_roe"
    if "主力" in desc:
        return "stock.moneyflow.main_net", "sum", "total_main_net"
    return "stock.quote.amount", "sum", "total_amount"


def _default_outputs(subject: str, dataview: str) -> List[str]:
    if dataview == "base_info":
        if subject == "industry":
            return ["industry_code", "industry_name", "level"]
        if subject == "plate":
            return ["plate_code", "plate_name"]
        if subject == "index":
            return ["code", "name", "publisher", "index_type"]
        return ["code", "name"]
    if dataview == "quote":
        return ["code", "name", "tradedate", "open", "close", "high", "low", "pct", "amount", "volumn"]
    if dataview == "constitution":
        return [f"{subject}_code", f"{subject}_name", "stock_code", "stock_name"]
    if dataview == "moneyflow":
        return ["code", "name", "tradedate", "total_buy", "total_sell", "total_net"]
    if dataview == "pricevalue":
        if subject == "index":
            return ["code", "name", "pe", "pb", "dividend_yield", "pe_percentile"]
        if subject == "industry":
            return ["industry_code", "industry_name", "pe", "pb"]
        if subject == "plate":
            return ["plate_code", "plate_name", "pe", "pb"]
        return ["code", "name", "pe", "pb", "ps", "market_value"]
    if dataview == "financial_3_table":
        return ["code", "name", "report_date", "revenue", "profit", "total_assets", "total_liab", "debt_ratio", "operating_cashflow", "roe"]
    return ["code", "name"]
