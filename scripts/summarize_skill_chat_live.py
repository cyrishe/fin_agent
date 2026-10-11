"""Summarize completed live Chat cases; no model or data-provider requests."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.eval_skill_chat_live import save
from src.experiments.skill_handoff import coverage, freeze_results
from src.services.session_variable_store_service import SessionVariableStoreService


def summarize(out):
    rows = []
    for case in json.loads((out / "manifest.json").read_text())["cases"]:
        folder = out / case["id"]
        if not (folder / "completion.json").exists():
            continue
        completion = json.loads((folder / "completion.json").read_text())
        events = json.loads((folder / "dsh_events.json").read_text())
        if not events:
            rows.append({"case": case["id"], **completion})
            continue
        event = events[-1]
        requests = {r["request_index"]: r for r in event["loop_policy"]["requests"]}
        llm_steps, tool_spans = [], []
        by_stage = defaultdict(lambda: {"calls": 0, "seconds": 0, "input_uncached": 0,
            "input_cached": 0, "output": 0, "reasoning": 0})
        for span in event["execution_steps"]:
            if span["kind"] == "llm":
                req = requests.get(span["request_index"], {})
                usage = span.get("usage") or {}
                row = {**req, "seconds": (span.get("duration_ms") or 0) / 1000,
                       "input_uncached": usage.get("input_tokens", 0),
                       "input_cached": usage.get("cache_read_tokens", 0)}
                llm_steps.append(row)
                acc = by_stage[req.get("stage") or "unattributed"]
                acc["calls"] += 1
                acc["seconds"] += row["seconds"]
                for key, source in [("input_uncached", "input_tokens"), ("input_cached", "cache_read_tokens"),
                                    ("output", "output_tokens"), ("reasoning", "reasoning_tokens")]:
                    acc[key] += usage.get(source, 0)
            else:
                tool_spans.append(span)
        tool_counts = Counter(s["tool"] for s in tool_spans)
        tool_seconds = defaultdict(float)
        for span in tool_spans:
            tool_seconds[span["tool"]] += (span.get("duration_ms") or 0) / 1000
        refs = event.get("result_refs") or []
        query_calls = [c for c in event.get("tool_calls", []) if c.get("tool") == "finance_query"]
        query_failures = [{"goal": c.get("goal"), "request": c.get("request"),
                           "failure": "execution_error"} for c in query_calls if c.get("execution_error")]
        # Scope comes from the actual host-returned result ref, never arbitrary input.
        frozen = []
        for ref in refs:
            sid = ref["result_ref"].split("session://", 1)[1].split("/", 1)[0]
            frozen.extend(freeze_results(SessionVariableStoreService(), session_id=sid, result_refs=[ref]))
        save(folder / "frozen_evidence.json", frozen)
        datasets = [{"ref": f["result_ref"], "api": f["api"], "rows": f["row_count"],
                     "dates": coverage(f["rows"]), "warnings": f["warnings"],
                     "timings": f["registered"].get("timings"),
                     "provider_evidence": f["provider_evidence"]} for f in frozen]
        dispatch = json.loads((folder / "dispatch.json").read_text())
        response = json.loads((folder / "response.json").read_text())
        details = {"llm_steps": llm_steps, "stage_totals": dict(by_stage),
            "tool_spans": tool_spans, "tool_counts": dict(tool_counts),
            "tool_seconds_sum_not_wall_time": dict(tool_seconds), "datasets": datasets,
            "dispatch_usage": dispatch.get("llm_usage"),
            "follow_up_usage": response.get("follow_up_usage"),
            "api_total_usage": response.get("llm_usage"),
            "query_failures": query_failures,
            "timing_caveat": "LLM spans include request preparation. Parallel tool durations are summed, not wall time. Shared model work is not attributable to individual skills."}
        save(folder / "chain_analysis.json", details)
        rows.append({"case": case["id"], **completion, "dsh_seconds": event["duration_ms"] / 1000,
            "skills": [s["skill_id"] for s in event.get("skill_entries", [])],
            "llm_usage": event["llm_usage"], "dispatch_usage": dispatch.get("llm_usage"),
            "follow_up_usage": response.get("follow_up_usage"), "api_total_usage": response.get("llm_usage"),
            "model_seconds": sum(r["seconds"] for r in llm_steps),
            "tool_counts": dict(tool_counts), "query_steps": len(refs),
            "query_steps_attempted": len(query_calls), "query_steps_failed": len(query_failures),
            "provider_seconds_including_failures": sum(float(c.get("api_execution_ms") or 0) for c in query_calls) / 1000,
            "max_token_hits": sum(bool(r.get("max_token_hit")) for r in llm_steps),
            "queue_wait_ms": event.get("queue_wait_ms"), "client_reused": event.get("client_reused"),
            "tool_errors": sum(bool(s.get("is_error")) for s in tool_spans)})
    save(out / "summary.json", rows)
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    for row in summarize(args.output.resolve()):
        print(row["case"], round(row["seconds"], 2), row.get("finish_reason"), row.get("skills", []))
