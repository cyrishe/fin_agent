from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from src.experiments.staged_data_protocol.mock_stock_api import (
    STOCK_QUOTE_METHODS,
    mock_stock_quote_method_call,
    validate_stock_quote_method_call,
)


MODULE_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = MODULE_DIR / "prompts"
FIXTURES_DIR = MODULE_DIR / "fixtures"

ACTIONS = {"fetch", "compute", "output"}

SUBJECT_DATAVIEWS: Dict[str, List[str]] = {
    "stock": [
        "base_info",
        "quote",
        "moneyflow",
        "pricevalue",
        "tech_factors",
        "news",
        "reports",
        "ann",
    ],
    "fund": ["base_info", "quote", "moneyflow", "constitution", "news"],
    "bond": ["base_info", "quote", "rating", "news"],
    "plate": ["base_info", "quote", "moneyflow", "constitution", "news"],
    "industry": ["base_info", "quote", "money_flow", "pricevalue", "constitution", "report"],
    "hot_concept": ["base_info", "constitution", "event", "heat"],
    "index": ["base_info", "quote", "moneyflow", "pricevalue", "constitution", "news"],
}

API_REFERENCES: Dict[tuple[str, str], Dict[str, Any]] = {
    ("stock", "base_info"): {
        "api_id": "stock.base_info",
        "tool_name": "stock_profile_query",
        "operations": ["query"],
        "filter_capabilities": ["name", "code", "industry", "member_ref"],
        "output_fields": ["code", "name", "main_business", "industry", "core_member", "main_product"],
    },
    ("stock", "quote"): {
        "api_id": "stock.quote",
        "tool_name": "stock_daily_kline_query",
        "operations": ["query", "field", "rank", "window_metric"],
        "filter_capabilities": ["code", "name", "trade_date", "volume_top_n", "dependency_ref"],
        "output_fields": ["code", "name", "trade_date", "open", "close", "high", "low", "pct", "volume", "amount"],
        "method_calls": STOCK_QUOTE_METHODS,
    },
    ("stock", "pricevalue"): {
        "api_id": "stock.pricevalue",
        "tool_name": "stock_valuation_query",
        "operations": ["query", "filter"],
        "filter_capabilities": ["market_value_range", "pe_range", "pb_range", "ps_range", "dependency_ref"],
        "output_fields": ["code", "name", "pe", "pb", "ps", "market_value"],
    },
    ("stock", "moneyflow"): {
        "api_id": "stock.moneyflow",
        "tool_name": "stock_capital_flow_query",
        "operations": ["query", "rank", "window_metric"],
        "filter_capabilities": ["code", "name", "trade_date", "dependency_ref"],
        "output_fields": ["code", "name", "trade_date", "huge_buy", "huge_sell", "huge_net", "main", "total"],
        "metrics": ["kd_sum", "kd_min", "kd_max", "kd_avg", "kd_median"],
    },
    ("industry", "base_info"): {
        "api_id": "industry.base_info",
        "tool_name": "industry_profile_query",
        "operations": ["query"],
        "filter_capabilities": ["name", "code", "level", "dependency_ref"],
        "output_fields": ["code", "name", "level"],
    },
    ("industry", "quote"): {
        "api_id": "industry.quote",
        "tool_name": "industry_daily_market_query",
        "operations": ["query", "rank", "window_metric"],
        "filter_capabilities": ["code", "name", "trade_date", "dependency_ref"],
        "output_fields": ["code", "name", "trade_date", "open", "close", "high", "low", "pct", "volume", "amount"],
        "metrics": ["kd_pct_sum", "kd_min", "kd_max", "kd_avg", "kd_median"],
    },
    ("industry", "constitution"): {
        "api_id": "industry.constitution",
        "tool_name": "industry_constituents_query",
        "operations": ["query"],
        "filter_capabilities": ["industry_code", "industry_name", "stock_code", "stock_name", "dependency_ref"],
        "output_fields": ["industry_code", "industry_name", "stock_code", "stock_name"],
    },
    ("industry", "money_flow"): {
        "api_id": "industry.money_flow",
        "tool_name": "industry_capital_flow_query",
        "operations": ["query", "rank", "window_metric"],
        "filter_capabilities": ["code", "name", "trade_date", "dependency_ref"],
        "output_fields": ["code", "name", "trade_date", "main", "total"],
        "metrics": ["kd_sum", "kd_min", "kd_max", "kd_avg", "kd_median"],
    },
    ("plate", "quote"): {
        "api_id": "plate.quote",
        "tool_name": "plate_daily_market_query",
        "operations": ["query", "rank", "window_metric"],
        "filter_capabilities": ["code", "name", "trade_date", "dependency_ref"],
        "output_fields": ["code", "name", "trade_date", "open", "close", "high", "low", "pct", "volume", "amount"],
        "metrics": ["kd_pct_sum", "kd_min", "kd_max", "kd_avg", "kd_median"],
    },
    ("plate", "constitution"): {
        "api_id": "plate.constitution",
        "tool_name": "plate_constituents_query",
        "operations": ["query"],
        "filter_capabilities": ["plate_code", "plate_name", "stock_code", "stock_name", "dependency_ref"],
        "output_fields": ["plate_code", "plate_name", "stock_code", "stock_name"],
    },
}


