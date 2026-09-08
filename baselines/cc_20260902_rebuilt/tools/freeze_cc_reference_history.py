#!/usr/bin/env python3
"""Freeze the existing 2026-09-02 CC evidence without invoking any service.

Reads only historical JSON and writes only the requested history directory.
The complete result tree is preserved, except credential/header/cookie values.
No environment files, model credentials, databases or network are accessed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any


REDACTED = "[REDACTED: credential or transport metadata]"
SOURCE = "outputs/financial_qa_prompt_policy_random20_20260902/cc_results.json"
COMPARISON = "outputs/financial_qa_prompt_policy_random20_20260902/comparison_analysis.json"
EVENTS = "outputs/financial_qa_cc/events.jsonl"
EXPECTED_IDS = (
    "RTE003", "RTE009", "RTE033", "RTE034", "RTE044", "RTE045", "RTE046",
    "RTE053", "RTE055", "RTE056", "RTE059", "RTE067", "RTE070", "RTE073",
    "RTE076", "RTE080", "RTE082", "RTE089", "RTE093", "RTE096",
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    if normalized.endswith(("headers", "header", "cookies", "cookie")):
        return True
    return normalized in {"authorization", "proxyauthorization", "password", "passwd", "secret", "token"} or normalized.endswith(
        ("apikey", "apisecret", "accesstoken", "refreshtoken", "authtoken", "idtoken", "securitytoken", "accesskeysecret", "secretaccesskey")
    )


STRING_SECRETS = (
    ("bearer credential", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")),
    ("API key literal", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    ("private key", re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z ]+ )?PRIVATE KEY-----")),
    ("credential assignment", re.compile(r"(?i)\b[A-Z0-9_]*(?:API_KEY|AUTH_TOKEN|ACCESS_TOKEN|REFRESH_TOKEN|PASSWORD|API_SECRET|ACCESS_KEY_SECRET)\s*[=:]\s*[\"']?[^\s\"',;}]+")),
    ("URL credential", re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s/@:]+:[^\s/@]+@")),
    ("credential URL parameter", re.compile(r"(?i)[?&](?:api_key|apikey|key|token|access_token|password|signature)=[^\s&#\"']+")),
    ("HTTP credential header", re.compile(r"(?im)^(?:authorization|proxy-authorization|cookie|set-cookie|x-api-key)\s*:[^\r\n]+")),
)


def sanitize(value: Any, path: str = "$", redactions: list[dict[str, str]] | None = None) -> Any:
    """Return a new tree; the audit contains paths/reasons, never secret values."""
    if redactions is None:
        redactions = []
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            child_path = path + "[" + json.dumps(key, ensure_ascii=False) + "]"
            if sensitive_key(key) and item is not None and item != "":
                redactions.append({"path": child_path, "reason": "credential/header/cookie field"})
                result[key] = REDACTED
            else:
                result[key] = sanitize(item, child_path, redactions)
        return result
    if isinstance(value, list):
        return [sanitize(item, f"{path}[{index}]", redactions) for index, item in enumerate(value)]
    if isinstance(value, str):
        # Tool/SSE traces sometimes contain a JSON document as a string. Scan
        # its fields too, preserving its bytes when no secret is found.
        if value.lstrip().startswith(("{", "[")):
            try:
                embedded = json.loads(value)
            except (ValueError, TypeError):
                embedded = None
            if isinstance(embedded, (dict, list)):
                before = len(redactions)
                safe_embedded = sanitize(embedded, path + "::<json>", redactions)
                if len(redactions) != before:
                    value = json.dumps(safe_embedded, ensure_ascii=False)
        for reason, pattern in STRING_SECRETS:
            value, count = pattern.subn(REDACTED, value)
            if count:
                redactions.append({"path": path, "reason": reason})
        return value
    return value


def project_cases(source: dict[str, Any]) -> dict[str, Any]:
    """Preserve the exact original questions and all existing source-case metadata."""
    cases = []
    for result in source["cases"]:
        cases.append({
            "case_id": result["case_id"],
            "question": result["question"],
            "source_case": result.get("source_case", {}),
        })
    return {
        "reference": "2026-09-02 CC first-pass random 20; historical labels, not newly adjudicated gold",
        "source": SOURCE,
        "case_count": len(cases),
        "cases": cases,
    }


def evidence(root: Path, source: dict[str, Any]) -> dict[str, Any]:
    comparison_path = root / COMPARISON
    comparison = json.loads(comparison_path.read_text()) if comparison_path.exists() else {}
    session_ids = {case["financial_qa"]["session_id"] for case in source["cases"]}
    event_path = root / EVENTS
    matched_events = []
    if event_path.exists():
        for line in event_path.read_text().splitlines():
            event = json.loads(line)
            if event.get("session_id") in session_ids:
                matched_events.append(event)
    native_evidence = []
    # Only the selected run's known session directories are searched, not ~/.claude.
    for session_id in sorted(session_ids):
        directory = root / "data/financial_qa_cc_sessions/_client_sessions" / session_id
        if not directory.exists():
            continue
        for path in sorted(directory.rglob(session_id + ".jsonl")):
            events = [json.loads(line) for line in path.read_text().splitlines()]
            native_evidence.append({
                "path": str(path.relative_to(root)), "sha256": sha256(path.read_bytes()),
                "versions": sorted({event["version"] for event in events if event.get("version")}),
                "models": sorted({event["message"]["model"] for event in events if isinstance(event.get("message"), dict) and event["message"].get("model")}),
            })
            break
        if native_evidence:
            break
    return {
        "comparison_source": COMPARISON,
        "comparison_source_sha256": sha256(comparison_path.read_bytes()) if comparison_path.exists() else None,
        "cc_configuration_description": comparison.get("manifest", {}).get("configurations", {}).get("cc"),
        "models_observed": sorted({case["done_result"]["model_name"] for case in source["cases"]}),
        "research_modes_observed": sorted({case["done_result"]["research_mode"]["requested"] for case in source["cases"]}),
        "completed_cases": sum(case.get("status") == "ok" for case in source["cases"]),
        "nonempty_final_answers": sum(bool(case.get("done_result", {}).get("message")) for case in source["cases"]),
        "selection": source.get("selection", {}),
        "runtime_events_source": EVENTS,
        "runtime_events_source_sha256": sha256(event_path.read_bytes()) if event_path.exists() else None,
        "matched_event_count": len(matched_events),
        "catalog_revisions_observed": sorted({event["finance_catalog_revision"] for event in matched_events if event.get("finance_catalog_revision")}),
        "turn_timeout_seconds_observed": sorted({event["turn_timeout_seconds"] for event in matched_events if event.get("turn_timeout_seconds")}),
        "native_evidence": native_evidence,
        "missing": [
            "Provider and effective upstream endpoint were not captured; historical code/template says deepseek, not a bound runtime observation.",
            "Effective max_turns was not captured; contemporaneous template says 12, not a native observation.",
            "Python claude-agent-sdk package version was not saved with this run; native CLI version is separately evidenced.",
            "No complete source/config content manifest was attached to the historical run; reconstruction is not an exact verified checkout.",
        ],
    }


def build_history(root: Path, output: Path | None = None) -> dict[str, Any]:
    raw = (root / SOURCE).read_bytes()
    original = json.loads(raw)
    if tuple(case["case_id"] for case in original.get("cases", [])) != EXPECTED_IDS:
        raise ValueError("Historical case identities/order do not match the selected 2026-09-02 run")
    redactions: list[dict[str, str]] = []
    results = sanitize(original, redactions=redactions)
    audit = {
        "source": SOURCE, "source_sha256": sha256(raw), "source_bytes": len(raw),
        "case_count": len(results["cases"]), "redactions": redactions,
    }
    if output is None:
        return audit
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("Refusing to replace an existing history directory")
    observations = evidence(root, original)
    safe_observations = sanitize(observations, path="$evidence", redactions=redactions)
    output.mkdir(parents=True, exist_ok=True)
    files = {"results.json": results, "cases.json": project_cases(results)}
    for name, value in files.items():
        encoded = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
        (output / name).write_bytes(encoded)
        audit[name + "_sha256"] = sha256(encoded)
    audit["observations"] = safe_observations
    (output / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    native_versions = sorted({version for native in observations["native_evidence"] for version in native["versions"]})
    readme = f"""# 2026-09-02 CC 历史评测证据（只读封存）

