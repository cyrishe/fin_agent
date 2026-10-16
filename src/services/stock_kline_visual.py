"""Same-source daily chart evidence for the conversation's existing main agent.

The visual model receives only a chart and the Skill's visual instructions.
Numerical facts and visual observations are combined by the calling agent.
"""
from __future__ import annotations

import base64
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import time

import pandas as pd

from src.services.stock_technical_history import (
    HISTORY_ANCHOR, completed_cutoff, market_connection, read_source_frame,
)


def model_call_recorder(calls, *, deadline):
    from openai import OpenAI
    from src.utils.ai_service import build_multimodal_user_message
    client = OpenAI(base_url=os.environ.get("LLM_BASE_URL"),
                    api_key=os.environ.get("LLM_API_KEY"), max_retries=0)

    def call(stage, prompt, images, structured):
        model = os.environ.get("LLM_VISION_MODEL" if images else "LLM_DEFAULT_MODEL", "").strip()
        started = time.monotonic()
        record = {"stage": stage, "model": model, "prompt": prompt,
                  "image_sha256": [hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in images]}
        try:
            remaining = deadline - started
            if remaining <= 0:
                raise TimeoutError("visual evidence budget exhausted")
            response = client.chat.completions.create(
                model=model, messages=[build_multimodal_user_message(text=prompt, image_paths=images)],
                # These are bounded evidence tasks, not the main research loop.
                # Reserve output for the result instead of exhausting it in reasoning.
                reasoning_effort="low", max_tokens=4096, timeout=min(60, remaining),
                **({"extra_body": {"enable_thinking": False}} if structured else {}),
                **({"response_format": {"type": "json_object"}} if structured else {}),
            )
            choice = response.choices[0]
            record.update(response=choice.message.content or "", finish_reason=choice.finish_reason,
                          usage=response.usage.model_dump() if response.usage else None)
            if structured and choice.finish_reason == "stop":
                try:
                    parsed = json.loads(choice.message.content or "")
                    record["parsed"] = parsed if isinstance(parsed, dict) else None
                except json.JSONDecodeError:
                    record["parsed"] = None
        except Exception as exc:
            # Provider exceptions may contain endpoints; business output needs
            # only the failing operation, not configuration or credentials.
            record.update(finish_reason="error", response="", error=type(exc).__name__)
        record["duration_ms"] = round((time.monotonic()-started)*1000)
        calls.append(record)
        return record
    call.close = client.close
    return call


def inspect_daily_chart(*, code, question="观察近期走势与量价变化", as_of=None,
                        window=10, review_patterns=True,
                        connection_factory=market_connection, call_factory=model_call_recorder):
    if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}\.(SH|SZ|BJ)", code):
        raise ValueError("code must be a full stock code, e.g. 600519.SH")
    if type(window) is not int or not 1 <= window <= 60:
        raise ValueError("window must be 1..60 completed daily candles")
    if type(review_patterns) is not bool:
        raise ValueError("review_patterns must be a boolean")
    if not isinstance(question, str) or len(question) > 4000:
        raise ValueError("question must be text of at most 4000 characters")
    requested = date.fromisoformat(as_of) if as_of else None
    cutoff = completed_cutoff(requested)
    if not os.environ.get("LLM_VISION_MODEL", "").strip():
        return {"ok": False, "error": "尚未配置视觉模型 LLM_VISION_MODEL，未执行看图；可继续使用日线与数值指标。"}
    started = time.monotonic()
    with connection_factory() as connection:
        with connection.cursor() as cursor:
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            frame, source = read_source_frame(cursor, code, cutoff)
    if frame.empty or pd.isna(frame.iloc[-1].raw_close) or pd.isna(frame.iloc[-1].close):
        return {"ok": False, "error": "截止日前没有可绘图的完整日线价格，未执行看图。", "provider_evidence": source}
    from src.experiments.kline_patterns.engine import prepare, REVISION
    from src.experiments.kline_patterns.analysis import analyze, PATTERNS
    scale = frame.iloc[-1].raw_close / frame.iloc[-1].close
    frame[["open", "high", "low", "close"]] *= scale
    features = prepare(frame)
    calls = []
    call = call_factory(calls, deadline=started+150)
    with TemporaryDirectory(prefix="fin-kline-") as folder:
        try:
            result = analyze(features, code, question, folder, call, window,
                             review_patterns=review_patterns, synthesize=False)
        finally:
            close = getattr(call, "close", None)
            if callable(close):
                close()
        charts = []
        chart_titles = {"current.png": "近期日K与成交量"}
        for index, review in enumerate(result["reviews"], 1):
            pattern_id, signal_date = review["candidate_id"].rsplit("@", 1)
            chart_titles[f"candidate_{index}/detail.png"] = f"{PATTERNS[pattern_id]['name']} · 信号日 {signal_date}"
        for path in sorted(Path(folder).rglob("*.png")):
            content = path.read_bytes()
            metadata_path = path.with_name(path.stem+"_metadata.json")
            charts.append({"name": str(path.relative_to(folder)),
                "title": chart_titles.get(str(path.relative_to(folder)), "形态局部图"),
                "sha256": hashlib.sha256(content).hexdigest(),
                "metadata": json.loads(metadata_path.read_text()),
                "url": "data:image/png;base64,"+base64.b64encode(content).decode("ascii")})
    evidence = {"source": "kingdomai.kcrp_stock_price", **source,
        "requested_as_of": as_of, "completed_cutoff": cutoff.isoformat(),
        "history_anchor": HISTORY_ANCHOR.isoformat(), "price_basis": "hfq scaled to cutoff raw close",
        "volume_unit": "股", "pattern_revision": REVISION,
        "scope": "已完成日线；当前源数据修订，不是历史时点当时已知版本。",
        "selected_ids": result["selected_ids"], "review_patterns": review_patterns}
    return {"ok": True, "data": result["evidence_text"], "provider_evidence": evidence,
            "charts": charts, "llm_calls": calls,
            "duration_ms": round((time.monotonic()-started)*1000)}
