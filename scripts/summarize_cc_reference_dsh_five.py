"""Small evidence table for the fixed five-case comparison; no model calls."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs/cc_reference_vs_dsh_five_20260908"


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def native_cc_usage(response):
    sid = response["financial_qa"]["session_id"]
    paths = list((OUTPUT / "cc/workspace/data/financial_qa_cc_sessions").rglob(sid + ".jsonl"))
    if len(paths) != 1:
        raise ValueError("Need exactly one CC native session for usage reconciliation")
    messages = {}
    for event in read_rows(paths[0]):
        msg = event.get("message") or {}
        if event.get("type") == "assistant" and msg.get("usage"):
            messages[msg.get("id") or event["uuid"]] = msg["usage"]
    usage = {key: sum(int(value.get(key) or 0) for value in messages.values()) for key in (
        "input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")}
    reported = response["llm_usage"]
    assert usage["input_tokens"] == reported["prompt_tokens"]
    assert usage["output_tokens"] == reported["completion_tokens"]
    return {"llm_requests": len(messages), "tokens_with_cache": sum(usage.values()),
            "native_usage": usage, "native_source": str(paths[0].relative_to(ROOT)),
            "native_source_sha256": hashlib.sha256(paths[0].read_bytes()).hexdigest()}


def metrics(row, cc=False):
    response = row["response"]
    finance = response["financial_qa"]
    calls = finance["tool_calls"]
    usage = response["llm_usage"]
    values = native_cc_usage(response) if cc else {
        "llm_requests": len(finance["llm_step_usages"]),
        "tokens_with_cache": usage["total_tokens"] + usage.get("cache_read_tokens", 0),
    }
    return {**values, "seconds": row["wall_duration_ms"] / 1000,
            "tool_calls": len(calls), "static_failures": sum(bool(call.get("validation_errors")) for call in calls),
            "api_execution_seconds": sum(float(call.get("api_execution_ms") or 0) for call in calls) / 1000,
            "runtime_error": row.get("error") or finance.get("error") or "",
            "api_rows": [(value["api"], value["row_count"]) for value in response["data"]["results"]]}


def data(row, api):
    return [item for result in row["response"]["data"]["results"] if result["api"] == api for item in result["rows"]]


def canonical(rows, columns=None):
    return Counter(json.dumps({key: row.get(key) for key in columns} if columns else row,
                              sort_keys=True, ensure_ascii=False) for row in rows)


def main():
    cc = {row["case_id"]: row for row in read_rows(OUTPUT / "cc/results.jsonl")}
    dsh = {row["case_id"]: row for row in read_rows(OUTPUT / "dsh_configured/results.jsonl")}
    assert list(cc) == list(dsh) == ["BUS010", "BUS093", "BUS141", "BUS168", "RTEF012"]
    rows = []
    for key in cc:
        assert cc[key]["question"] == dsh[key]["question"]
        rows.append({"case_id": key, "question": cc[key]["question"],
                     "cc": metrics(cc[key], True), "dsh": metrics(dsh[key])})
    checks = {
        "quote_10_rows_identical": canonical(data(cc["BUS010"], "stock.quote")) == canonical(data(dsh["BUS010"], "stock.quote")),
        "index_600_rows_identical_ignoring_order": canonical(data(cc["BUS093"], "index.constitution")) == canonical(data(dsh["BUS093"], "index.constitution")),
        "fund_cc_10_subset_of_dsh_50": canonical(data(cc["BUS168"], "fund.basic_info")) <= canonical(data(dsh["BUS168"], "fund.basic_info")),
        "fund_all_names_match": all("债券" in row["name"] for row in data(dsh["BUS168"], "fund.basic_info")),
        "target_report_common_fields_identical": canonical(data(cc["RTEF012"], "stock.report"), ["code", "report_date", "institution", "target_price_upper"]) == canonical(data(dsh["RTEF012"], "stock.report"), ["code", "report_date", "institution", "target_price_upper"]),
        "window_both_empty": not data(cc["BUS141"], "plate.moneyflow.kd_main_net_sum") and not data(dsh["BUS141"], "plate.moneyflow.kd_main_net_sum"),
    }
    totals = {runtime: {key: sum(row[runtime][key] for row in rows) for key in (
        "seconds", "tokens_with_cache", "llm_requests", "tool_calls", "static_failures", "api_execution_seconds")}
        for runtime in ("cc", "dsh")}
    result = {"cases": rows, "data_checks": checks, "totals": totals,
              "wall_time_reduction": 1-totals["dsh"]["seconds"]/totals["cc"]["seconds"],
              "token_reduction_with_cache": 1-totals["dsh"]["tokens_with_cache"]/totals["cc"]["tokens_with_cache"],
              "notes": "Tokens include cache reads; not a billed-price calculation. CC API requests deduplicated by native assistant message id; DSH uses native llm_step_usages. Initial misconfigured DSH run is excluded, retained separately as environment diagnostics."}
    (OUTPUT / "comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