这里是已有结果的脱敏封存，不是一次新评测，也不是已证明逐字复原的旧代码版本。生成过程不访问模型、数据库或网络。

## 文件与完整性

- `cases.json`：20 个原题，顺序和文本不变；保留当时逐题 source_case 元数据及推荐入口。标签是历史判断，未重新标注为正确答案。
- `results.json`：完整原始 cc_results.json JSON 树；只脱敏敏感凭据、headers/cookies，以及字符串中的明确凭据形态。问题、最终答案、选择、数据、usage、时间、SSE 与 trace 均保留；原始值仅在审计列出的路径发生变化。
- `audit.json`：原文件/产物 SHA256、脱敏路径及独立历史证据。只记录路径与原因，不记录被移除的值。

原始来源：`{SOURCE}`，原字节数 {len(raw):,}。

```text
original SHA256: {audit['source_sha256']}
results.json SHA256: {audit['results.json_sha256']}
cases.json SHA256: {audit['cases.json_sha256']}
```

本次发现并处理的敏感位置记录数：{len(redactions)}。零记录表示规则未命中，不声称自动扫描能识别所有未知形式的秘密。审计测试另外逐题核对问题、答案、工具选择、usage 和计时未丢失。

## 运行设置的证据等级

| 项目 | 历史事实及边界 |
|---|---|
| 实际模型 | {', '.join(observations['models_observed'])}；来自20题响应，另有native消息佐证 |
| 回答模式 | 20题 research_mode=auto，均有最终自然语言答案；完整问答，不是9月8日data-only/fast实验 |
| 推理 | comparison_analysis.json 明确 CC effort=low、ClaudeAgentOptions 未显式设置 max_tokens；不是逐请求 native effort 记录 |
| 最大轮次 | 同期配置模板为12，但此次结果未保存有效max_turns；不能伪称运行时已核实 |
| Provider/endpoint | 同期代码/模板为deepseek；该次provider_transport为空，实际endpoint缺少运行绑定；不可默认等同当前Bailian路由 |
| SDK | native Claude CLI：{', '.join(native_versions) or '未取得'}；Python claude-agent-sdk包版本未随该次保存 |
| 评测方式 | scripts/eval_finance_query_api_batch.py，真实Chat/SSE；HTTP http://127.0.0.1:22120；并发3、每题超时600秒 |
| CC内部超时 | 选中会话旁路记录为{observations['turn_timeout_seconds_observed']}秒，普通/长题各自预算；与HTTP超时不是同一指标 |
| 会话隔离 | 同一guest身份，每题新HTTP会话和thread；本批20题没有多轮场景 |
| 目录revision | {', '.join(observations['catalog_revisions_observed']) or '未记录'} |
| 源码绑定 | 没有本次全部src/config内容哈希；重建版本不能被描述为完整精确历史checkout |

