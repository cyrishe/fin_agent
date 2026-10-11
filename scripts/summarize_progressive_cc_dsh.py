#!/usr/bin/env python3
"""Summarize recorded native traces only; does not call models or data APIs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "outputs/progressive_cc_dsh_20260908"
CASES = ["BUS010", "CHAT_maotai_open_close", "BUS168", "BUS093", "BUS141", "RTEF012"]


def payloads(value):
    if isinstance(value, dict):
        if value.get("mode") == "dataview" or ("error" in value and "subject" in value):
            yield value
            return
        for item in value.values():
            yield from payloads(item)
    elif isinstance(value, list):
        for item in value:
            yield from payloads(item)
    elif isinstance(value, str) and value.strip().startswith("{"):
        try:
            yield from payloads(json.loads(value))
        except ValueError:
            pass


def main():
    report = {"purpose": "Runtime compatibility audit, not a speed or population accuracy benchmark", "cases": [], "totals": {}}
    evidence = {"catalog_returns": [], "system_inputs": {}, "user_and_stage_inputs": []}
    packs = {}
    for runtime in ["cc", "dsh"]:
        previous_api_ms = {}
        total = {}
        for case in CASES:
            directory = "cc_remaining" if runtime == "cc" and case == "RTEF012" else runtime
            path = BASE / directory / f"{case}.json"
            data = json.loads(path.read_text())
            record = data["record"]
            calls = record.get("tool_calls", [])
            natives = data["native_events"]
            row = {"runtime": runtime, "id": case, "question": data["question"], "trace": str(path.relative_to(ROOT)), "resumed": record.get("resumed"), "host_error": record.get("error"), "host_failure_kind": record.get("failure_kind", ""), "calls": calls}
            if runtime == "cc":
                result = next(x["message"] for x in natives if x.get("message", {}).get("_sdk_type") == "ResultMessage")
                usage = result["usage"]
                session = result["session_id"]
                model_ms = result["duration_api_ms"] - previous_api_ms.get(session, 0)
                previous_api_ms[session] = result["duration_api_ms"]
                turns, uncached, cached, output = result["num_turns"], usage["input_tokens"], usage.get("cache_read_input_tokens", 0) + usage.get("cache_creation_input_tokens", 0), usage["output_tokens"]
                row.update(native_finish=result["subtype"], native_final_text_present=bool(result.get("result")))
            else:
                usage = record["llm_usage"]
                model_ms = sum(x.get("duration_ms", 0) for x in record["execution_steps"] if x.get("kind") == "llm")
                turns, uncached, cached, output = usage["call_count"], usage["prompt_tokens"], usage["cache_read_tokens"], usage["completion_tokens"]
                row.update(native_finish=record.get("finish_reason"), native_final_text_present=bool(record.get("result")))
            metrics = {"model_turns": turns, "uncached_input_tokens": uncached, "cached_input_tokens": cached, "input_tokens": uncached + cached, "output_tokens": output, "total_tokens": uncached + cached + output, "model_seconds": model_ms / 1000, "data_api_seconds": round(sum(x.get("api_execution_ms", 0) for x in calls) / 1000, 6), "total_seconds": data["elapsed_ms"] / 1000, "static_failures": sum(bool(x.get("validation_errors")) for x in calls), "catalog_reads": sum(x["tool"] == "read_finance_catalog" for x in calls), "detail_loads": sum(x["tool"] == "load_finance_result" for x in calls)}
            row.update(metrics)
            report["cases"].append(row)
            for key, value in metrics.items():
                total[key] = round(total.get(key, 0) + value, 6)
            tool_arguments = {}
            for item in natives:
                if item["type"] == "host/query_start":
                    evidence["user_and_stage_inputs"].append({"runtime": runtime, "case": case, "prompt": item["prompt"]})
                elif item["type"] == "cc/actual_system_prompt":
                    evidence["system_inputs"]["cc_sdk_system_prompt"] = item["prompt"]
                message = item.get("message", {})
                event = item.get("event", {})
                data_event = event.get("data", {})
                if message.get("_sdk_type") == "AssistantMessage":
                    for block in message.get("content", []):
                        if block.get("name") == "mcp__finance__read_finance_catalog":
                            tool_arguments[block["id"]] = block.get("input", {})
                if event.get("type") == "tool/call" and data_event.get("name") == "mcp__finance__read_finance_catalog":
                    tool_arguments[data_event["callId"]] = json.loads(data_event["arguments"])
                if event.get("type") == "request/header":
                    evidence["system_inputs"]["dsh_request_header_system"] = data_event["header"]["system"]
                if event.get("type") == "user/message":
                    evidence["user_and_stage_inputs"].append({"runtime": runtime, "case": case, "message": data_event})
                candidate_blocks = message.get("content", []) if message.get("_sdk_type") == "UserMessage" else data_event.get("message", {}).get("content", []) if event.get("type") == "tool/result" else []
                for block in candidate_blocks:
                    call_id = block.get("tool_use_id") or block.get("toolCallId")
                    if call_id not in tool_arguments:
                        continue
                    for payload in payloads(block):
                        view = payload.get("dataview")
                        item_evidence = {"runtime": runtime, "case": case, "arguments": tool_arguments[call_id], "payload": payload}
                        if isinstance(view, dict):
                            digest = hashlib.sha256(json.dumps(view, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                            item_evidence.update(selected_operation=view.get("selected_operation", "full_view"), functions=[f["api_name"] for f in view.get("functions", [])], siblings=view.get("available_operations", {}), view_payload_sha256=digest)
                            if view.get("selected_operation"):
                                assert len(view["functions"]) == 1, item_evidence
                                assert all(isinstance(v, str) for v in view.get("available_operations", {}).values())
                            key = f"{payload['subject']}.{view['name']}:{view.get('selected_operation', 'full_view')}"
                            packs.setdefault(key, {}).setdefault(runtime, set()).add(digest)
                        evidence["catalog_returns"].append(item_evidence)
        report["totals"][runtime] = total
    report["shared_payload_hash_checks"] = {key: {"cc": sorted(value["cc"]), "dsh": sorted(value["dsh"]), "equal": value["cc"] == value["dsh"]} for key, value in packs.items() if set(value) == {"cc", "dsh"}}
    report["timing_note"] = "CC model_seconds is the delta of SDK cumulative duration_api_ms within each provider session. DSH model_seconds is step/start to assistant/message. Neither is pure reasoning time, and runtime strategies differ."
    report["system_capture_note"] = "CC application's actual SDK system argument captured during RTEF012; previous five share its fixed source construction but were not separately captured. DSH system is native request/header; all user and stage messages are native trace evidence."
    (BASE / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    (BASE / "catalog_and_input_evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    print(json.dumps({"totals": report["totals"], "shared_payload_hash_checks": report["shared_payload_hash_checks"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
