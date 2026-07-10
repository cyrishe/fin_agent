from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.services.code_work_item_runner import CodeWorkItemRunner
from src.services.file_artifact_service import FileArtifactService
from src.services.python_execution_runtime import PythonExecutionRuntime
from src.services.quant_data_provider_service import QuantDataProviderError, QuantDataProviderService


class QuantFactorScreeningError(Exception):
    def __init__(self, failure_kind: str, message: str, data: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.failure_kind = failure_kind
        self.message = message
        self.data = data or {}


class QuantFactorScreeningService:
    TOOL_NAME = "quant_factor_screening"

    def __init__(
        self,
        *,
        data_root: str | Path = "data",
        provider_service: Optional[QuantDataProviderService] = None,
        code_runner: Optional[CodeWorkItemRunner] = None,
        file_artifact_service: Optional[FileArtifactService] = None,
    ) -> None:
        self.file_artifact_service = file_artifact_service or FileArtifactService(data_root=data_root)
        self.provider_service = provider_service or QuantDataProviderService(
            data_root=data_root,
            file_artifact_service=self.file_artifact_service,
        )
        self.code_runner = code_runner or CodeWorkItemRunner(
            python_runtime=PythonExecutionRuntime(),
            file_artifact_service=self.file_artifact_service,
        )

    def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(payload or {})
        user_text = self._trim(payload.get("user_text") or payload.get("query") or payload.get("objective"))
        factor_plan = self._normalize_factor_plan(payload.get("factor_plan") if isinstance(payload.get("factor_plan"), dict) else {}, payload, user_text)
        plan_hash = self._hash_json(factor_plan)
        try:
            prepared = self.provider_service.prepare(
                {
                    "universe": factor_plan.get("universe"),
                    "date_range": factor_plan.get("date_range"),
                    "report_period": factor_plan.get("report_period"),
                    "required_data": factor_plan.get("required_data"),
                    "max_preview_rows": factor_plan.get("max_preview_rows") or 20,
                }
            )
        except QuantDataProviderError as exc:
            raise QuantFactorScreeningError(
                exc.failure_kind,
                exc.message,
                self._empty_result_data(factor_plan=factor_plan, plan_hash=plan_hash, prepared={}),
            ) from exc
        data_artifacts = [
            item
            for item in prepared.get("artifacts", [])
            if isinstance(item, dict) and self._trim(item.get("source_id")) != "universe" and self._trim(item.get("artifact_ref"))
        ]
        if not data_artifacts:
            raise QuantFactorScreeningError(
                "data_unavailable",
                "no verified provider data artifact is available for factor screening",
                self._empty_result_data(factor_plan=factor_plan, plan_hash=plan_hash, prepared=prepared),
            )

        code = self._screening_code()
        code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
        step = {
            "step_id": "quant_factor_screening_code",
            "type": "code",
            "name": "analysis_python",
            "runtime_profile": {
                "backend": "local_dev",
                "limits": {"timeout_ms": 5000, "output_json_bytes": 1000000, "artifact_total_bytes": 5 * 1024 * 1024},
            },
            "code_task_spec": {"task_kind": "quant_factor_screening", "solution_mode": "generated_inline", "code": code},
            "max_attempts": 1,
        }
        resolved_inputs = {str(item["source_id"]): item["artifact_ref"] for item in data_artifacts}
        code_run = self.code_runner.run(step=step, resolved_inputs=resolved_inputs)
        if code_run.get("status") != "completed":
            raise QuantFactorScreeningError(
                self._trim(code_run.get("failure_kind")) or "code_runtime_failed",
                self._trim(code_run.get("error")) or "code runtime failed",
                self._empty_result_data(
                    factor_plan=factor_plan,
                    plan_hash=plan_hash,
                    prepared=prepared,
                    code_hash=code_hash,
                    code_run=code_run,
                ),
            )
        structured = ((code_run.get("result") or {}).get("data") or {}).get("structured_data")
        if not isinstance(structured, dict):
            structured = {}
        factor_rows = [dict(row) for row in structured.get("factor_rows", []) if isinstance(row, dict)]
        top_n = self._bounded_int((factor_plan.get("ranking") or {}).get("top_n"), default=20, min_value=1, max_value=100)
        selected = factor_rows[:top_n]
        factor_table = self._write_factor_table(factor_rows)
        return {
            "selected_stocks": selected,
            "factor_table": factor_table,
            "factor_definitions": factor_plan.get("factors") or [],
            "data_coverage": self._coverage(prepared=prepared, selected_count=len(selected), eligible_count=len(factor_rows)),
            "risk_warnings": self._risk_warnings(prepared=prepared, factor_rows=factor_rows),
            "render_blocks": self._render_blocks(selected=selected, factor_table=factor_table, prepared=prepared),
            "audit": {
                "factor_plan_hash": f"sha256:{plan_hash}",
                "code_hash": f"sha256:{code_hash}",
                "data_artifacts": data_artifacts,
                "code_diagnostics": (code_run.get("diagnostics") or {}),
                "runtime_status": code_run.get("status"),
            },
            "factor_plan": factor_plan,
        }

    def _normalize_factor_plan(self, plan: Dict[str, Any], payload: Dict[str, Any], user_text: str) -> Dict[str, Any]:
        normalized = dict(plan or {})
        normalized["universe"] = normalized.get("universe") or payload.get("universe") or payload.get("stocks") or []
        normalized["date_range"] = normalized.get("date_range") or payload.get("date_range") or {}
        normalized["report_period"] = normalized.get("report_period") or payload.get("report_period") or {}
        normalized["required_data"] = self._required_data(normalized.get("required_data") or payload.get("required_data"), user_text)
        normalized["factors"] = self._factor_definitions(normalized.get("factors"), user_text)
        ranking = normalized.get("ranking") if isinstance(normalized.get("ranking"), dict) else {}
        ranking["method"] = self._trim(ranking.get("method")) or "weighted_score"
        ranking["top_n"] = self._bounded_int(ranking.get("top_n") or payload.get("top_n"), default=20, min_value=1, max_value=100)
        normalized["ranking"] = ranking
        normalized["max_preview_rows"] = self._bounded_int(normalized.get("max_preview_rows") or payload.get("max_preview_rows"), default=20, min_value=1, max_value=100)
        return normalized

    def _required_data(self, raw: Any, user_text: str) -> List[str]:
        if isinstance(raw, str):
            values = [raw]
        elif isinstance(raw, list):
            values = [str(item) for item in raw]
        else:
            values = []
        text = user_text or ""
        keyword_map = [
            ("daily_price", ("涨幅", "走势", "量价", "成交额", "成交量", "日k", "日K", "价格")),
            ("minute_price", ("分钟", "分时")),
            ("moneyflow", ("资金", "流入", "主力")),
            ("valuation", ("估值", "市盈率", "pe", "PB", "pb")),
            ("financial_indicator", ("财务", "利润", "ROE", "roe")),
            ("cashflow", ("现金流",)),
            ("concept_signal", ("概念", "板块", "热点", "主题")),
            ("news", ("消息", "新闻", "舆情", "催化")),
        ]
        for source_id, keywords in keyword_map:
            if any(keyword in text for keyword in keywords) and source_id not in values:
                values.append(source_id)
        if not values:
            values.append("daily_price")
        return values

    def _factor_definitions(self, raw: Any, user_text: str) -> List[Dict[str, Any]]:
        if isinstance(raw, list) and raw:
            return [dict(item) for item in raw if isinstance(item, dict)]
        factors = [
            {
                "name": "price_momentum",
                "family": "price_volume",
                "definition": "区间收盘价涨幅，基于 provider 提供的日线或价格数据计算。",
                "direction": "higher_better",
                "weight": 0.45,
            },
            {
                "name": "liquidity_activity",
                "family": "price_volume",
                "definition": "区间成交额/成交量活跃度，作为量能确认信号。",
                "direction": "higher_better",
                "weight": 0.25,
            },
        ]
        if any(keyword in user_text for keyword in ("资金", "流入", "主力")):
            factors.append({"name": "moneyflow_signal", "family": "capital_flow", "definition": "资金流入强度。", "direction": "higher_better", "weight": 0.2})
        if any(keyword in user_text for keyword in ("消息", "新闻", "概念", "热点", "催化")):
            factors.append({"name": "catalyst_signal", "family": "news_concept", "definition": "消息面或概念热度信号。", "direction": "higher_better", "weight": 0.1})
        return factors

    def _write_factor_table(self, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        columns = self._columns_from_rows(rows)
        manifest = self.file_artifact_service.write_table_artifact(
            columns=columns,
            rows=rows,
            preview_rows=rows[:20],
            source_file={
                "source_type": "quant_factor_screening",
                "source_id": "factor_table",
                "file_name": "factor_table.jsonl",
                "mime_type": FileArtifactService.TABLE_CONTENT_TYPE,
                "size_bytes": 0,
                "sha256": "",
            },
            created_by_tool=self.TOOL_NAME,
            table_id="factor_table",
        )
        return {
            "columns": [column["name"] for column in columns],
            "rows": rows[:20],
            "artifact_ref": manifest["artifact_ref"],
            "row_count": manifest.get("row_count", len(rows)),
            "physical_format": manifest.get("physical_format"),
        }

    def _empty_result_data(
        self,
        *,
        factor_plan: Dict[str, Any],
        plan_hash: str,
        prepared: Dict[str, Any],
        code_hash: str = "",
        code_run: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return {
            "selected_stocks": [],
            "factor_table": {"columns": [], "rows": [], "artifact_ref": "", "row_count": 0},
            "factor_definitions": factor_plan.get("factors") or [],
            "data_coverage": self._coverage(prepared=prepared, selected_count=0, eligible_count=0),
            "risk_warnings": self._risk_warnings(prepared=prepared, factor_rows=[]),
            "render_blocks": self._render_blocks(selected=[], factor_table={}, prepared=prepared),
            "audit": {
                "factor_plan_hash": f"sha256:{plan_hash}",
                "code_hash": f"sha256:{code_hash}" if code_hash else "",
                "data_artifacts": [item for item in prepared.get("artifacts", []) if isinstance(item, dict)],
                "code_diagnostics": (code_run or {}).get("diagnostics") or {},
                "runtime_status": (code_run or {}).get("status") or "not_started",
            },
            "factor_plan": factor_plan,
        }

    def _coverage(self, *, prepared: Dict[str, Any], selected_count: int, eligible_count: int) -> Dict[str, Any]:
        provider_coverage = prepared.get("data_coverage") if isinstance(prepared.get("data_coverage"), dict) else {}
        universe = provider_coverage.get("universe") if isinstance(provider_coverage.get("universe"), dict) else {}
        return {
            "universe_count": int(universe.get("normalized_count") or 0),
            "eligible_count": int(eligible_count),
            "selected_count": int(selected_count),
            "missing_data_summary": provider_coverage.get("gaps") or [],
            "provider_coverage": provider_coverage,
            "source_coverage": prepared.get("source_coverage") if isinstance(prepared.get("source_coverage"), dict) else {},
        }

    def _risk_warnings(self, *, prepared: Dict[str, Any], factor_rows: List[Dict[str, Any]]) -> List[str]:
        warnings = ["本结果仅为数据筛选和候选排序，不构成收益承诺或交易建议。"]
        gaps = ((prepared.get("data_coverage") or {}).get("gaps") if isinstance(prepared.get("data_coverage"), dict) else []) or []
        if gaps:
            warnings.append("部分数据源不可用，排序结果仅覆盖已 materialize 的数据。")
        if not factor_rows:
            warnings.append("没有可计算的候选股票，需补充股票池或 verified provider 数据。")
        return warnings

    def _render_blocks(self, *, selected: List[Dict[str, Any]], factor_table: Dict[str, Any], prepared: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            {
                "type": "table",
                "title": "量化筛选候选股",
                "columns": factor_table.get("columns") or ["stk_code", "score"],
                "rows": selected[:20],
                "artifact_ref": factor_table.get("artifact_ref", ""),
            },
            {
                "type": "structured_text",
                "title": "数据覆盖与风险",
                "data": {
                    "coverage": prepared.get("data_coverage") or {},
                    "note": "仅展示 bounded preview，完整因子表通过 artifact_ref 传递。",
                },
            },
        ]

    def _columns_from_rows(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        names: List[str] = []
        for row in rows:
            for key in row.keys():
                if key not in names:
                    names.append(key)
        return [{"name": name, "type": self._infer_type([row.get(name) for row in rows]), "index": index} for index, name in enumerate(names)]

    def _infer_type(self, values: List[Any]) -> str:
        for value in values:
            if value is None:
                continue
            if isinstance(value, bool):
                return "boolean"
            if isinstance(value, int):
                return "integer"
            if isinstance(value, float):
                return "number"
            return "string"
        return "string"

    @staticmethod
    def _screening_code() -> str:
        return r'''
import json
import os
from collections import defaultdict

def load_rows(payload, source_id):
    artifact = payload.get("artifact_inputs", {}).get(source_id)
    if not artifact:
        return []
    path = os.path.join(os.environ["CODE_INPUT_DIR"], artifact["data_path"])
    rows = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows

def code_of(row):
    return str(row.get("stk_code") or row.get("stock_code") or row.get("code") or "").strip()

with open(os.environ["CODE_INPUT_JSON"], "r", encoding="utf-8") as f:
    payload = json.load(f)

all_rows = []
for source_id in ["daily_price", "minute_price", "moneyflow", "valuation", "financial_indicator", "cashflow", "concept_signal", "news"]:
    for row in load_rows(payload, source_id):
        item = dict(row)
        item["_source_id"] = source_id
        all_rows.append(item)

grouped = defaultdict(list)
for row in all_rows:
    code = code_of(row)
    if code:
        grouped[code].append(row)

factor_rows = []
for code, rows in grouped.items():
    price_rows = [row for row in rows if row.get("_source_id") in {"daily_price", "minute_price"}]
    price_rows.sort(key=lambda row: str(row.get("trade_date") or row.get("date") or row.get("minute_index") or ""))
    first_close = next((float(row.get("close")) for row in price_rows if row.get("close") not in (None, "")), 0.0)
    last_close = next((float(row.get("close")) for row in reversed(price_rows) if row.get("close") not in (None, "")), first_close)
    momentum = ((last_close - first_close) / first_close) if first_close else 0.0
    amounts = [float(row.get("amount") or row.get("turnover") or row.get("volume") or 0) for row in price_rows]
    liquidity = (sum(amounts) / len(amounts)) if amounts else 0.0
    moneyflow = sum(float(row.get("net_inflow") or row.get("main_net_inflow") or row.get("value") or 0) for row in rows if row.get("_source_id") == "moneyflow")
    catalyst = len([row for row in rows if row.get("_source_id") in {"news", "concept_signal"}])
    score = momentum * 100.0 + (liquidity / 100000000.0) + (moneyflow / 100000000.0) + catalyst
    factor_rows.append({
        "stk_code": code,
        "score": round(score, 6),
        "price_momentum": round(momentum, 6),
        "liquidity_activity": round(liquidity, 6),
        "moneyflow_signal": round(moneyflow, 6),
        "catalyst_signal": catalyst,
        "source_row_count": len(rows),
    })

factor_rows.sort(key=lambda row: row.get("score", 0), reverse=True)
out = {
    "tool": "analysis_python",
    "ok": True,
    "data": {"structured_data": {"factor_rows": factor_rows}, "render_blocks": []},
    "error": "",
}
with open(os.path.join(os.environ["CODE_OUTPUT_DIR"], "output.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False)
'''

    @staticmethod
    def _hash_json(payload: Dict[str, Any]) -> str:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _bounded_int(value: Any, *, default: int, min_value: int, max_value: int) -> int:
        try:
            parsed = int(value)
        except Exception:
            parsed = default
        return min(max(parsed, min_value), max_value)

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()