原始结果本身记录的模型、研究模式和日期等保存在results.json；补充证据的来源路径、SHA及可观察值在audit.json。

## 不能从“20完成”推导“20正确”

20/20请求正常结束并有自然语言最终回答，不等于20/20取得了题目全部所需数据，也不等于答案正确。原标签仍有已知局限：RTE053没有结构化取数；RTE033护城河走基础资料/财务表；RTE082查研报但未严格限制评级调整。完整保留这些现场，不挑选或改写成功样本。

母集为67道大陆可支持题，排除3道含news需求后，从64题以种子20260902选20；不是行情/基金/债券的全场景基准。source_metadata部分旧分类统计仍合计100，已原样保留而未悄悄改写；实际题数与入口应从逐题source_case读取。历史推荐入口不是强制唯一API，明细/聚合等价需要按题义和数据完整性复核。

## 复现封存（无模型调用）

```sh
.venv/bin/python scripts/freeze_cc_reference_history.py --scan-only
.venv/bin/python scripts/freeze_cc_reference_history.py --output-dir /absolute/new/history
.venv/bin/python -m pytest tests/test_cc_reference_history.py -q
```

生成器拒绝覆盖已有非空目录。不要复制旧个人env、cookies或Claude配置；未来运行必须由外部安全注入凭据，并与这些静态历史证据分开存放。
"""
    (output / "README.md").write_text(readme)
    return audit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path, default=Path("baselines/cc_20260902_rebuilt/history"))
    parser.add_argument("--scan-only", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output_dir if args.output_dir.is_absolute() else root / args.output_dir
    audit = build_history(root, None if args.scan_only else output)
    print(json.dumps({key: audit[key] for key in ("source_sha256", "source_bytes", "case_count", "redactions")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
