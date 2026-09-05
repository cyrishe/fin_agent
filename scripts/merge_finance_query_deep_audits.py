#!/usr/bin/env python3
"""Merge human deep-audit chunks into the generated benchmark analysis JSON."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = ROOT / "outputs" / "d4f10504-8df6-435e-9316-3d89b5fd1015"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json_text(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, str):
        return value.strip()
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _normalize_audit(item: Mapping[str, Any], *, source: str) -> dict[str, Any]:
    checks = item.get("checks") if isinstance(item.get("checks"), Mapping) else {}
    key_issue = _text(item.get("key_issue"))
    if not key_issue:
        raw_issues = item.get("key_issues")
        if isinstance(raw_issues, list):
            key_issue = "；".join(_text(value) for value in raw_issues if _text(value))
        else:
            key_issue = _text(raw_issues)
    root_cause = _text(item.get("primary_root_cause"))
    time_note = _json_text(item.get("time") or checks.get("time"))
    entity_note = _json_text(item.get("entity") or checks.get("entity"))
    numeric_note = _json_text(item.get("numeric") or checks.get("numeric"))
    aggregation_note = _json_text(item.get("aggregation") or checks.get("aggregation"))
    evidence_note = _json_text(
        item.get("evidence_alignment") or checks.get("evidence_alignment")
    )
    evidence = {
        "review_source": source,
        "route_ok": item.get("route_ok"),
        "time": item.get("time") or checks.get("time"),
        "entity": item.get("entity") or checks.get("entity"),
        "numeric": item.get("numeric") or checks.get("numeric"),
        "aggregation": item.get("aggregation") or checks.get("aggregation"),
        "evidence_alignment": item.get("evidence_alignment")
        or checks.get("evidence_alignment"),
    }
    rationale_parts = [
        key_issue,
        f"主要根因：{root_cause}" if root_cause else "",
        f"聚合：{aggregation_note}" if aggregation_note else "",
        f"证据对齐：{evidence_note}" if evidence_note else "",
    ]
    return {
        "case_id": _text(item.get("case_id")),
        "verdict": _text(item.get("verdict")) or "unreviewed",
        "route_ok": item.get("route_ok"),
        "rationale": "\n".join(part for part in rationale_parts if part),
        "evidence": evidence,
        "freshness": time_note,
        "schema_compliance": _text(item.get("expected_dataview"))
        or _json_text(item.get("expected_dataviews")),
        "numeric_consistency": numeric_note,
        "entity_date_correctness": "；".join(
            part for part in (entity_note, time_note) if part
        ),
        "hallucination_risk": evidence_note,
        "key_issue": key_issue,
        "primary_root_cause": root_cause,
        "review_source": source,
    }


def _curated_root_causes(
    analysis: Mapping[str, Any], audits: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Return cross-case design findings backed by the deep-audit evidence.

    This intentionally describes a few stable responsibilities instead of
    turning individual benchmark failures into product branches.
    """

    def existing(*case_ids: str) -> list[str]:
        return [case_id for case_id in case_ids if case_id in audits]

    def route_failures() -> list[str]:
        return sorted(
            case_id
            for case_id, audit in audits.items()
            if audit.get("route_ok") is False
        )

    route_ids = route_failures()
    completeness_ids = existing(
        "BUS036", "BUS039", "BUS047", "BUS057", "BUS066", "BUS076",
        "BUS078", "BUS080", "BUS093", "BUS101", "BUS102", "BUS111",
        "BUS115", "BUS122", "BUS149", "BUS151", "BUS157", "BUS162",
        "BUS165", "BUS166", "BUS168", "BUS170", "BUS173", "BUS178",
        "BUS186", "BUS188", "BUS193", "BUS202", "BUS219", "BUS226",
    )
    latest_join_ids = existing(
        "BUS063", "BUS070", "BUS074", "BUS101", "BUS102", "BUS104",
        "BUS106", "BUS120", "BUS213", "BUS216", "BUS217", "BUS221",
        "BUS224", "BUS225", "BUS226", "BUS227", "BUS228", "BUS229",
    )
    date_ids = existing(
        "BUS034", "BUS035", "BUS069", "BUS072", "BUS074", "BUS082",
        "BUS087", "BUS089", "BUS090", "BUS103", "BUS106", "BUS135",
        "BUS139", "BUS141", "BUS142", "BUS144", "BUS147", "BUS159",
        "BUS182", "BUS200", "BUS213", "BUS224",
    )
    filter_ids = existing(
        "BUS062", "BUS064", "BUS067", "BUS080", "BUS108", "BUS109",
        "BUS110", "BUS112", "BUS114", "BUS120", "BUS203", "BUS204",
        "BUS213", "BUS214", "BUS218", "BUS220", "BUS227", "BUS229",
    )
    renderer_ids = existing(
        "BUS003", "BUS050", "BUS051", "BUS058", "BUS061", "BUS064",
        "BUS068", "BUS071", "BUS081", "BUS092", "BUS097", "BUS104",
        "BUS113", "BUS114", "BUS133", "BUS134", "BUS140", "BUS161",
        "BUS164", "BUS168", "BUS186", "BUS191", "BUS192", "BUS193",
        "BUS194", "BUS195", "BUS197", "BUS199", "BUS201", "BUS227",
        "BUS230",
    )
    fixed_longtail_ids = existing(
        "BUS012", "BUS039", "BUS047", "BUS069", "BUS160", "BUS194"
    )

    def record(
        dimension: str,
        cause: str,
        case_ids: list[str],
        evidence: str,
        recommendation: str,
        severity: str,
    ) -> dict[str, Any]:
        return {
            "dimension": dimension,
            "root_cause": cause,
            "affected_count": len(case_ids),
            "case_ids": case_ids,
            "evidence": evidence,
            "severity": severity,
            "recommendation": recommendation,
        }

    return [
        record(
            "系统路由",
            "明确的金融数据意图仍可能在进入金融查询前被通用澄清或默认助手截断",
            route_ids,
            f"人工审计确认 {len(route_ids)} 条 route_ok=false；其中包含股票筛选、行业聚合、债券发行人和 hot_event 查询。快速返回不代表主线有效。",
            "先补齐顶层业务责任描述与入口契约；路由只决定是否进入金融查询，不替下游猜业务细节。",
            "P0",
        ),
        record(
            "HARD 结果协议",
            "返回样本、全集规模与下游可用完整性没有成为稳定事实",
            completeness_ids,
            "limit=50/100、limit=-1仍封顶、以及2000字符答案上限多次被当成全量；上游截断继续污染计数、TopN、均值和跨表筛选。",
            "统一 returned_count、total_count/unknown、truncated、sample_complete，并让不完整性随 result_ref 进入下一步。",
            "P0",
        ),
        record(
            "工具/数据流",
            "缺少 groupwise-latest、稳定 Join 与当前成员快照语义",
            latest_join_ids,
            "股东、行业成分、热点 state/member 等视图需要每实体最新一条或当前批次；模型只能在截断历史样本上临时聚合，轮次增加后结论仍不可证。",
            "把每实体最新、当前快照、成员集合一致性做成通用查询能力或可组合执行原语，而不是继续加提示词。",
            "P0",
        ),
        record(
            "工具契约/Harness",
            "过滤、中文枚举和代码规范化失败方式不一致，部分无效过滤会静默返回未过滤数据",
            filter_ids,
            "LIKE、债券名称/代码范围、热点日期偏移和带市场后缀代码在多条 Case 中出现空查或静默失效；BUS203/204因此产生长循环。",
            "目录一次说明可用语法和枚举；执行层必须返回实际应用的 filter/selection 及未支持项，不能把请求参数回显当成已生效证据。",
            "P0",
        ),
        record(
            "时间协议",
            "全局交易日与各 dataview 最新可用日期、窗口结束日和日内最后快照没有统一解析",
            date_ids,
            "同一交易日下行情、融资、估值、热点等数据新鲜度不同；出现空查、陈旧快照当当前值，以及窗口 end_date 被补写。",
            "先由交易日模块解析用户日期，再由目标 dataview 返回真实 as_of/end_date；非交易日回退和数据滞后都显式展示。",
            "P1",
        ),
        record(
            "Renderer",
            "最终表达仍会越过证据边界，且字段单位、行对齐和完整列表交付不稳定",
            renderer_ids,
            "逐案复核发现比例10/100倍误读、元/亿元换算错误、零行推导生命周期、有限字段推导因果/龙头，以及完整结果漏行。",
            "Renderer消费字段级单位和证据边界；大结果转表格/附件；原因解释若非工具证据则降级为假设并明确标记。",
            "P0",
        ),
        record(
            "性能/可观测性",
            "少数长尾由数据查询或模型等待主导，并非减少 loop 就能解决",
            fixed_longtail_ids,
            "BUS012 单次全市场实时排序查询约93.5秒；BUS194仅2次 finance_query仍耗时97.8秒。现有查询阶段是SSE估算，缺少逐工具服务端精确span。",
            "增加目录、模型决策、SQL/API、结果装载、Renderer 的服务端 span；分别治理慢查询和推理等待，不用工具次数代理全部耗时。",
            "P1",
        ),
        record(
            "Harness效率",
            "能力发现仍依赖多层目录和试探，但一次有针对性的恢复通常是合理的",
            existing("BUS026", "BUS067", "BUS091", "BUS108", "BUS110", "BUS112", "BUS121", "BUS155", "BUS203", "BUS204", "BUS213", "BUS225", "BUS227"),
            f"全量 {analysis.get('summary', {}).get('operation_count', 0)} 次操作中，设计浪费率为 {analysis.get('summary', {}).get('design_waste_rate', 0):.2%}；主要是终态零结果后继续重查、重复装载和无效过滤恢复，而非所有loop。",
            "保留一次有解释的纠错；目录先给全局五类规则与目标dataview完整协议，失败反馈直接指出不可用字段/语法和可恢复方向。",
            "P1",
        ),
    ]
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis", default=str(DEFAULT_DIR / "analysis.json"))
    parser.add_argument("--audit-dir", default=str(DEFAULT_DIR))
    args = parser.parse_args()

    analysis_path = Path(args.analysis).expanduser().resolve()
    audit_dir = Path(args.audit_dir).expanduser().resolve()
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    audit_paths = sorted(audit_dir.glob("deep_audit_*.json"))
    if not audit_paths:
        raise FileNotFoundError(f"no deep_audit_*.json files under {audit_dir}")

    by_case: dict[str, dict[str, Any]] = {}
    duplicates: list[str] = []
    for path in audit_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for item in payload.get("cases") or []:
            if not isinstance(item, Mapping):
                continue
            audit = _normalize_audit(item, source=path.name)
            case_id = audit["case_id"]
            if not case_id:
                continue
            if case_id in by_case:
                duplicates.append(case_id)
            by_case[case_id] = audit

    case_rows = analysis.get("cases") if isinstance(analysis.get("cases"), list) else []
    known_ids = {
        _text(item.get("case_id"))
        for item in case_rows
        if isinstance(item, Mapping) and _text(item.get("case_id"))
    }
    unknown_audits = sorted(set(by_case) - known_ids)
    ignored_missing_source_ids = [
        case_id
        for case_id in unknown_audits
        if by_case[case_id].get("verdict") == "unreviewed"
        and by_case[case_id].get("primary_root_cause") == "source_case_missing"
    ]
    unexpected_unknown_audits = sorted(set(unknown_audits) - set(ignored_missing_source_ids))
    if unexpected_unknown_audits:
        raise ValueError(
            f"deep audits reference unknown cases: {unexpected_unknown_audits}"
        )
    for case_id in ignored_missing_source_ids:
        del by_case[case_id]

    for item in case_rows:
        if not isinstance(item, dict):
            continue
        audit = by_case.get(_text(item.get("case_id")))
        if not audit:
            continue
        item["manual_verdict"] = audit["verdict"]
        item["manual_route_ok"] = audit["route_ok"]
        item["manual_key_issue"] = audit["key_issue"]
        item["manual_primary_root_cause"] = audit["primary_root_cause"]
        item["answer_audit"] = audit

    verdict_counts = Counter(audit["verdict"] for audit in by_case.values())
    route_known = [
        audit["route_ok"]
        for audit in by_case.values()
        if isinstance(audit.get("route_ok"), bool)
    ]
    analysis["answer_audits"] = [by_case[key] for key in sorted(by_case)]
    analysis["root_causes"] = _curated_root_causes(analysis, by_case)
    analysis["manual_audit_summary"] = {
        "audit_files": [str(path) for path in audit_paths],
        "reviewed_case_count": len(by_case),
        "missing_review_case_ids": sorted(known_ids - set(by_case)),
        "verdict_counts": dict(verdict_counts),
        "route_ok_count": sum(route_known),
        "route_failed_count": len(route_known) - sum(route_known),
        "route_unknown_count": len(by_case) - len(route_known),
        "duplicate_case_ids": sorted(set(duplicates)),
        "ignored_source_missing_case_ids": ignored_missing_source_ids,
    }
    analysis.setdefault("observability_contract", {}).setdefault("measured", []).append(
        "manual semantic audit verdicts merged from four deep_audit JSON chunks"
    )
    _atomic_write(analysis_path, analysis)
    print(
        json.dumps(
            {
                "analysis": str(analysis_path),
                "audit_files": len(audit_paths),
                "reviewed_cases": len(by_case),
                "verdict_counts": dict(verdict_counts),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
