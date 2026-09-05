from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from statistics import median
from typing import Any, Mapping


REPORT_APIS = {
    "stock.report",
    "stock.report.agg",
    "stock.report_metric",
    "stock.report_metric.agg",
}
METRIC_TERMS = (
    "EPS", "每股收益", "归母净利润", "净利润预测", "净利润增速", "营业收入预测",
    "营收预测", "营收增速", "毛利率", "净利率", "研发投入", "净资产收益率",
    "ROE", "每股股利", "股息率", "盈利预测", "业绩预测",
)
REPORT_TERMS = (
    "研报", "评级", "目标价", "投资观点", "主要理由", "主要风险", "竞争优势",
    "核心增长逻辑", "管理层", "护城河", "战略", "分歧点", "估值方法", "展望",
    "增长原因", "竞争格局", "布局", "投资逻辑",
)


def args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def text(value: Any) -> str:
    return str(value or "").strip()


def expected_family(question: str) -> str:
    metric = any(term.lower() in question.lower() for term in METRIC_TERMS)
    report = any(term in question for term in REPORT_TERMS)
    if metric and report:
        return "metric+report"
    if metric:
        return "metric"
    if report:
        return "report"
    return "report_or_mixed"


def expected_mode(question: str) -> str:
    aggregate_terms = (
        "平均", "最高", "最低", "中位数", "分布", "趋势", "一致预期", "一致评级",
        "不同机构", "多家", "机构对", "变化", "差异", "调整", "相比", "对比",
    )
    return "aggregate_or_multi" if any(term in question for term in aggregate_terms) else "direct"


def api_from_call(call: Mapping[str, Any]) -> str:
    request = text(call.get("request") or call.get("submitted_request"))
    match = re.search(r"=\s*([A-Za-z_][\w.]*)\s*\(", request)
    return match.group(1) if match else ""


def api_grade(family: str, apis: list[str]) -> str:
    report_apis = [api for api in apis if api in REPORT_APIS]
    if not report_apis:
        return "未选择研报API"
    has_metric = any(api.startswith("stock.report_metric") for api in report_apis)
    has_report = any(api.startswith("stock.report") and not api.startswith("stock.report_metric") for api in report_apis)
    if family == "metric":
        return "正确" if has_metric else "错误"
    if family == "report":
        return "正确" if has_report else "错误"
    if family == "metric+report":
        return "正确" if has_metric and has_report else "部分正确"
    return "正确" if report_apis else "未选择研报API"


def percentile(values: list[int], p: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * p)))
    return ordered[index]


def tool_spans(events: list[Mapping[str, Any]], total_ms: int) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    active: list[dict[str, Any]] = []
    for event in events:
        elapsed = int(event.get("elapsed_ms") or 0)
        if text(event.get("type")) == "tool_call":
            active.append({
                "tool": text(event.get("tool") or event.get("content")).rsplit("__", 1)[-1],
                "start_ms": elapsed,
                "end_ms": None,
            })
            continue
        progress_id = text(event.get("progress_id"))
        status = text(event.get("status"))
        if status not in {"completed", "error"}:
            continue
        if progress_id.startswith("finance_catalog"):
            wanted = "read_finance_catalog"
        elif progress_id.startswith("finance_query_step"):
            wanted = "finance_query"
        else:
            continue
        match = next((item for item in active if item["end_ms"] is None and item["tool"] == wanted), None)
        if match:
            match["end_ms"] = elapsed
            match["duration_ms"] = max(0, elapsed - int(match["start_ms"]))
            spans.append(match)
    for item in active:
        if item["end_ms"] is None:
            item["end_ms"] = total_ms
            item["duration_ms"] = max(0, total_ms - int(item["start_ms"]))
            spans.append(item)
    return spans


