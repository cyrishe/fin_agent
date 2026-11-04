from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
import uuid

from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.services.custom_tool_service import CustomToolAgentService, CustomToolRuntimeService, CustomToolStoreService


TEST_PATTERN = re.compile(r"py_compile|pytest|unittest|scratch/|assert ", re.IGNORECASE)
FAILURE_PATTERN = re.compile(
    r"command not found|Traceback \(most recent call last\)|\bFAILED\b|Process exited with code [1-9]",
    re.IGNORECASE,
)
INFRA_FAILURE_PATTERN = re.compile(r"CreateProcess|Failed to create unified exec process", re.IGNORECASE)
TEST_SETUP_FAILURE_PATTERN = re.compile(r"set a finance query handler|ModuleNotFoundError", re.IGNORECASE)


def _payload_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(
            str(item.get("text") or "") for item in value if isinstance(item, dict)
        )
    return json.dumps(value, ensure_ascii=False)


def _patch_targets(text: str) -> list[str]:
    targets: list[str] = []
    for chunk in text.replace("\\n", "\n").split("*** "):
        header = chunk.splitlines()[0] if chunk else ""
        match = re.match(r"(?:Add|Update|Delete) File:\s*(.+)$", header)
        if match:
            targets.append(match.group(1).strip())
    return targets


def _rollout_metrics(*, session_id: str, provider_session_id: str) -> dict[str, object]:
    session_key = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:24]
    session_home = REPO_ROOT / "data/agent_sessions/codex" / session_key
    rollouts = sorted(session_home.rglob(f"*{provider_session_id}.jsonl"))
    if not rollouts:
        return {}
    rows = [json.loads(line) for line in rollouts[-1].read_text(encoding="utf-8").splitlines() if line.strip()]
    calls: dict[str, str] = {}
    outputs: dict[str, str] = {}
    ordered_events: list[tuple[str, str]] = []
    for row in rows:
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        payload_type = payload.get("type")
        if payload_type in {"custom_tool_call", "function_call"}:
            call_id = str(payload.get("call_id") or payload.get("id") or "")
            calls[call_id] = str(
                payload.get("input") or payload.get("arguments") or ""
            )
            ordered_events.append(("call", call_id))
        elif payload_type in {"custom_tool_call_output", "function_call_output"}:
            call_id = str(payload.get("call_id") or "")
            outputs[call_id] = _payload_text(payload.get("output"))
            ordered_events.append(("output", call_id))
    test_call_ids = [call_id for call_id, text in calls.items() if TEST_PATTERN.search(text)]
    patch_calls = sum("apply_patch" in text for text in calls.values())
    patch_targets = {call_id: _patch_targets(text) for call_id, text in calls.items()}
    module_patch_calls = sum(
        any("implementation/modules/" in target for target in targets) for targets in patch_targets.values()
    )
    scratch_patch_calls = sum(
        any("scratch/" in target for target in targets) for targets in patch_targets.values()
    )
    failed_test_calls = sum(FAILURE_PATTERN.search(outputs.get(call_id, "")) is not None for call_id in test_call_ids)
    failure_seen = False
    repair_patch_calls = 0
    repair_module_patch_calls = 0
    for event_type, call_id in ordered_events:
        if event_type == "output" and call_id in test_call_ids and FAILURE_PATTERN.search(outputs.get(call_id, "")):
            failure_seen = True
        elif event_type == "call" and failure_seen and "apply_patch" in calls.get(call_id, ""):
            repair_patch_calls += 1
            if any("implementation/modules/" in target for target in patch_targets.get(call_id, [])):
                repair_module_patch_calls += 1
    return {
        "rollout_path": str(rollouts[-1].relative_to(REPO_ROOT)),
        "tool_calls": len(calls),
        "api_reference_calls": sum("task_context" in text or "api_catalog" in text for text in calls.values()),
        "patch_calls": patch_calls,
        "module_patch_calls": module_patch_calls,
        "scratch_patch_calls": scratch_patch_calls,
        "repair_patch_calls": repair_patch_calls,
        "repair_module_patch_calls": repair_module_patch_calls,
        "test_calls": len(test_call_ids),
        "failed_test_calls": failed_test_calls,
        "infrastructure_failure_calls": sum(
            INFRA_FAILURE_PATTERN.search(text) is not None for text in outputs.values()
        ),
        "test_setup_failure_calls": sum(
            TEST_SETUP_FAILURE_PATTERN.search(text) is not None for text in outputs.values()
        ),
        "first_pass": failed_test_calls == 0 and repair_module_patch_calls == 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("design_path")
    parser.add_argument("--label", default="")
    parser.add_argument("--output-dir", default="outputs/codex_coding_efficiency")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env", override=False)
    source_path = Path(args.design_path).resolve()
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    design = dict(payload.get("design") or payload)
    understanding = dict(payload.get("understanding") or {})
    label = args.label or str(design.get("tool_name") or source_path.stem)
    owner_id = f"codex-efficiency-{uuid.uuid4().hex[:12]}"

    with tempfile.TemporaryDirectory(prefix="codex_efficiency_store_") as tmp:
        store = CustomToolStoreService(root_dir=tmp)
        agent = CustomToolAgentService(store=store, runtime=CustomToolRuntimeService(store=store))
        result = agent.implement_dynamic_tool(
            state={
                "owner_id": owner_id,
                "requirement_text": str(understanding.get("goal") or design.get("description") or ""),
                "understanding": understanding,
                "design_contract": design,
                "tool_name": str(design.get("tool_name") or ""),
            },
            owner_id=owner_id,
            instruction="根据已确认设计实现动态金融工具，并完成必要的聚焦技术测试。",
        )

    meta = dict(result.get("implementation_meta") or {})
    test_result = dict(result.get("test_result") or {})
    session_id = str(meta.get("session_id") or "")
    provider_session_id = str(meta.get("provider_session_id") or "")
    evidence = {
        "label": label,
        "design_path": str(source_path),
        "tool_name": design.get("tool_name"),
        "data_sources": [
            item.get("source_ref")
            for item in design.get("data_requirements") or []
            if isinstance(item, dict) and item.get("source_ref")
        ],
        "module_count": len(design.get("modules") or []),
        "output_count": len(design.get("outputs") or []),
        "execution_ok": test_result.get("execution_ok") is True,
        "contract_ok": test_result.get("contract_ok") is True,
        "error": test_result.get("error"),
        "message": result.get("message"),
        "duration_ms": meta.get("duration_ms"),
        "provider": meta.get("provider"),
        "complexity": meta.get("complexity"),
        "model": meta.get("model"),
        "reasoning_effort": meta.get("reasoning_effort"),
        "session_id": session_id,
        "provider_session_id": provider_session_id,
        **(_rollout_metrics(session_id=session_id, provider_session_id=provider_session_id) if session_id and provider_session_id else {}),
    }
    output_dir = (REPO_ROOT / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{label}.json"
    output_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0 if evidence["execution_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