def load_cases(path: str | Path | None = None) -> List[Dict[str, Any]]:
    case_path = Path(path) if path else FIXTURES_DIR / "cases.json"
    payload = json.loads(case_path.read_text(encoding="utf-8"))
    return [dict(item) for item in payload.get("cases", []) if isinstance(item, dict)]


def render_prompt(template_name: str, context: Mapping[str, Any]) -> str:
    template = (PROMPTS_DIR / template_name).read_text(encoding="utf-8")
    rendered = template
    for key, value in context.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", str(value))
    unresolved = sorted(set(re.findall(r"{{\s*([a-zA-Z0-9_]+)\s*}}", rendered)))
    if unresolved:
        raise ValueError(f"unresolved prompt placeholders: {', '.join(unresolved)}")
    return rendered


def run_cases(
    cases: Sequence[Mapping[str, Any]],
    *,
    use_llm_stage1: bool = False,
    use_llm_stage2: bool = False,
) -> Dict[str, Any]:
    rows = [
        run_case(case, use_llm_stage1=use_llm_stage1, use_llm_stage2=use_llm_stage2)
        for case in cases
    ]
    return {
        "experiment": "staged_data_protocol",
        "use_llm_stage1": use_llm_stage1,
        "use_llm_stage2": use_llm_stage2,
        "case_count": len(rows),
        "summary": summarize(rows),
        "cases": rows,
    }


def run_case(
    case: Mapping[str, Any],
    *,
    use_llm_stage1: bool = False,
    use_llm_stage2: bool = False,
) -> Dict[str, Any]:
    question = str(case.get("question") or "").strip()
    stage1_plan = _build_stage1_plan(question, case, use_llm_stage1=use_llm_stage1)
    stage1_errors = validate_stage1_plan(stage1_plan)
    loop_iterations = _run_stage2_loop(
        question=question,
        stage1_plan=stage1_plan,
        fixture_bindings=case.get("stage2_bindings") if isinstance(case.get("stage2_bindings"), Mapping) else {},
        use_llm_stage2=use_llm_stage2,
    )
    return {
        "case_id": str(case.get("case_id") or "").strip(),
        "question": question,
        "stage1_source": "llm" if use_llm_stage1 else "fixture",
        "stage1_plan": stage1_plan,
        "stage1_validation_errors": stage1_errors,
        "loop_iterations": loop_iterations,
        "valid": not stage1_errors and all(item.get("status") == "ready" for item in loop_iterations),
    }


