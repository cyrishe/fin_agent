from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from src.experiments.staged_data_protocol.phase2.trade_date_resolver import (
    TradeDateResolver,
)
from src.services.finance_data_tool_runtime_service import FinanceDataToolRuntimeService


class FinanceUniverseInputResolverService:
    """Resolve a named financial universe through the formal finance API."""

    SUBJECTS = {
        "plate": {
            "subject": "plate",
            "identity_code": "plate_code",
            "identity_name": "plate_name",
            "member_code": "plate_code",
            "member_name": "plate_name",
        },
        "industry": {
            "subject": "industry",
            "identity_code": "industry_code",
            "identity_name": "industry_name",
            "member_code": "industry_code",
            "member_name": "industry_name",
        },
        "index": {
            "subject": "index",
            "identity_code": "code",
            "identity_name": "name",
            "member_code": "index_code",
            "member_name": "index_name",
        },
    }

    def __init__(
        self,
        *,
        runtime: Optional[FinanceDataToolRuntimeService] = None,
    ) -> None:
        self.runtime = runtime or FinanceDataToolRuntimeService(
            trade_date_resolver=TradeDateResolver()
        )

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _literal(value: Any) -> str:
        return str(value or "").replace("\\", "\\\\").replace('"', '\\"').strip()

    def resolve(self, source: Mapping[str, Any]) -> Dict[str, Any]:
        subject_type = self._trim(source.get("subject_type")).lower()
        query = self._trim(source.get("query") or source.get("name"))
        if subject_type not in self.SUBJECTS:
            return {
                "status": "needs_input",
                "message": "请说明股票池属于板块、行业还是指数。",
                "items": [],
            }
        if not query:
            return {
                "status": "needs_input",
                "message": "请补充要查询的板块、行业或指数名称。",
                "items": [],
            }

        subject_config = self.SUBJECTS[subject_type]
        subject = subject_config["subject"]
        identity_code_field = subject_config["identity_code"]
        identity_name_field = subject_config["identity_name"]
        member_code_field = subject_config["member_code"]
        member_name_field = subject_config["member_name"]
        identity_api = f"{subject}.basic_info"
        identity_request = (
            f'r1 = {identity_api}(filter = "{identity_name_field} = {self._literal(query)}", '
            f"limit = 20) -> {identity_code_field}, {identity_name_field}"
        )
        identity_payload = self.runtime.execute_request(request=identity_request)
        identity_data = self._result_data(identity_payload)
        if not identity_payload.get("ok"):
            return self._provider_failure(identity_payload, stage="identity")
        resolution = (
            identity_data.get("name_resolution")
            if isinstance(identity_data.get("name_resolution"), Mapping)
            else {}
        )
        if self._trim(resolution.get("status")) == "ambiguous":
            candidates = [
                {
                    "code": self._trim(item.get(identity_code_field)),
                    "name": self._trim(item.get(identity_name_field)),
                }
                for item in resolution.get("candidates") or []
                if isinstance(item, Mapping)
            ]
            return {
                "status": "needs_selection",
                "message": "匹配到多个股票池，请选择后再执行。",
                "items": [],
                "candidates": candidates,
            }
        rows = [
            item
            for item in identity_data.get("rows") or []
            if isinstance(item, Mapping)
            and self._trim(item.get(identity_code_field))
        ]
        if len(rows) != 1:
            return {
                "status": "needs_input",
                "message": (
                    f"没有找到“{query}”对应的股票池。"
                    if not rows
                    else f"“{query}”对应多个股票池，请补充更准确的名称。"
                ),
                "items": [],
                "candidates": [
                    {
                        "code": self._trim(item.get(identity_code_field)),
                        "name": self._trim(item.get(identity_name_field)),
                    }
                    for item in rows
                ],
            }

        identity = {
            "code": self._trim(rows[0].get(identity_code_field)),
            "name": self._trim(rows[0].get(identity_name_field)),
            "subject_type": subject_type,
        }
        member_request = (
            f'r1 = {subject}.constitution(filter = "{member_code_field} = '
            f'{self._literal(identity["code"])}", order = "stock_code asc", limit = 500) '
            f"-> {member_code_field}, {member_name_field}, stock_code, stock_name"
        )
        member_payload = self.runtime.execute_request(request=member_request)
        member_data = self._result_data(member_payload)
        if not member_payload.get("ok"):
            return self._provider_failure(member_payload, stage="members")
        member_rows = [
            {
                "code": self._trim(item.get("stock_code")).upper(),
                "name": self._trim(item.get("stock_name")),
            }
            for item in member_data.get("rows") or []
            if isinstance(item, Mapping) and self._trim(item.get("stock_code"))
        ]
        deduped: list[Dict[str, str]] = []
        seen: set[str] = set()
        for item in member_rows:
            if item["code"] in seen:
                continue
            seen.add(item["code"])
            deduped.append(item)
        if not deduped:
            return {
                "status": "needs_input",
                "message": f"已找到“{identity['name']}”，但没有查询到可执行的成分股。",
                "items": [],
                "resolved_subject": identity,
            }
        return {
            "status": "ready",
            "message": f"已解析“{identity['name']}”的 {len(deduped)} 只成分股。",
            "items": [item["code"] for item in deduped],
            "records": deduped,
            "resolved_subject": identity,
            "member_count": len(deduped),
            "evidence": {
                "identity_api": identity_api,
                "membership_api": f"{subject}.constitution",
            },
        }

    @staticmethod
    def _result_data(payload: Mapping[str, Any]) -> Dict[str, Any]:
        result = payload.get("result") if isinstance(payload.get("result"), Mapping) else {}
        data = result.get("data") if isinstance(result.get("data"), Mapping) else {}
        return dict(data)

    @classmethod
    def _provider_failure(cls, payload: Mapping[str, Any], *, stage: str) -> Dict[str, Any]:
        execution = payload.get("execution") if isinstance(payload.get("execution"), Mapping) else {}
        validation = payload.get("validation") if isinstance(payload.get("validation"), Mapping) else {}
        reason = cls._trim(execution.get("reason")) or "；".join(
            cls._trim(item) for item in validation.get("errors") or [] if cls._trim(item)
        )
        return {
            "status": "failed",
            "message": f"股票池查询失败（{stage}）：{reason or '金融数据接口未返回有效结果'}",
            "items": [],
        }
