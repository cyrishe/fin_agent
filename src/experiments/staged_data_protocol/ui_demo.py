from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterator, Mapping

from flask import Flask, Response, request, stream_with_context

from src.experiments.staged_data_protocol.phrase1_stage import (
    parse_subject_dataview_contract,
    render_stage1_prompt_template,
    run_phrase1_case,
)
from src.experiments.staged_data_protocol.phase2.api_runner import execute_api_call
from src.experiments.staged_data_protocol.phase2.call_parser import parse_api_call
from src.experiments.staged_data_protocol.phase2.call_validator import validate_call
from src.experiments.staged_data_protocol.phase2.context_builder import build_context_sections
from src.experiments.staged_data_protocol.phase2.engine import (
    build_final_check_prompt,
    parse_final_check_response,
    parse_phase2_response,
)
from src.experiments.staged_data_protocol.phase2.models import ResultHandle, Step
from src.experiments.staged_data_protocol.phase2.step_parser import parse_steps


app = Flask(__name__)

PHASE1_PROMPT_PATH = Path("phrase_1_prompt.md")


@app.get("/")
def page() -> str:
    return HTML


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/run")
def run_stream() -> Response:
    question = str(request.args.get("question") or "").strip()
    if not question:
        return Response(_sse("error", {"message": "question is required"}), mimetype="text/event-stream")
    return Response(
        stream_with_context(_run_pipeline(question)),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _run_pipeline(question: str) -> Iterator[str]:
    started = time.time()
    yield _sse("run_started", {"question": question})
    try:
        prompt_template = render_stage1_prompt_template(PHASE1_PROMPT_PATH.read_text(encoding="utf-8"))
        contract = parse_subject_dataview_contract(prompt_template)
        yield _sse("phase1_started", {"title": "第一阶段：理解问题并拆解步骤"})
        phase1 = run_phrase1_case(
            {"case_id": "adhoc", "question": question},
            prompt_template=prompt_template,
            contract=contract,
        )
        yield _sse(
            "phase1_completed",
            {
                "analyze": phase1.get("analyze") or "",
                "steps": phase1.get("steps") or [],
                "parsed_steps": phase1.get("parsed_steps") or [],
                "validation_errors": phase1.get("validation_errors") or [],
                "elapsed_ms": _elapsed_ms(started),
            },
        )
        if phase1.get("validation_errors"):
            yield _sse("run_completed", {"status": "phase1_error", "elapsed_ms": _elapsed_ms(started)})
            return

        previous_results: dict[str, ResultHandle] = {}
        feedback_by_index: dict[int, list[str]] = {}
        attempts_by_index: dict[int, int] = {}
        call_rows: list[dict[str, Any]] = []
        final_checks: list[dict[str, Any]] = []
        steps = parse_steps(phase1.get("steps") or [])
        yield _sse("phase2_started", {"title": "第二阶段：逐步绑定 API 并执行", "step_count": len(steps)})
        index = 0
        iterations = 0
        max_iterations = max(len(steps) * 4, 4) + max(len(steps) + 1, 1) * 2
        while iterations < max_iterations:
            iterations += 1
            if index >= len(steps):
                if len(final_checks) >= 2:
                    break
                check_started = time.time()
                yield _sse("final_check_started", {"round": len(final_checks) + 1, "title": "最终检查：核对需求是否完整满足"})
                check_prompt = build_final_check_prompt(
                    question=question,
                    steps=steps,
                    calls=call_rows,
                    previous_results=previous_results,
                )
                raw_check = _call_final_check_llm(check_prompt)
                check = parse_final_check_response(raw_check)
                final_checks.append({"status": check["status"], "feedback": check.get("feedback") or ""})
                yield _sse(
                    "final_check_completed",
                    {
                        "round": len(final_checks),
                        "status": check["status"],
                        "feedback": check.get("feedback") or "",
                        "raw_response": raw_check,
                        "elapsed_ms": _elapsed_ms(check_started),
                    },
                )
                if check["status"] == "OK":
                    break
                target_index = _final_check_target_index(steps)
                feedback = f"FINAL_CHECK_FEEDBACK: {check.get('feedback') or ''}"
                repair_step = _final_check_repair_step(steps[target_index], len(steps) + 1, check.get("feedback") or "")
                steps.append(repair_step)
                repair_index = len(steps) - 1
                feedback_by_index.setdefault(repair_index, []).append(feedback)
                index = repair_index
                yield _sse(
                    "final_check_retry",
                    {
                        "round": len(final_checks),
                        "target_index": repair_index + 1,
                        "source_index": target_index + 1,
                        "feedback": feedback,
                    },
                )
                continue

            step = steps[index]
            display_index = index + 1
            result_id = f"r{display_index}"
            step_started = time.time()
            yield _sse(
                "step_started",
                {
                    "index": display_index,
                    "result_id": result_id,
                    "step_id": step.step_id,
                    "subject": step.subject,
                    "dataview": step.dataview,
                    "condition": step.condition_desc,
                    "is_output": step.is_output,
                    "raw": step.raw,
                },
            )
            sections = build_context_sections(
                step=step,
                previous_results=previous_results,
                validation_feedback=feedback_by_index.get(index, []),
                result_id=result_id,
            )
            yield _sse(
                "step_context",
                {
                    "index": display_index,
                    "request_types": sections.get("request_types") or "",
                    "current_dataview": sections.get("current_dataview") or "",
                    "available_apis": sections.get("available_apis") or "",
                    "previous_results": sections.get("previous_results") or "",
                    "session_results": sections.get("session_results") or "",
                },
            )
            raw_response = _call_phase2_llm(question=question, context_sections=sections)
            response = parse_phase2_response(raw_response)
            yield _sse(
                "request_generated",
                {
                    "index": display_index,
                    "result_id": result_id,
                    "raw_call": _display_request(response.get("res")),
                    "request": response.get("res"),
                    "raw_response": raw_response,
                    "phase2_status": response["status"],
                },
            )
            if response["status"] == "roll_back":
                issue = str(response.get("res") or "").strip() or "phase2 requested rollback"
                target_index = max(index - 1, 0)
                feedback_by_index.setdefault(target_index, []).append(f"ROLLBACK_FROM_{result_id}: {issue}")
                _drop_results_from(previous_results, target_index + 1)
                yield _sse(
                    "step_rollback",
                    {
                        "index": display_index,
                        "from_result_id": result_id,
                        "rollback_to": f"r{target_index + 1}",
                        "reason": issue,
                        "elapsed_ms": _elapsed_ms(step_started),
                    },
                )
                call_rows.append(
                    {
                        "step": step.raw,
                        "raw_call": raw_response,
                        "call": None,
                        "validation": {"ok": False, "errors": [f"ROLLBACK: {issue}"], "warnings": []},
                        "execution_result": None,
                    }
                )
                index = target_index
                continue

            parsed = _parse_validate_execute(response.get("res"), previous_results, expected_result_id=result_id)
            call_rows.append(
                {
                    "step": step.raw,
                    "raw_call": parsed.get("raw_call") or _display_request(response.get("res")),
                    "call": parsed.get("call"),
                    "validation": parsed.get("validation"),
                    "execution_result": parsed.get("execution_result"),
                }
            )
            yield _sse(
                "request_validated",
                {
                    "index": display_index,
                    "validation": parsed["validation"],
                    "call": parsed["call"],
                },
            )
            if parsed.get("execution_result"):
                result = parsed["execution_result"]
                data = _compact_data(result.get("data"))
                yield _sse(
                    "step_executed",
                    {
                        "index": display_index,
                        "result_id": result.get("name"),
                        "api": result.get("api"),
                        "is_dynamic_code": str(result.get("api") or "").endswith(".dynamic_cal"),
                        "columns": result.get("columns") or [],
                        "data": data,
                        "elapsed_ms": _elapsed_ms(step_started),
                    },
                )
                previous_results[result["name"]] = ResultHandle(
                    name=result["name"],
                    api=result["api"],
                    columns=list(result.get("columns") or []),
                    data=result.get("data"),
                    step_id=step.step_id,
                    task=step.condition_desc,
                )
                feedback_by_index.pop(index, None)
                index += 1
            else:
                yield _sse(
                    "step_executed",
                    {
                        "index": display_index,
                        "result_id": result_id,
                        "api": "",
                        "columns": [],
                        "data": {"status": "validation_failed", "rows": []},
                        "elapsed_ms": _elapsed_ms(step_started),
                    },
                )
                attempts_by_index[index] = attempts_by_index.get(index, 0) + 1
                feedback_by_index.setdefault(index, []).extend(parsed["validation"]["errors"])
                if attempts_by_index[index] >= 2:
                    index += 1
            yield _sse("step_completed", {"index": display_index, "elapsed_ms": _elapsed_ms(step_started)})
        final_status = final_checks[-1]["status"] if final_checks else "missing"
        status = "ok" if index >= len(steps) and final_status == "OK" else "loop_exhausted"
        yield _sse("run_completed", {"status": status, "elapsed_ms": _elapsed_ms(started)})
    except Exception as exc:  # noqa: BLE001 - demo stream should surface failures to UI.
        yield _sse("error", {"message": str(exc), "elapsed_ms": _elapsed_ms(started)})


def _call_phase2_llm(*, question: str, context_sections: Mapping[str, str]) -> str:
    from src.experiments.staged_data_protocol.phase2.engine import _call_llm

    return _call_llm(question=question, context_sections=context_sections)


def _call_final_check_llm(prompt: str) -> str:
    from src.experiments.staged_data_protocol.phase2.engine import _call_final_check_llm

    return _call_final_check_llm(prompt)


def _parse_validate_execute(
    request_payload: Any,
    previous_results: Mapping[str, ResultHandle],
    *,
    expected_result_id: str,
) -> dict[str, Any]:
    from src.experiments.staged_data_protocol.phase2.engine import _api_call_from_payload

    try:
        call = _api_call_from_payload(request_payload)
    except Exception as exc:  # noqa: BLE001
        return {
            "raw_call": request_payload,
            "call": None,
            "validation": {"ok": False, "errors": [f"PARSE_ERROR: {exc}"], "warnings": []},
            "execution_result": None,
        }
    validation = validate_call(call, previous_results)
    if call.result_id != expected_result_id:
        validation.ok = False
        validation.errors.append(f"RESULT_ID_ERROR: expected {expected_result_id}, got {call.result_id}")
    result = execute_api_call(call, previous_results=previous_results) if validation.ok else None
    return {
        "raw_call": call.raw,
        "call": {"result_id": call.result_id, "api": call.api, "args": call.args, "outputs": call.outputs},
        "validation": {"ok": validation.ok, "errors": validation.errors, "warnings": validation.warnings},
        "execution_result": {
            "name": result.name,
            "api": result.api,
            "columns": result.columns,
            "data": result.data,
        }
        if result
        else None,
    }


def _display_request(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, Mapping):
        return json.dumps(payload, ensure_ascii=False, default=str, indent=2)
    return str(payload or "")


def _compact_data(data: Any) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        return {"status": "unknown", "rows": []}
    rows = data.get("rows")
    compact = dict(data)
    if isinstance(rows, list):
        compact["rows"] = rows[:20]
        compact["row_count"] = data.get("row_count", len(rows))
        compact["shown_rows"] = min(len(rows), 20)
    return compact


def _drop_results_from(previous_results: dict[str, ResultHandle], step_number: int) -> None:
    for name in list(previous_results):
        if not name.startswith("r"):
            continue
        try:
            parsed = int(name[1:])
        except ValueError:
            continue
        if parsed >= step_number:
            previous_results.pop(name, None)


def _final_check_target_index(steps: list[Any]) -> int:
    for index in range(len(steps) - 1, -1, -1):
        raw = str(getattr(steps[index], "raw", "") or "")
        if "(output)" in raw or "（output）" in raw:
            return index
    return max(len(steps) - 1, 0)


def _final_check_repair_step(template: Any, step_number: int, feedback: str) -> Step:
    condition = f"根据最终检查反馈补充查询：{str(feedback or '').strip()} (output)"
    raw = f"S{step_number} | {template.subject} | {template.dataview} | {condition}"
    return Step(
        step_id=f"S{step_number}",
        subject=template.subject,
        dataview=template.dataview,
        condition_desc=condition,
        raw=raw,
    )


def _elapsed_ms(started: float) -> int:
    return int((time.time() - started) * 1000)


def _sse(event: str, payload: Mapping[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Staged Data Protocol Demo</title>
  <style>
    :root {
      --bg: #f6f7fb;
      --panel: #fff;
      --line: #e8ebf0;
      --text: #1d2433;
      --muted: #667085;
      --blue: #2f7df6;
      --blue-soft: #e8f1ff;
      --green: #16a34a;
      --red: #dc2626;
      --amber: #b45309;
      --shadow: 0 12px 30px rgba(16, 24, 40, .08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    .shell { max-width: 1120px; margin: 0 auto; padding: 24px 20px 96px; }
    .topbar { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 20px; }
    .brand { font-weight: 700; font-size: 18px; }
    .hint { color: var(--muted); font-size: 13px; }
    .ask {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      box-shadow: var(--shadow);
    }
    textarea {
      min-height: 46px;
      resize: vertical;
      border: 0;
      outline: none;
      font-size: 15px;
      line-height: 1.5;
      color: var(--text);
    }
    button {
      border: 0;
      border-radius: 7px;
      background: var(--blue);
      color: white;
      padding: 0 18px;
      font-weight: 650;
      cursor: pointer;
    }
    button:disabled { opacity: .55; cursor: not-allowed; }
    .chat { margin-top: 24px; display: flex; flex-direction: column; gap: 14px; }
    .bubble {
      align-self: flex-end;
      max-width: 640px;
      background: #dbe8ff;
      border-radius: 8px;
      padding: 12px 14px;
      line-height: 1.5;
    }
    .run-card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      box-shadow: var(--shadow);
    }
    .run-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 14px 16px;
      background: #f1f3f7;
      border-bottom: 1px solid var(--line);
      cursor: pointer;
    }
    .run-title { font-weight: 700; }
    .run-subtitle { margin-top: 4px; color: var(--muted); font-size: 13px; }
    .run-body { padding: 14px 16px 18px; display: flex; flex-direction: column; gap: 14px; }
    .collapsed .run-body { display: none; }
    .phase {
      border-left: 3px solid var(--blue);
      padding-left: 12px;
    }
    .phase h3 { margin: 0 0 8px; font-size: 15px; }
    .analysis { color: #344054; line-height: 1.6; }
    .steps { display: flex; flex-direction: column; gap: 8px; }
    .step {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      overflow: hidden;
    }
    .step-head {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 11px 12px;
      background: #fcfcfd;
      border-bottom: 1px solid var(--line);
    }
    .dot {
      width: 18px;
      height: 18px;
      border-radius: 50%;
      background: #cfd4dc;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      color: #fff;
      font-size: 12px;
      flex: none;
    }
    .dot.running { background: var(--blue); }
    .dot.ok { background: var(--green); }
    .dot.error { background: var(--red); }
    .step-title { font-weight: 650; }
    .step-meta { color: var(--muted); font-size: 13px; margin-top: 2px; }
    .step-body { padding: 12px; display: grid; gap: 10px; }
    details {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcff;
    }
    summary { cursor: pointer; padding: 9px 10px; font-weight: 650; }
    pre {
      margin: 0;
      padding: 10px;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
      color: #344054;
      border-top: 1px solid var(--line);
      font-size: 12px;
      line-height: 1.45;
    }
    .result {
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }
    .result-head {
      display: flex;
      justify-content: space-between;
      padding: 9px 10px;
      background: #f8fafc;
      border-bottom: 1px solid var(--line);
      font-weight: 650;
    }
    .pill { font-size: 12px; color: var(--muted); }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { border-bottom: 1px solid var(--line); padding: 9px 10px; text-align: left; }
    th { color: #475467; font-weight: 650; background: #fff; }
    .empty { padding: 12px; color: var(--muted); }
    .status-ok { color: var(--green); }
    .status-error { color: var(--red); }
        .status-warn { color: var(--amber); }
    .code-chip {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border: 1px solid #bad7ff;
      background: var(--blue-soft);
      color: #175cd3;
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 12px;
      font-weight: 650;
    }
    .code-block summary {
      color: #175cd3;
    }
    @media (max-width: 720px) {
      .shell { padding: 16px 12px 88px; }
      .ask { grid-template-columns: 1fr; }
      button { height: 42px; }
      .bubble { max-width: 100%; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <div class="topbar">
      <div>
        <div class="brand">Staged Data Protocol Demo</div>
        <div class="hint">Phase1 拆解问题，Phase2 按步骤生成并执行 API request。</div>
      </div>
    </div>
    <form class="ask" id="askForm">
      <textarea id="question" placeholder="输入金融查询问题">筛选最近20个交易日收盘价高于开盘价超过15天的股票。</textarea>
      <button id="runBtn" type="submit">运行</button>
    </form>
    <section class="chat" id="chat"></section>
  </main>
  <script>
    const form = document.getElementById('askForm');
    const input = document.getElementById('question');
    const chat = document.getElementById('chat');
    const button = document.getElementById('runBtn');
    let source = null;
    let currentCard = null;
    let stepMap = new Map();

    form.addEventListener('submit', (event) => {
      event.preventDefault();
      const question = input.value.trim();
      if (!question) return;
      if (source) source.close();
      chat.innerHTML = '';
      stepMap = new Map();
      addBubble(question);
      currentCard = addRunCard();
      button.disabled = true;
      source = new EventSource('/api/run?question=' + encodeURIComponent(question));
      bindEvents(source);
    });

    function bindEvents(es) {
      es.addEventListener('run_started', (event) => setSubtitle('开始执行'));
      es.addEventListener('phase1_started', (event) => setSubtitle(JSON.parse(event.data).title));
      es.addEventListener('phase1_completed', (event) => renderPhase1(JSON.parse(event.data)));
      es.addEventListener('phase2_started', (event) => setSubtitle('进入逐步执行'));
      es.addEventListener('step_started', (event) => renderStepStart(JSON.parse(event.data)));
      es.addEventListener('step_context', (event) => renderStepContext(JSON.parse(event.data)));
      es.addEventListener('request_generated', (event) => renderRequest(JSON.parse(event.data)));
      es.addEventListener('request_validated', (event) => renderValidation(JSON.parse(event.data)));
      es.addEventListener('step_executed', (event) => renderExecution(JSON.parse(event.data)));
      es.addEventListener('step_rollback', (event) => renderRollback(JSON.parse(event.data)));
      es.addEventListener('step_completed', (event) => markStepDone(JSON.parse(event.data)));
      es.addEventListener('final_check_started', (event) => renderFinalCheckStarted(JSON.parse(event.data)));
      es.addEventListener('final_check_completed', (event) => renderFinalCheckCompleted(JSON.parse(event.data)));
      es.addEventListener('final_check_retry', (event) => renderFinalCheckRetry(JSON.parse(event.data)));
      es.addEventListener('run_completed', (event) => {
        const data = JSON.parse(event.data);
        setSubtitle(data.status === 'ok' ? `执行完成 · ${formatMs(data.elapsed_ms)}` : `结束 · ${data.status}`);
        button.disabled = false;
        es.close();
      });
      es.addEventListener('error', (event) => {
        let data = {};
        try { data = JSON.parse(event.data); } catch (_) {}
        setSubtitle(data.message ? '执行失败：' + data.message : '连接结束');
        button.disabled = false;
        es.close();
      });
    }

    function addBubble(text) {
      const el = document.createElement('div');
      el.className = 'bubble';
      el.textContent = text;
      chat.appendChild(el);
    }

    function addRunCard() {
      const card = document.createElement('article');
      card.className = 'run-card';
      card.innerHTML = `
        <div class="run-head">
          <div>
            <div class="run-title">优小智 模型答案生成中</div>
            <div class="run-subtitle" id="runSubtitle">等待事件流</div>
          </div>
          <div>⌃</div>
        </div>
        <div class="run-body" id="runBody"></div>`;
      card.querySelector('.run-head').addEventListener('click', () => card.classList.toggle('collapsed'));
      chat.appendChild(card);
      return card;
    }

    function setSubtitle(text) {
      const el = currentCard?.querySelector('#runSubtitle');
      if (el) el.textContent = text;
    }

    function body() { return currentCard.querySelector('#runBody'); }

    function renderPhase1(data) {
      const section = document.createElement('section');
      section.className = 'phase';
      const errors = data.validation_errors || [];
      section.innerHTML = `
        <h3>问题分析完成</h3>
        <div class="analysis">${escapeHtml(data.analyze || '无分析文本')}</div>
        <details open>
          <summary>阶段一步骤 (${(data.steps || []).length})</summary>
          <pre>${escapeHtml((data.steps || []).join('\\n') || '无步骤')}</pre>
        </details>
        ${errors.length ? `<pre class="status-error">${escapeHtml(errors.join('\\n'))}</pre>` : ''}`;
      body().appendChild(section);
      setSubtitle(errors.length ? '第一阶段存在结构问题' : '第一阶段完成，准备逐步执行');
    }

    function renderStepStart(data) {
      const step = document.createElement('section');
      step.className = 'step';
      step.innerHTML = `
        <div class="step-head">
          <span class="dot running">${data.index}</span>
          <div>
            <div class="step-title">第 ${data.index} 步 · ${escapeHtml(data.subject)}.${escapeHtml(data.dataview)}</div>
            <div class="step-meta">${escapeHtml(data.condition || '')}</div>
          </div>
        </div>
        <div class="step-body"></div>`;
      body().appendChild(step);
      stepMap.set(data.index, step);
      setSubtitle(`正在执行第 ${data.index} 步`);
    }

    function renderStepContext(data) {
      const blocks = [
        '# REQUEST TYPES\\n' + (data.request_types || ''),
        '# CURRENT DATAVIEW\\n' + (data.current_dataview || ''),
        '# AVAILABLE APIS\\n' + (data.available_apis || '')
      ];
      if (data.previous_results) {
        blocks.push('# PREVIOUS RESULTS\\n' + data.previous_results);
      }
      appendDetails(data.index, '思考过程：当前可用 API 上下文', blocks.join('\\n\\n'));
    }

    function renderRequest(data) {
      const title = data.phase2_status === 'roll_back' ? '模型请求回退' : '生成的 API Request';
      appendDetails(data.index, title, data.raw_call || data.raw_response || '', true);
    }

    function renderRollback(data) {
      appendDetails(
        data.index,
        `回退到 ${data.rollback_to || '上一阶段'}`,
        data.reason || '',
        true
      );
      const step = stepMap.get(data.index);
      if (step) {
        const dot = step.querySelector('.dot');
        dot.classList.remove('running');
        dot.classList.add('error');
      }
      setSubtitle(`第 ${data.index} 步请求回退`);
    }

    function renderValidation(data) {
      const ok = data.validation && data.validation.ok;
      appendDetails(
        data.index,
        ok ? '协议校验通过' : '协议校验失败',
        JSON.stringify(data.validation || {}, null, 2),
        !ok
      );
    }

    function renderExecution(data) {
      const step = stepMap.get(data.index);
      if (!step) return;
      const wrap = document.createElement('div');
      wrap.className = 'result';
      const status = data.data?.status || 'unknown';
      const rows = data.data?.rows || [];
      const cols = data.columns || data.data?.columns || [];
      const dynamicCode = data.is_dynamic_code || Boolean(data.data?.code);
      wrap.innerHTML = `
        <div class="result-head">
          <span>执行结果 · ${escapeHtml(data.api || '')} ${dynamicCode ? '<span class="code-chip">动态代码计算</span>' : ''}</span>
          <span class="pill">${escapeHtml(status)} · ${formatMs(data.elapsed_ms)}</span>
        </div>
        ${renderDynamicCode(data.data, dynamicCode)}
        ${renderTable(cols, rows, data.data)}`;
      step.querySelector('.step-body').appendChild(wrap);
    }

    function markStepDone(data) {
      const step = stepMap.get(data.index);
      if (!step) return;
      const dot = step.querySelector('.dot');
      dot.classList.remove('running');
      dot.classList.add('ok');
      setSubtitle(`第 ${data.index} 步完成`);
    }

    function renderFinalCheckStarted(data) {
      const section = document.createElement('section');
      section.className = 'phase';
      section.dataset.finalCheckRound = data.round;
      section.innerHTML = `
        <h3>最终检查 · 第 ${data.round} 轮</h3>
        <div class="analysis">正在核对原始需求、执行步骤和结果 schema。</div>`;
      body().appendChild(section);
      setSubtitle('最终检查中');
    }

    function renderFinalCheckCompleted(data) {
      const section = currentCard?.querySelector(`[data-final-check-round="${data.round}"]`);
      if (!section) return;
      const ok = data.status === 'OK';
      const div = document.createElement('div');
      div.className = ok ? 'analysis status-ok' : 'analysis status-warn';
      div.textContent = ok ? `检查通过：${data.feedback || '需求已满足'}` : `需要复查：${data.feedback || '发现可能的问题'}`;
      section.appendChild(div);
      appendStandaloneDetails(section, '最终检查原始输出', data.raw_response || '', false);
      setSubtitle(ok ? '最终检查通过' : '最终检查发现问题，准备重试');
    }

    function renderFinalCheckRetry(data) {
      const section = currentCard?.querySelector(`[data-final-check-round="${data.round}"]`);
      if (!section) return;
      const div = document.createElement('div');
      div.className = 'analysis status-warn';
      div.textContent = `带着反馈重试第 ${data.target_index} 步。`;
      section.appendChild(div);
      appendStandaloneDetails(section, '回灌给下一轮的反馈', data.feedback || '', true);
      setSubtitle(`最终检查反馈已回灌，重试第 ${data.target_index} 步`);
    }

    function appendDetails(index, title, text, open = false) {
      const step = stepMap.get(index);
      if (!step) return;
      const details = document.createElement('details');
      if (open) details.open = true;
      details.innerHTML = `<summary>${escapeHtml(title)}</summary><pre>${escapeHtml(text)}</pre>`;
      step.querySelector('.step-body').appendChild(details);
    }

    function appendStandaloneDetails(parent, title, text, open = false) {
      const details = document.createElement('details');
      if (open) details.open = true;
      details.innerHTML = `<summary>${escapeHtml(title)}</summary><pre>${escapeHtml(text)}</pre>`;
      parent.appendChild(details);
    }

    function renderTable(cols, rows, data) {
      if (!rows.length) {
        const reason = data?.provider ? `provider: ${data.provider}` : '无返回行';
        return `<div class="empty">${escapeHtml(reason)}</div>`;
      }
      const safeCols = cols.length ? cols : Object.keys(rows[0] || {});
      return `<table><thead><tr>${safeCols.map(c => `<th>${escapeHtml(c)}</th>`).join('')}</tr></thead>
        <tbody>${rows.map(row => `<tr>${safeCols.map(c => `<td>${escapeHtml(row[c] ?? '')}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
    }

    function renderDynamicCode(data, dynamicCode) {
      if (!dynamicCode) return '';
      const code = data?.code || '本次动态计算没有返回代码。';
      const schema = data?.output_schema ? JSON.stringify(data.output_schema, null, 2) : '[]';
      return `
        <details class="code-block" open>
          <summary>动态 Python 代码与输出字段</summary>
          <pre>${escapeHtml(code)}</pre>
        </details>
        <details class="code-block">
          <summary>output_schema</summary>
          <pre>${escapeHtml(schema)}</pre>
        </details>`;
    }

    function escapeHtml(value) {
      return String(value ?? '').replace(/[&<>"']/g, char => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      }[char]));
    }

    function formatMs(ms) {
      if (!Number.isFinite(Number(ms))) return '';
      const seconds = Number(ms) / 1000;
      return seconds >= 1 ? `${seconds.toFixed(1)}s` : `${ms}ms`;
    }
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5062, debug=False, threaded=True)