def validate_stage1_plan(plan: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        return ["stage1_plan.steps must be a non-empty list"]
    seen: set[str] = set()
    for index, raw_step in enumerate(steps):
        if not isinstance(raw_step, Mapping):
            errors.append(f"steps[{index}] must be an object")
            continue
        step_id = _text(raw_step.get("id"))
        action = _text(raw_step.get("action"))
        subject = _text(raw_step.get("subject"))
        dataviews = _list_text(raw_step.get("dataviews"))
        depends_on = _list_text(raw_step.get("depends_on"))
        if not step_id:
            errors.append(f"steps[{index}].id is required")
        elif step_id in seen:
            errors.append(f"{step_id}: duplicate step id")
        if action not in ACTIONS:
            errors.append(f"{step_id or index}: action must be one of {sorted(ACTIONS)}")
        if action == "fetch":
            if subject not in SUBJECT_DATAVIEWS:
                errors.append(f"{step_id}: unknown subject {subject!r}")
            allowed = set(SUBJECT_DATAVIEWS.get(subject, []))
            unknown = sorted(set(dataviews) - allowed)
            if unknown:
                errors.append(f"{step_id}: unknown dataviews for {subject}: {unknown}")
            if not dataviews:
                errors.append(f"{step_id}: fetch step requires dataviews")
        elif subject or dataviews:
            errors.append(f"{step_id}: non-fetch step should not set subject/dataviews")
        missing_deps = [dep for dep in depends_on if dep not in seen]
        if missing_deps:
            errors.append(f"{step_id}: dependencies must reference earlier steps: {missing_deps}")
        seen.add(step_id)
    return errors


def subject_dataview_context() -> str:
    return json.dumps(SUBJECT_DATAVIEWS, ensure_ascii=False, indent=2)


def api_reference_for_step(step: Mapping[str, Any]) -> List[Dict[str, Any]]:
    subject = _text(step.get("subject"))
    refs = []
    for dataview in _list_text(step.get("dataviews")):
        ref = API_REFERENCES.get((subject, dataview))
        if ref:
            refs.append(deepcopy(ref))
        else:
            refs.append(
                {
                    "api_id": f"{subject}.{dataview}",
                    "status": "missing_api_reference",
                    "subject": subject,
                    "dataview": dataview,
                }
            )
    return refs


def summarize(rows: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    total_steps = 0
    ready_steps = 0
    missing_api_steps = 0
    validation_errors = 0
    valid_cases = 0
    for row in rows:
        if row.get("valid"):
            valid_cases += 1
        validation_errors += len(row.get("stage1_validation_errors") or [])
        for item in row.get("loop_iterations") or []:
            total_steps += 1
            status = item.get("status")
            if status == "ready":
                ready_steps += 1
            if status == "missing_api":
                missing_api_steps += 1
    return {
        "valid_cases": valid_cases,
        "total_steps": total_steps,
        "ready_steps": ready_steps,
        "missing_api_steps": missing_api_steps,
        "stage1_validation_errors": validation_errors,
    }


def _build_stage1_plan(
    question: str,
    case: Mapping[str, Any],
    *,
    use_llm_stage1: bool,
) -> Dict[str, Any]:
    if not use_llm_stage1:
        return deepcopy(dict(case.get("stage1_plan") or {"steps": []}))
    prompt = render_prompt(
        "stage1_plan.md",
        {
            "subject_dataview_context": subject_dataview_context(),
            "question": question,
        },
    )
    return _call_llm_json(prompt)


def _run_stage2_loop(
    *,
    question: str,
    stage1_plan: Mapping[str, Any],
    fixture_bindings: Mapping[str, Any],
    use_llm_stage2: bool,
) -> List[Dict[str, Any]]:
    steps = [step for step in stage1_plan.get("steps", []) if isinstance(step, Mapping)]
    result_context: Dict[str, Dict[str, Any]] = {}
    iterations: List[Dict[str, Any]] = []
    for step in steps:
        step_id = _text(step.get("id"))
        deps = _list_text(step.get("depends_on"))
        dependency_context = [result_context[dep] for dep in deps if dep in result_context]
        api_refs = api_reference_for_step(step) if _text(step.get("action")) == "fetch" else []
        binding = _bind_step(
            question=question,
            stage1_plan=stage1_plan,
            step=step,
            dependency_context=dependency_context,
            api_refs=api_refs,
            fixture_binding=fixture_bindings.get(step_id),
            use_llm_stage2=use_llm_stage2,
        )
        execution = mock_execute(step=step, binding=binding)
        result_context[step_id] = execution
        iterations.append(
            {
                "step_id": step_id,
                "action": _text(step.get("action")),
                "status": binding.get("status", "needs_repair"),
                "loaded_api_refs": api_refs,
                "binding": binding,
                "mock_execution": execution,
            }
        )
    return iterations


def _bind_step(
    *,
    question: str,
    stage1_plan: Mapping[str, Any],
    step: Mapping[str, Any],
    dependency_context: Sequence[Mapping[str, Any]],
    api_refs: Sequence[Mapping[str, Any]],
    fixture_binding: Any,
    use_llm_stage2: bool,
) -> Dict[str, Any]:
    if use_llm_stage2:
        prompt = render_prompt(
            "stage2_bind_step.md",
            {
                "question": question,
                "stage1_plan_json": _json(stage1_plan),
                "current_step_json": _json(step),
                "dependency_context_json": _json(dependency_context),
                "api_reference_json": _json(api_refs),
            },
        )
        return _validate_binding(_call_llm_json(prompt))
    if isinstance(fixture_binding, Mapping):
        return _validate_binding(dict(fixture_binding))
    return _bind_step_by_rule(step=step, dependency_context=dependency_context, api_refs=api_refs)


def _bind_step_by_rule(
    *,
    step: Mapping[str, Any],
    dependency_context: Sequence[Mapping[str, Any]],
    api_refs: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    step_id = _text(step.get("id"))
    action = _text(step.get("action"))
    dependency_refs = [str(item.get("result_ref")) for item in dependency_context if item.get("result_ref")]
    if action == "fetch":
        missing = [ref for ref in api_refs if ref.get("status") == "missing_api_reference"]
        status = "missing_api" if missing else "ready"
        api_calls = [
            {
                "api_id": ref.get("api_id"),
                "tool_name": ref.get("tool_name"),
                "operation": "query",
                "arguments": {
                    "requirement": _text(step.get("requirement")),
                    "dependency_refs": dependency_refs,
                },
                "filters": [],
                "outputs": list(ref.get("output_fields") or []),
                "method_calls": [],
            }
            for ref in api_refs
            if ref.get("status") != "missing_api_reference"
        ]
        return {
            "step_id": step_id,
            "status": status,
            "executable_request": {
                "type": "fetch",
                "api_calls": api_calls,
                "compute": None,
                "output": None,
            },
            "result_schema": _merged_output_fields(api_refs),
            "dependency_refs": dependency_refs,
            "issues": [f"missing api reference: {ref.get('api_id')}" for ref in missing],
        }
    if action == "compute":
        return {
            "step_id": step_id,
            "status": "ready",
            "executable_request": {
                "type": "compute",
                "api_calls": [],
                "compute": {
                    "operation_requirement": _text(step.get("requirement")),
                    "inputs": dependency_refs,
                },
                "output": None,
            },
            "result_schema": [_text(step.get("expected_result")) or "computed_result"],
            "dependency_refs": dependency_refs,
            "issues": [],
        }
    if action == "output":
        return {
            "step_id": step_id,
            "status": "ready",
            "executable_request": {
                "type": "output",
                "api_calls": [],
                "compute": None,
                "output": {
                    "result_refs": dependency_refs,
                    "requirement": _text(step.get("requirement")),
                },
            },
            "result_schema": dependency_refs,
            "dependency_refs": dependency_refs,
            "issues": [],
        }
    return {
        "step_id": step_id,
        "status": "needs_repair",
        "executable_request": {"type": action or "unknown", "api_calls": [], "compute": None, "output": None},
        "result_schema": [],
        "dependency_refs": dependency_refs,
        "issues": [f"unsupported action: {action}"],
    }


def mock_execute(*, step: Mapping[str, Any], binding: Mapping[str, Any]) -> Dict[str, Any]:
    step_id = _text(step.get("id"))
    method_results = _mock_method_results(binding)
    method_errors = [
        error
        for result in method_results
        for error in result.get("errors", [])
    ]
    ready = binding.get("status") == "ready" and not method_errors
    return {
        "step_id": step_id,
        "status": "mock_executed" if ready else "mock_blocked",
        "result_ref": f"{step_id}.result",
        "schema": list(binding.get("result_schema") or []),
        "method_results": method_results,
        "errors": method_errors,
        "row_count": "unknown",
    }


def _mock_method_results(binding: Mapping[str, Any]) -> List[Dict[str, Any]]:
    request = binding.get("executable_request")
    if not isinstance(request, Mapping):
        return []
    rows: List[Dict[str, Any]] = []
    for api_call in request.get("api_calls") or []:
        if not isinstance(api_call, Mapping) or api_call.get("api_id") != "stock.quote":
            continue
        for method_call in api_call.get("method_calls") or []:
            if not isinstance(method_call, Mapping):
                continue
            errors = validate_stock_quote_method_call(method_call)
            result = mock_stock_quote_method_call(method_call)
            if errors and not result.get("errors"):
                result["errors"] = errors
            rows.append(result)
    return rows


def _validate_binding(binding: Dict[str, Any]) -> Dict[str, Any]:
    issues = list(binding.get("issues") or [])
    request = binding.get("executable_request")
    if isinstance(request, Mapping):
        for api_call in request.get("api_calls") or []:
            if not isinstance(api_call, Mapping) or api_call.get("api_id") != "stock.quote":
                continue
            for method_call in api_call.get("method_calls") or []:
                if isinstance(method_call, Mapping):
                    issues.extend(validate_stock_quote_method_call(method_call))
                else:
                    issues.append("stock.quote method_call must be an object")
    if issues:
        binding["status"] = "needs_repair"
        binding["issues"] = issues
    return binding


def _call_llm_json(prompt: str) -> Dict[str, Any]:
    from src.utils.ai_service import chat_qwen_flash_json

    payload, _usage = chat_qwen_flash_json([{"role": "user", "content": prompt}], enable_think=False)
    return payload if isinstance(payload, dict) else {}


def _merged_output_fields(api_refs: Iterable[Mapping[str, Any]]) -> List[str]:
    fields: List[str] = []
    seen: set[str] = set()
    for ref in api_refs:
        api_id = _text(ref.get("api_id"))
        for field in ref.get("output_fields") or []:
            value = f"{api_id}.{field}" if api_id else str(field)
            if value not in seen:
                seen.add(value)
                fields.append(value)
    return fields


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list_text(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item or "").strip() for item in value if str(item or "").strip()]
    if value is None:
        return []
    text = str(value).strip()
    return [text] if text else []
