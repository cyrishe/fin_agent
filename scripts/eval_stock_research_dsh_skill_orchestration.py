"""Controlled one-question DSH skill orchestration replay.

Both arms receive the same frozen evidence. This measures the analysis phase,
not live retrieval or the web API. No product profile or database is changed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def save(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def billion(value: object) -> str:
    return f"{float(value) / 1e8:.2f} 亿" if value is not None else "缺失"


def paragraph_near(text: str, term: str, limit: int = 330) -> str:
    match = re.search(re.escape(term), text)
    if not match:
        return ""
    start = max(0, match.start() - 35)
    return re.sub(r"\s+", " ", text[start:start + limit]).strip()


def evidence_capsule(source: Path) -> str:
    data = json.loads(source.read_text(encoding="utf-8"))
    finance = next(item for item in data if item.get("api") == "stock.financial_3_table.query")
    segments = next(item for item in data if item.get("api") == "stock.business_segment.query")
    valuation = next(item for item in data if item.get("api") == "stock.pricevalue.query")
    percentile = next(item for item in data if item.get("api") == "stock.pricevalue.kd_pe_ttm_percentile")
    lines = [
        "对象：紫金矿业 601899.SH；资料截止：2026-09-30。以下为冻结的原始结果摘要，金额统一换算为亿元。",
        "F1 结构化财务三表（报告期、公告日随行；金额来自原表元字段）：",
    ]
    fields = [
        ("total_revenue", "营业总收入"), ("operating_cost", "营业成本"),
        ("operating_profit", "营业利润"), ("net_profit", "净利润"),
        ("parent_net_profit", "归母净利润"), ("cashflow_operating", "经营现金流"),
        ("cashflow_investing", "投资现金流"), ("cashflow_financing", "筹资现金流"),
        ("monetary_cap", "货币资金"),
    ]
    for row in finance["rows"]:
        if row.get("report_period") not in {"2025-06-30", "2026-06-30"}:
            continue
        parts = [f"{label} {billion(row.get(key))}" for key, label in fields]
        parts.extend([f"毛利率 {row.get('gross_margin')}%", f"资产负债率 {row.get('debt_ratio')}%"])
        lines.append(f"{row['report_period']}（公告 {row.get('ann_date')}）：" + "；".join(parts))
    lines.append("S1 结构化业务分部（内部销售未抵销；分品种成本/利润字段为空）：")
    for row in segments["rows"]:
        if row.get("report_period") in {"2025-06-30", "2026-06-30"} and row.get("project_name") in {
            "矿山产金", "矿山产铜", "矿山产锂", "内部销售抵销数", "内部销售抵消数"
        }:
            lines.append(f"{row['report_period']} {row['project_name']}收入 {billion(row.get('segment_sales'))}")
    vr = valuation["rows"][0]
    pr = percentile["rows"][0]
    lines.append(
        f"V1 结构化估值 {vr['tradedate']}：PE TTM {vr['pe_ttm']:.2f}、PB {vr['pb_mrq']:.2f}、"
        f"市值 {billion(vr['market_value'])}；V2 三年 PE 分位 {pr['pe_ttm_pctile_3y']:.2%}，有效窗口 {pr['window_count']}。"
    )
    articles = [row for item in data for row in item.get("rows", [])
                if isinstance(row, dict) and row.get("content_markdown")
                and "紫金矿业" in str(row.get("content_markdown"))]
    chosen = []
    for row in articles:
        published = str(row.get("publish_time") or "")[:10]
        if not published or published > "2026-09-30":
            continue
        text = row.get("content_markdown") or ""
        if "铜精矿单位成本2.44" in text and not any(tag == "W1" for tag, _ in chosen):
            chosen.append(("W1", row))
        if "215.86" in text and not any(tag == "W2" for tag, _ in chosen):
            chosen.append(("W2", row))
    for tag, row in chosen:
        text = row["content_markdown"]
        terms = ["矿产金47吨", "铜精矿单位成本2.44"] if tag == "W1" else ["紫金矿业经营现金流净额达"]
        excerpts = [paragraph_near(text, term) for term in terms]
        lines.append(f"{tag} 二手公开材料（{row.get('publish_time')}；{row.get('url')}）：" + " | ".join(dict.fromkeys(p for p in excerpts if p)))
    lines.append("缺口：冻结结果未给出购建长期资产支付现金的分项；投资现金净额不能直接当资本开支。W 类材料为媒体转述，不能升级为已读公司原始报告。")
    return "\n".join(lines)


def usage(notifications: list[object]) -> dict[str, object]:
    sessions: dict[str, dict[str, int]] = {}
    children: list[str] = []
    tools: list[str] = []
    for note in notifications:
        payload = getattr(note, "payload", {})
        if getattr(note, "method", "") == "subagent.started":
            child = payload.get("childSessionId")
            if isinstance(child, str):
                children.append(child)
        if getattr(note, "method", "") != "session.event":
            continue
        event = payload.get("event") or {}
        data = event.get("data") or {}
        sid = str(payload.get("sessionId") or "")
        if event.get("type") == "tool/call":
            tools.append(str(data.get("name") or ""))
        if event.get("type") != "assistant/message":
            continue
        u = data.get("usage") or {}
        if not u:
            continue
        rec = sessions.setdefault(sid, {"steps": 0, "input_tokens": 0, "cache_read_tokens": 0,
                                        "output_tokens": 0, "reasoning_tokens": 0})
        rec["steps"] += 1
        for key, source_key in [("input_tokens", "inputTokens"), ("cache_read_tokens", "cacheReadTokens"),
                                ("output_tokens", "outputTokens"), ("reasoning_tokens", "reasoningTokens")]:
            rec[key] += int(u.get(source_key) or 0)
    aggregate = {key: sum(v[key] for v in sessions.values()) for key in
                 ("steps", "input_tokens", "cache_read_tokens", "output_tokens", "reasoning_tokens")}
    aggregate["total_tokens"] = aggregate["input_tokens"] + aggregate["cache_read_tokens"] + aggregate["output_tokens"]
    return {"aggregate": aggregate, "sessions": sessions, "child_session_ids": children, "tool_calls": tools}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--arms", default="single,managed", help="Comma-separated: single,managed")
    parser.add_argument("--cases-file", type=Path,
                        default=ROOT / "tests/evals/stock_research_wencai_pair_20261007.json")
    parser.add_argument("--case-id", default="resource")
    parser.add_argument("--evidence-source", type=Path,
                        default=ROOT / "outputs/stock_research_wencai_pair_20261007/resource/frozen_evidence.json",
                        help="Local frozen tool results; raw external excerpts are not committed")
    parser.add_argument("--child-reasoning-effort", choices=("off", "low", "high", "max"))
    parser.add_argument("--child-max-tokens", type=int)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    from dotenv import load_dotenv, dotenv_values
    load_dotenv(ROOT / ".env", override=False)
    cfg = dotenv_values(ROOT / ".env_tmp")
    base_url = cfg.get("BASE_URL") or os.environ.get("LLM_BASE_URL")
    api_key = cfg.get("LLM_KEY") or os.environ.get("LLM_API_KEY")
    model = cfg.get("MODEL_NAME") or os.environ.get("LLM_DEFAULT_MODEL")
    if not all((base_url, api_key, model)):
        parser.error("LLM endpoint, key and model are required")
    from src.scenarios.financial_qa.dsh_service import _load_sdk_class
    Harness = _load_sdk_class()
    if not args.cases_file.is_file():
        parser.error(f"cases file not found: {args.cases_file}")
    if not args.evidence_source.is_file():
        parser.error(f"frozen evidence not found: {args.evidence_source}; pass --evidence-source")
    cases = json.loads(args.cases_file.read_text(encoding="utf-8"))["cases"]
    question = next((item["question"] for item in cases if item["id"] == args.case_id), None)
    if not question:
        parser.error(f"case id not found: {args.case_id}")
    evidence = evidence_capsule(args.evidence_source)
    (out / "evidence_capsule.md").write_text(evidence, encoding="utf-8")
    stock_method = (ROOT / "src/skills/finance-business/skills/stock-research/SKILL.md").read_text(encoding="utf-8")
    earnings_method = (ROOT / "src/skills/finance-business/skills/earnings-analysis/SKILL.md").read_text(encoding="utf-8")
    earnings_method += "\n" + (ROOT / "src/skills/finance-business/skills/earnings-analysis/references/method.md").read_text(encoding="utf-8")
    common_patch = [{"id": name, "disabled": True} for name in ("persistent-bash", "persistent-pwsh", "str-replace-editor")]
    patch_path = out / "no_tools.patch.json"
    save(patch_path, common_patch)
    managed_patch = out / "managed.patch.json"
    child_config = {
        "provider": "spawn", "toolName": "finance_skill_task", "backgroundMode": "one-shot", "maxDepth": 1,
        "persona": "你是股票分析师。只完成交给你的局部判断，不撰写整份报告。\n" + earnings_method,
    }
    if args.child_reasoning_effort or args.child_max_tokens:
        child_config["agentOptions"] = {
            **({"reasoningEffort": args.child_reasoning_effort} if args.child_reasoning_effort else {}),
            **({"maxTokens": args.child_max_tokens} if args.child_max_tokens else {}),
        }
    save(managed_patch, [*common_patch, {"insert": [
        {"id": "eval-subagent", "name": "@deepseek-ai/dsh-subagent"},
        {"id": "eval-subagent-spawn", "name": "@deepseek-ai/dsh-subagent-spawn-in-process", "config": {"providerName": "spawn"}},
        {"id": "eval-skill-task", "name": "@deepseek-ai/dsh-tool-subagent", "config": child_config},
    ]}])
    dsh_bin = shutil.which("dsh") or str(ROOT / "scripts/dsh_source_runtime.sh")
    baseline_prompt = (f"用户问题：{question}\n\n冻结证据：\n{evidence}\n\n"
                       f"个股研究方法：\n{stock_method}\n\n财报分析方法：\n{earnings_method}\n\n"
                       "请直接给统一的个股结论，正文约 900–1300 个汉字。只引用上述证据，不扩查。")
    managed_prompt = (f"用户问题：{question}\n\n冻结证据：\n{evidence}\n\n"
                      f"个股研究方法：\n{stock_method}\n\n"
                      "这是一道复杂个股研究题。先用 finance_skill_task 对利润与现金流质量、资本开支证据缺口做一次独立局部判断；"
                      "把该任务所需的冻结证据和来源标签交给子任务，要求它简短返回。随后由你综合量价、成本、资本开支和估值，"
                      "给一份约 900–1300 汉字的统一结论。只使用上述冻结证据，不扩查。")
    save(out / "manifest.json", {"date": datetime.now(timezone.utc).isoformat(), "model": model,
                                 "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                                 "question": question, "cases_file": str(args.cases_file),
                                 "evidence_source": str(args.evidence_source),
                                 "evidence_sha256": hashlib.sha256(args.evidence_source.read_bytes()).hexdigest(),
                                 "evidence_chars": len(evidence), "scope": "analysis-only replay; excludes retrieval, routing and web/API",
                                 "arms": args.arms, "child_agent_options": child_config.get("agentOptions", {})})
    arms = {arm.strip() for arm in args.arms.split(",") if arm.strip()}
    if not arms or arms - {"single", "managed"}:
        parser.error("--arms accepts only single,managed")
    for arm, patch, prompt in [("single", patch_path, baseline_prompt), ("managed", managed_patch, managed_prompt)]:
        if arm not in arms:
            continue
        print("START", arm, flush=True)
        started = time.perf_counter()
        try:
            with Harness(provider="deepseek-official", model=model, reasoning_effort="low", max_tokens=8192,
                         cwd=str(ROOT), runtime_cwd=str(ROOT), dsh_bin=dsh_bin, profile="sdk-minimal",
                         patches=(str(patch),), dsh_home=str(out / arm / "dsh_home"),
                         env={"DSH_SYSTEM_PROMPT": "你是股票分析师。回答应准确、简洁，区分事实和推断。"},
                         base_url=base_url, api_key=api_key, request_timeout_seconds=180,
                         initialize_timeout_seconds=120) as harness:
                result = harness.run(prompt, session_id=f"stock-research-{arm}")
            (out / arm / "answer.md").write_text(result.final_response, encoding="utf-8")
            stats = usage(result.notifications)
            save(out / arm / "metrics.json", {"seconds": round(time.perf_counter() - started, 3),
                                              "finish_reason": result.finish_reason,
                                              "answer_chars": len(result.final_response), **stats})
            print("DONE", arm, result.finish_reason, round(time.perf_counter() - started, 2), flush=True)
        except Exception as exc:
            save(out / arm / "error.json", {"seconds": round(time.perf_counter() - started, 3),
                                           "type": type(exc).__name__, "message": str(exc)})
            print("ERROR", arm, type(exc).__name__, str(exc)[:300], flush=True)


if __name__ == "__main__":
    main()