def main() -> int:
    ns = args()
    input_dir = Path(ns.input_dir)
    cases: list[dict[str, Any]] = []
    run_meta: list[dict[str, Any]] = []
    for path in sorted(input_dir.glob("batch_*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        run_meta.append({
            "file": str(path),
            "max_turns": payload.get("max_turns"),
            "effort": payload.get("effort"),
            "started_at": payload.get("started_at"),
            "finished_at": payload.get("finished_at"),
        })
        cases.extend(dict(item) for item in payload.get("cases") or [] if isinstance(item, Mapping))
    cases.sort(key=lambda item: text(item.get("id")))

    case_rows: list[dict[str, Any]] = []
    call_rows: list[dict[str, Any]] = []
    turn_rows: list[dict[str, Any]] = []
    for case in cases:
        case_id = text(case.get("id"))
        question = text(case.get("question"))
        calls = [dict(item) for item in case.get("tool_calls") or [] if isinstance(item, Mapping)]
        finance_calls = [item for item in calls if text(item.get("tool")) == "finance_query"]
        apis = [api_from_call(item) for item in finance_calls]
        apis = [api for api in apis if api]
        family = expected_family(question)
        mode = expected_mode(question)
        static_failures = [item for item in finance_calls if item.get("validation_errors")]
        provider_failures = [item for item in finance_calls if item.get("execution_error") or item.get("error")]
        static_ms = sum(float(item.get("static_validation_ms") or 0) for item in finance_calls)
        api_ms = sum(float(item.get("api_execution_ms") or 0) for item in finance_calls)
        total_ms = int(case.get("total_duration_ms") or 0)
        events = [dict(item) for item in case.get("events") or [] if isinstance(item, Mapping)]
        spans = tool_spans(events, total_ms)
        observed_tool_ms = sum(int(item.get("duration_ms") or 0) for item in spans)
        cold_start_ms = int(events[0].get("elapsed_ms") or 0) if events else 0
        cc_non_tool_ms = max(0, total_ms - cold_start_ms - observed_tool_ms)
        retried_after_static = bool(static_failures and len(finance_calls) > len(static_failures))
        corrected = bool(
            retried_after_static
            and any(not item.get("validation_errors") for item in finance_calls[finance_calls.index(static_failures[-1]) + 1 :])
        )
        first_request = text(finance_calls[0].get("submitted_request")) if finance_calls else ""
        final_request = text(finance_calls[-1].get("request") or finance_calls[-1].get("submitted_request")) if finance_calls else ""
        case_rows.append({
            "case_id": case_id,
            "question": question,
            "expected_family": family,
            "expected_mode": mode,
            "actual_apis": ", ".join(apis),
            "api_selection": api_grade(family, apis),
            "first_request": first_request,
            "final_request": final_request,
            "finance_query_count": len(finance_calls),
            "static_fail_count": len(static_failures),
            "static_retry": retried_after_static,
            "static_retry_corrected": corrected,
            "provider_fail_count": len(provider_failures),
            "empty_result_count": sum(1 for item in finance_calls if "row_count" in item and int(item.get("row_count") or 0) == 0),
            "assistant_message_count": int(case.get("assistant_message_count") or 0),
            "tool_result_message_count": int(case.get("tool_result_message_count") or 0),
            "tool_call_count": int(case.get("tool_call_count") or len(calls)),
            "cold_start_ms": cold_start_ms,
            "cc_non_tool_ms_est": cc_non_tool_ms,
            "static_validation_ms": round(static_ms, 3),
            "api_execution_ms": round(api_ms, 3),
            "observed_tool_ms": observed_tool_ms,
            "total_duration_ms": total_ms,
            "execution_completed": bool(case.get("execution_completed")),
            "error": text(case.get("error")),
            "answer": text(case.get("answer")),
        })
        for index, call in enumerate(finance_calls, start=1):
            call_rows.append({
                "case_id": case_id,
                "question": question,
                "attempt": index,
                "flow_step": int(call.get("flow_step") or 0),
                "goal": text(call.get("goal")),
                "api": api_from_call(call),
                "submitted_request": text(call.get("submitted_request")),
                "normalized_request": text(call.get("request")),
                "static_status": "失败" if call.get("validation_errors") else "通过",
                "static_errors": "; ".join(text(item) for item in call.get("validation_errors") or []),
                "static_validation_ms": float(call.get("static_validation_ms") or 0),
                "api_execution_ms": float(call.get("api_execution_ms") or 0),
                "provider_retry_count": int(call.get("provider_retry_count") or 0),
                "execution_error": text(call.get("execution_error") or call.get("error")),
                "row_count": call.get("row_count"),
                "result_name": text(call.get("result_name")),
            })
        previous_end = cold_start_ms
        for index, span in enumerate(sorted(spans, key=lambda item: int(item["start_ms"])), start=1):
            start_ms = int(span["start_ms"])
            end_ms = int(span["end_ms"])
            turn_rows.append({
                "case_id": case_id,
                "question": question,
                "segment": index,
                "tool": span["tool"],
                "cc_before_tool_ms_est": max(0, start_ms - previous_end),
                "tool_start_ms": start_ms,
                "tool_end_ms": end_ms,
                "tool_wall_ms": int(span["duration_ms"]),
            })
            previous_end = end_ms
        turn_rows.append({
            "case_id": case_id,
            "question": question,
            "segment": len(spans) + 1,
            "tool": "final_answer",
            "cc_before_tool_ms_est": max(0, total_ms - previous_end),
            "tool_start_ms": previous_end,
            "tool_end_ms": total_ms,
            "tool_wall_ms": 0,
        })

    totals = [int(item["total_duration_ms"]) for item in case_rows]
    summary = {
        "case_count": len(case_rows),
        "api_selection_counts": {status: sum(1 for item in case_rows if item["api_selection"] == status) for status in ("正确", "部分正确", "错误", "未选择研报API")},
        "static_fail_case_count": sum(1 for item in case_rows if item["static_fail_count"]),
        "static_retry_case_count": sum(1 for item in case_rows if item["static_retry"]),
        "static_retry_corrected_case_count": sum(1 for item in case_rows if item["static_retry_corrected"]),
        "provider_fail_case_count": sum(1 for item in case_rows if item["provider_fail_count"]),
        "empty_result_case_count": sum(1 for item in case_rows if item["empty_result_count"]),
        "execution_completed_count": sum(1 for item in case_rows if item["execution_completed"]),
        "total_duration_ms_sum": sum(totals),
        "total_duration_ms_median": int(median(totals)) if totals else 0,
        "total_duration_ms_p95": percentile(totals, 0.95),
        "cc_non_tool_ms_est_sum": sum(int(item["cc_non_tool_ms_est"]) for item in case_rows),
        "api_execution_ms_sum": round(sum(float(item["api_execution_ms"]) for item in case_rows), 3),
        "static_validation_ms_sum": round(sum(float(item["static_validation_ms"]) for item in case_rows), 3),
    }
    output = Path(ns.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"run_meta": run_meta, "summary": summary, "cases": case_rows, "calls": call_rows, "turns": turn_rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
