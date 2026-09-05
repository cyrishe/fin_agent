#!/usr/bin/env python3
"""Run the financial-query benchmark through the real streaming Chat API.

The evaluator deliberately observes only the public HTTP/SSE contract.  Timing
milestones inferred from progress blocks are estimates; they are not presented
as server-side spans.  Every case gets a fresh requests.Session and omits a
thread id so conversations (and guest cookies) cannot leak across cases.

The command writes three incremental artifacts:

* ``--output``: an atomically replaced JSON snapshot after every completed case;
* ``<output>.jsonl``: one append-only case result per line;
* ``<output>.events.jsonl``: one append-only SSE observation per line.

This makes long runs resumable while retaining the event timestamps needed for
later latency and harness-efficiency analysis.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

import requests


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = (
    ROOT
    / "outputs"
    / "d4f10504-8df6-435e-9316-3d89b5fd1015"
    / "source_cases.json"
)
DEFAULT_OUTPUT = (
    ROOT
    / "outputs"
    / "d4f10504-8df6-435e-9316-3d89b5fd1015"
    / "finance_query_api_batch_results.json"
)
DEFAULT_BASE_URL = "http://127.0.0.1:22054"
GUEST_COOKIE_NAMES = ("aiia_guest_user_id", "aiia_guest_session_token")
THREAD_COOKIE_NAME = "aiia_assistant_thread_id"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _round_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 2)


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_load_response(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        body = response.text[:2000]
        raise RuntimeError(
            f"HTTP {response.status_code} returned invalid JSON: {body}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"HTTP {response.status_code} returned a non-object JSON value"
        )
    return payload


def _sse_data_payloads(lines: Iterable[str | bytes]) -> Iterator[str]:
    """Yield complete SSE data fields, including legal multi-line events."""

    data_lines: list[str] = []
    for raw_line in lines:
        line = (
            raw_line.decode("utf-8", errors="replace")
            if isinstance(raw_line, bytes)
            else str(raw_line)
        )
        line = line.rstrip("\r")
        if not line:
            if data_lines:
                yield "\n".join(data_lines)
                data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if field != "data":
            continue
        if separator and value.startswith(" "):
            value = value[1:]
        data_lines.append(value)
    if data_lines:
        yield "\n".join(data_lines)


def _event_view(event: Mapping[str, Any]) -> Mapping[str, Any]:
    """Unwrap the occasional ``event=block`` envelope used by asset runs."""

    nested = event.get("block")
    if isinstance(nested, Mapping):
        return nested
    return event


def _event_feedback_text(event: Mapping[str, Any]) -> str:
    view = _event_view(event)
    data = _mapping(view.get("data"))
    for value in (
        view.get("content"),
        view.get("message"),
        data.get("summary"),
        data.get("content"),
        view.get("title"),
    ):
        text = _text(value)
        if text:
            return text
    return ""


def _progress_milestone(
    event: Mapping[str, Any], elapsed_ms: float
) -> dict[str, Any] | None:
    """Extract an estimated stage milestone from a public progress block."""

    view = _event_view(event)
    data = _mapping(view.get("data"))
    role = _text(data.get("role"))
    block_type = _text(view.get("block_type") or view.get("type"))
    stage = _text(view.get("stage") or data.get("stage"))
    status = _text(data.get("status") or view.get("status"))
    is_progress = role in {"process", "live_progress", "conversation_progress"}
    is_progress = is_progress or block_type == "status"
    if not is_progress or not stage:
        return None
    return {
        "elapsed_ms": elapsed_ms,
        "stage": stage,
        "status": status or "running",
        "block_id": _text(view.get("block_id")),
        "title": _text(view.get("title") or data.get("title")),
        "summary": _event_feedback_text(view),
        "current_step": _text(data.get("current_step")),
        "timing_kind": "client_estimate",
        "basis": "public_sse_progress_block",
    }


def _stage_timing_estimates(
    milestones: list[dict[str, Any]], total_elapsed_ms: float
) -> list[dict[str, Any]]:
    """Estimate business-step intervals from public progress observations.

    Public financial progress currently uses the broad server stage ``runtime``
    for understanding, catalog lookup, each query, and synthesis.  Therefore
    grouping only by ``stage`` would collapse the exact phases the evaluator
    needs.  The stable public ``block_id`` is used as the business-step key.

    A step's end is its first completed/error observation.  If the stream never
    marks completion, the next step's first observation is used; the request
    end is the final fallback.  A completed-only block has no observable start,
    so the previous step's end is used and labelled as an estimate.
    """

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    step_order: list[str] = []
    for item in milestones:
        step_key = (
            _text(item.get("block_id"))
            or ":".join(
                value
                for value in (
                    _text(item.get("stage")),
                    _text(item.get("title")),
                    _text(item.get("current_step")),
                )
                if value
            )
        )
        if not step_key:
            continue
        if step_key not in grouped:
            step_order.append(step_key)
        grouped[step_key].append(item)

    results: list[dict[str, Any]] = []
    previous_end_ms = 0.0
    for index, step_key in enumerate(step_order):
        items = grouped[step_key]
        first_elapsed_ms = float(items[0].get("elapsed_ms") or 0.0)
        first_status = _text(items[0].get("status"))
        if first_status == "running":
            start_ms = first_elapsed_ms
            start_basis = "first_public_running_progress"
        else:
            start_ms = previous_end_ms if index > 0 else first_elapsed_ms
            start_basis = (
                "previous_public_step_end_estimate"
                if index > 0
                else "completed_only_public_observation"
            )
        terminal = next(
            (
                item
                for item in items
                if _text(item.get("status")) in {"completed", "error"}
            ),
            None,
        )
        if terminal is not None:
            end_ms = float(terminal.get("elapsed_ms") or start_ms)
            end_basis = "public_terminal_progress"
        elif index + 1 < len(step_order):
            end_ms = float(
                grouped[step_order[index + 1]][0].get("elapsed_ms") or start_ms
            )
            end_basis = "next_public_stage_start"
        else:
            end_ms = float(total_elapsed_ms)
            end_basis = "request_end_fallback"
        end_ms = max(start_ms, end_ms)
        previous_end_ms = end_ms
        results.append(
            {
                "stage": _text(items[0].get("stage")),
                "step_key": step_key,
                "block_id": _text(items[0].get("block_id")),
                "title": _text(items[0].get("title")),
                "start_ms": round(start_ms, 2),
                "end_ms": round(end_ms, 2),
                "duration_ms": round(end_ms - start_ms, 2),
                "event_count": len(items),
                "timing_kind": "client_estimate",
                "start_basis": start_basis,
                "end_basis": end_basis,
            }
        )
    return results


def _error_category(exc: BaseException, *, status_code: int | None = None) -> str:
    if status_code == 429:
        return "http_429"
    if "429" in str(exc) or "insufficient_quota" in str(exc).lower():
        return "provider_or_stream_429"
    if isinstance(exc, (requests.Timeout, TimeoutError)):
        return "timeout"
    if isinstance(exc, requests.ConnectionError):
        return "connection_error"
    if isinstance(exc, requests.HTTPError):
        return f"http_{status_code or 'error'}"
    if isinstance(exc, json.JSONDecodeError):
        return "invalid_sse_json"
    return "runtime_error"


class IncrementalWriter:
    def __init__(
        self,
        *,
        output_path: Path,
        metadata: Mapping[str, Any],
        case_order: list[str],
        initial_cases: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        self.output_path = output_path
        self.case_jsonl_path = output_path.with_suffix(".jsonl")
        self.event_jsonl_path = output_path.with_name(
            f"{output_path.stem}.events.jsonl"
        )
        self.metadata = dict(metadata)
        self.case_order = list(case_order)
        self.results: dict[str, dict[str, Any]] = {
            str(key): dict(value) for key, value in (initial_cases or {}).items()
        }
        self.lock = threading.Lock()
        for path in (self.output_path, self.case_jsonl_path, self.event_jsonl_path):
            path.parent.mkdir(parents=True, exist_ok=True)

    def append_event(self, record: Mapping[str, Any]) -> None:
        with self.lock:
            with self.event_jsonl_path.open("a", encoding="utf-8") as handle:
                handle.write(_json_dump(record) + "\n")
                handle.flush()

    def complete_case(self, result: Mapping[str, Any]) -> None:
        case_id = _text(result.get("case_id"))
        with self.lock:
            self.results[case_id] = dict(result)
            with self.case_jsonl_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    _json_dump(
                        {
                            "record_type": "case_result",
                            "written_at": _now_iso(),
                            **dict(result),
                        }
                    )
                    + "\n"
                )
                handle.flush()
            self._write_snapshot_locked()

    def _ordered_results(self) -> list[dict[str, Any]]:
        seen: set[str] = set()
        ordered: list[dict[str, Any]] = []
        for case_id in self.case_order:
            if case_id in self.results:
                ordered.append(self.results[case_id])
                seen.add(case_id)
        ordered.extend(
            value for key, value in self.results.items() if key not in seen
        )
        return ordered

    def _write_snapshot_locked(self) -> None:
        cases = self._ordered_results()
        ok_count = sum(1 for item in cases if item.get("status") == "ok")
        snapshot = {
            **self.metadata,
            "updated_at": _now_iso(),
            "partial": len(cases) < len(self.case_order),
            "summary": {
                "selected_cases": len(self.case_order),
                "completed_cases": len(cases),
                "ok_cases": ok_count,
                "error_cases": len(cases) - ok_count,
            },
            "artifacts": {
                "snapshot_json": str(self.output_path),
                "case_jsonl": str(self.case_jsonl_path),
                "event_jsonl": str(self.event_jsonl_path),
            },
            "cases": cases,
        }
        temporary = self.output_path.with_name(
            f".{self.output_path.name}.{uuid.uuid4().hex}.tmp"
        )
        temporary.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, self.output_path)

    def finish(self) -> None:
        with self.lock:
            self._write_snapshot_locked()


def _load_existing_results(output_path: Path) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    if output_path.exists():
        try:
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            for item in payload.get("cases") or []:
                if isinstance(item, Mapping) and _text(item.get("case_id")):
                    results[_text(item.get("case_id"))] = dict(item)
        except (OSError, ValueError, TypeError):
            pass
    case_jsonl_path = output_path.with_suffix(".jsonl")
    if case_jsonl_path.exists():
        try:
            for line in case_jsonl_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                item = json.loads(line)
                if isinstance(item, Mapping) and _text(item.get("case_id")):
                    results[_text(item.get("case_id"))] = dict(item)
        except (OSError, ValueError, TypeError):
            pass
    return results


def _bootstrap_identity_cookies(
    base_url: str, *, timeout_seconds: float
) -> dict[str, str]:
    """Create/resolve one guest identity for the whole benchmark run.

    Only the two guest identity cookies are copied into worker-local sessions.
    The assistant thread cookie is intentionally excluded so reuse of an
    identity never implies reuse of conversation context.
    """

    session = requests.Session()
    session.trust_env = False
    try:
        response = session.get(
            f"{base_url.rstrip('/')}/api/assistant/threads",
            timeout=(10.0, min(max(timeout_seconds, 10.0), 60.0)),
        )
        response.raise_for_status()
        payload = _json_load_response(response)
        if payload.get("ok") is False:
            raise RuntimeError(
                _text(payload.get("error")) or "guest identity bootstrap failed"
            )
        cookies = {
            name: _text(session.cookies.get(name)) for name in GUEST_COOKIE_NAMES
        }
        if not all(cookies.values()):
            raise RuntimeError(
                "guest identity bootstrap did not return both identity cookies"
            )
        return cookies
    finally:
        session.close()


def _run_case(
    *,
    case: Mapping[str, Any],
    ordinal: int,
    selected_count: int,
    base_url: str,
    timeout_seconds: float,
    financial_qa_runtime: str,
    identity_cookies: Mapping[str, str],
    writer: IncrementalWriter,
) -> dict[str, Any]:
    case_id = _text(case.get("case_id")) or f"case_{ordinal:04d}"
    question = _text(case.get("question") or case.get("query") or case.get("text"))
    started_perf = time.perf_counter()
    started_at = _now_iso()
    session = requests.Session()
    session.trust_env = False
    for cookie_name in GUEST_COOKIE_NAMES:
        session.cookies.set(
            cookie_name,
            _text(identity_cookies.get(cookie_name)),
            path="/",
        )
    sse_events: list[dict[str, Any]] = []
    milestones: list[dict[str, Any]] = []
    done_event: dict[str, Any] = {}
    done_result: dict[str, Any] = {}
    first_event: dict[str, Any] = {}
    first_feedback: dict[str, Any] = {}
    start_payload: dict[str, Any] = {}
    start_response_elapsed_ms = 0.0
    stream_url = ""
    thread_id: Any = None
    http_status: int | None = None
    error: dict[str, Any] = {}

    try:
        # Reset is cheap and makes the isolation boundary explicit even if a
        # future server version starts returning a thread cookie on SSE runs.
        session.cookies.pop(THREAD_COOKIE_NAME, None)
        reset_response = session.post(
            f"{base_url.rstrip('/')}/api/assistant/thread/reset",
            timeout=(10.0, min(max(timeout_seconds, 10.0), 60.0)),
        )
        http_status = reset_response.status_code
        if reset_response.status_code == 429:
            raise requests.HTTPError(
                f"HTTP 429: {reset_response.text[:2000]}", response=reset_response
            )
        reset_response.raise_for_status()
        session.cookies.pop(THREAD_COOKIE_NAME, None)
        start_response = session.post(
            f"{base_url.rstrip('/')}/api/chat/stream/start",
            json={
                "text": question,
                "application_name": "investment_workbench",
                "attachment_ids": [],
                "financial_qa_runtime": financial_qa_runtime,
            },
            timeout=(10.0, min(max(timeout_seconds, 10.0), 60.0)),
        )
        http_status = start_response.status_code
        if start_response.status_code == 429:
            raise requests.HTTPError(
                f"HTTP 429: {start_response.text[:2000]}", response=start_response
            )
        start_response.raise_for_status()
        start_payload = _json_load_response(start_response)
        start_response_elapsed_ms = _round_ms(started_perf)
        if start_payload.get("ok") is False:
            raise RuntimeError(
                _text(start_payload.get("error")) or "stream start returned ok=false"
            )
        stream_url = _text(start_payload.get("stream_url"))
        if not stream_url:
            raise RuntimeError(f"stream start returned no stream_url: {start_payload}")
        if stream_url.startswith("/"):
            stream_url = f"{base_url.rstrip('/')}{stream_url}"

        # ``timeout`` is both the HTTP read timeout and the reported test
        # budget.  Werkzeug sends progress frequently enough that a stalled run
        # is surfaced as a requests timeout instead of hanging the full batch.
        with session.get(
            stream_url,
            stream=True,
            headers={"Accept": "text/event-stream"},
            timeout=(10.0, timeout_seconds),
        ) as stream_response:
            http_status = stream_response.status_code
            if stream_response.status_code == 429:
                raise requests.HTTPError(
                    f"HTTP 429: {stream_response.text[:2000]}",
                    response=stream_response,
                )
            stream_response.raise_for_status()
            for sequence, raw_data in enumerate(
                _sse_data_payloads(
                    stream_response.iter_lines(decode_unicode=True, chunk_size=1)
                ),
                start=1,
            ):
                elapsed_ms = _round_ms(started_perf)
                if elapsed_ms > timeout_seconds * 1000.0:
                    raise TimeoutError(
                        f"case exceeded the {timeout_seconds:g}s client timeout"
                    )
                try:
                    parsed = json.loads(raw_data)
                except json.JSONDecodeError as exc:
                    raise json.JSONDecodeError(
                        f"invalid SSE JSON at event {sequence}: {exc.msg}",
                        raw_data,
                        exc.pos,
                    ) from exc
                if not isinstance(parsed, dict):
                    parsed = {"event": "unknown", "value": parsed}
                observation = {
                    "sequence": sequence,
                    "received_at": _now_iso(),
                    "client_elapsed_ms": elapsed_ms,
                    "payload": parsed,
                }
                sse_events.append(observation)
                writer.append_event(
                    {
                        "record_type": "sse_event",
                        "case_id": case_id,
                        "ordinal": ordinal,
                        **observation,
                    }
                )
                if not first_event:
                    first_event = {
                        "client_elapsed_ms": elapsed_ms,
                        "event": _text(parsed.get("event")),
                        "text": _event_feedback_text(parsed),
                    }
                feedback_text = _event_feedback_text(parsed)
                if not first_feedback and feedback_text:
                    first_feedback = {
                        "client_elapsed_ms": elapsed_ms,
                        "event": _text(parsed.get("event")),
                        "text": feedback_text,
                    }
                milestone = _progress_milestone(parsed, elapsed_ms)
                if milestone is not None:
                    milestones.append(milestone)
                event_name = _text(parsed.get("event"))
                if event_name == "done":
                    done_event = parsed
                    done_result = _mapping(parsed.get("result"))
                    thread_id = parsed.get("thread_id") or done_result.get("thread_id")
                elif event_name == "error":
                    raise RuntimeError(
                        _text(parsed.get("message") or parsed.get("error"))
                        or "stream returned an error event"
                    )
        if not done_result:
            raise RuntimeError(
                f"stream ended without done.result after {len(sse_events)} events"
            )
    except Exception as exc:  # A failed case is data; it must not abort the batch.
        response = getattr(exc, "response", None)
        if isinstance(response, requests.Response):
            http_status = response.status_code
        error = {
            "category": _error_category(exc, status_code=http_status),
            "type": type(exc).__name__,
            "message": str(exc),
            "http_status": http_status,
        }
    finally:
        session.close()

    total_elapsed_ms = _round_ms(started_perf)
    financial_qa = _mapping(done_result.get("financial_qa"))
    dispatch_plan = _mapping(done_result.get("dispatch_plan"))
    llm_usage = _mapping(done_result.get("llm_usage"))
    financial_qa_error = _text(financial_qa.get("error"))
    if not error and financial_qa_error:
        error = {
            "category": "runtime_error",
            "type": "FinancialQaRuntimeError",
            "message": financial_qa_error,
            "http_status": http_status,
        }
    result: dict[str, Any] = {
        "case_id": case_id,
        "ordinal": ordinal,
        "selected_count": selected_count,
        "question": question,
        "source_case": dict(case),
        "status": "error" if error else "ok",
        "started_at": started_at,
        "completed_at": _now_iso(),
        "total_elapsed_ms": total_elapsed_ms,
        "first_event": first_event,
        "first_feedback": first_feedback,
        "first_progress_feedback": (
            {
                "client_elapsed_ms": milestones[0]["elapsed_ms"],
                "title": milestones[0]["title"],
                "text": milestones[0]["summary"],
            }
            if milestones
            else {}
        ),
        "stream_start": {
            "client_elapsed_ms": start_response_elapsed_ms,
            "payload": start_payload,
            "stream_url": stream_url,
        },
        "thread_id": thread_id,
        "sse_event_count": len(sse_events),
        "sse_events": sse_events,
        "progress_milestones": milestones,
        "stage_timing_estimates": _stage_timing_estimates(
            milestones, total_elapsed_ms
        ),
        "timing_coverage": {
            "first_public_progress_ms": (
                milestones[0]["elapsed_ms"] if milestones else None
            ),
            "last_public_progress_ms": (
                milestones[-1]["elapsed_ms"] if milestones else None
            ),
            "unattributed_before_first_progress_ms": (
                milestones[0]["elapsed_ms"] if milestones else total_elapsed_ms
            ),
            "unattributed_after_last_progress_ms": (
                round(total_elapsed_ms - milestones[-1]["elapsed_ms"], 2)
                if milestones
                else 0.0
            ),
        },
        "timing_note": (
            "total_elapsed_ms and SSE receive times are client measurements; "
            "stage_timing_estimates are approximate intervals inferred from "
            "public progress blocks, not exact server spans."
        ),
        "done_event": done_event,
        "done_result": done_result,
        "financial_qa": financial_qa,
        "dispatch_plan": dispatch_plan,
        "llm_usage": llm_usage,
        "turn_meta": _mapping(done_result.get("turn_meta")),
        "diagnostic_trace": _mapping(done_result.get("diagnostic_trace")),
        "error": error,
    }
    writer.complete_case(result)
    mark = "OK" if result["status"] == "ok" else "ERR"
    print(
        f"[{mark}] {ordinal:03d}/{selected_count:03d} {case_id} "
        f"{total_elapsed_ms / 1000.0:.2f}s events={len(sse_events)}"
        + (f" error={error.get('category')}" if error else ""),
        flush=True,
    )
    return result


def _parse_case_filters(values: list[str]) -> set[str]:
    return {
        item.strip()
        for value in values
        for item in str(value).split(",")
        if item.strip()
    }


def _load_cases(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        cases = payload
        metadata: dict[str, Any] = {}
    elif isinstance(payload, dict):
        cases = payload.get("cases")
        metadata = {key: value for key, value in payload.items() if key != "cases"}
    else:
        raise ValueError("cases file must contain a JSON object or array")
    if not isinstance(cases, list):
        raise ValueError("cases file does not contain a cases array")
    normalized = [dict(item) for item in cases if isinstance(item, Mapping)]
    return metadata, normalized


def run(args: argparse.Namespace) -> int:
    cases_path = Path(args.cases).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    source_metadata, cases = _load_cases(cases_path)
    filters = _parse_case_filters(args.case)
    if filters:
        cases = [item for item in cases if _text(item.get("case_id")) in filters]
        found = {_text(item.get("case_id")) for item in cases}
        missing = sorted(filters - found)
        if missing:
            print(f"warning: unknown case id(s): {', '.join(missing)}", flush=True)
    cases = cases[max(args.offset, 0) :]
    if args.limit is not None:
        cases = cases[: max(args.limit, 0)]
    if not cases:
        print("No cases selected.", flush=True)
        return 2

    existing = _load_existing_results(output_path) if args.resume else {}
    selected_ids = [
        _text(item.get("case_id")) or f"case_{index:04d}"
        for index, item in enumerate(cases, start=1)
    ]
    pending: list[tuple[int, dict[str, Any]]] = []
    for ordinal, (case_id, case) in enumerate(zip(selected_ids, cases), start=1):
        if args.resume and case_id in existing:
            continue
        pending.append((ordinal, case))

    metadata = {
        "eval_name": "finance_query_api_batch_v1",
        "generated_at": _now_iso(),
        "base_url": args.base_url.rstrip("/"),
        "source_path": str(cases_path),
        "source_metadata": source_metadata,
        "selection": {
            "case_filters": sorted(filters),
            "offset": args.offset,
            "limit": args.limit,
            "concurrency": args.concurrency,
            "timeout_seconds": args.timeout,
            "financial_qa_runtime": args.financial_qa_runtime,
            "resume": bool(args.resume),
        },
        "isolation": (
            "The run bootstraps one guest identity, then each case uses a fresh "
            "HTTP session carrying only those identity cookies. The thread "
            "cookie is reset/removed and no thread_id is supplied, so the "
            "server creates a distinct conversation without creating 228 "
            "guest accounts."
        ),
    }
    writer = IncrementalWriter(
        output_path=output_path,
        metadata=metadata,
        case_order=selected_ids,
        initial_cases={key: value for key, value in existing.items() if key in selected_ids},
    )
    writer.finish()
    print(
        f"Selected {len(cases)} case(s); pending={len(pending)}; "
        f"concurrency={args.concurrency}; output={output_path}",
        flush=True,
    )
    if not pending:
        print("Resume found all selected cases already completed.", flush=True)
        return 0

    identity_cookies = _bootstrap_identity_cookies(
        args.base_url, timeout_seconds=args.timeout
    )

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, args.concurrency), thread_name_prefix="finance-eval"
    ) as pool:
        futures = [
            pool.submit(
                _run_case,
                case=case,
                ordinal=ordinal,
                selected_count=len(cases),
                base_url=args.base_url,
                timeout_seconds=args.timeout,
                financial_qa_runtime=args.financial_qa_runtime,
                identity_cookies=identity_cookies,
                writer=writer,
            )
            for ordinal, case in pending
        ]
        # _run_case converts operational failures into result rows.  This loop
        # only guards against evaluator bugs so one Future cannot hide others.
        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except Exception as exc:  # pragma: no cover - last-resort containment
                print(
                    f"[EVALUATOR_ERROR] {type(exc).__name__}: {exc}", flush=True
                )
    writer.finish()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run financial benchmark queries through /api/chat/stream/start "
            "and retain client-timed SSE traces."
        )
    )
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--case",
        action="append",
        default=[],
        help="Case id; repeat or comma-separate to select multiple cases.",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument(
        "--financial-qa-runtime",
        choices=("cc", "dsh"),
        default="cc",
        help="Financial-QA execution path requested from the Chat API.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip case ids already present in the output snapshot/JSONL.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="Per-case total and SSE read timeout in seconds (default: 300).",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.concurrency < 1:
        raise SystemExit("--concurrency must be at least 1")
    if args.timeout <= 0:
        raise SystemExit("--timeout must be positive")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
