from __future__ import annotations

import json
from pathlib import Path
import re
import uuid
from typing import Any, Dict, Mapping


def _trim(value: Any) -> str:
    return str(value or "").strip()


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


class CustomToolContextBundleService:
    """Build compact, file-based context for Codex custom-tool runs."""

    def __init__(
        self,
        *,
        catalog_path: str = "src/tools/finance_data/catalog/api_view_catalog.json",
        root_dir: str = "data/custom_tool_context",
    ) -> None:
        self.catalog_path = Path(catalog_path)
        self.root_dir = Path(root_dir)

    def build(
        self,
        *,
        stage: str,
        user_request: str,
        context: Mapping[str, Any] | None = None,
        run_id: str = "",
    ) -> Dict[str, Any]:
        bundle_id = self._safe_id(run_id) or uuid.uuid4().hex[:12]
        bundle_dir = self.root_dir / bundle_id
        api_dir = bundle_dir / "api_catalog"
        subject_dir = api_dir / "subjects"
        subject_dir.mkdir(parents=True, exist_ok=True)

        catalog = self._load_catalog()
        subjects = catalog.get("subjects") if isinstance(catalog.get("subjects"), dict) else {}
        patterns = catalog.get("api_class_patterns") if isinstance(catalog.get("api_class_patterns"), dict) else {}

        index_subjects = []
        for subject, subject_obj in sorted(subjects.items()):
            if not isinstance(subject_obj, dict):
                continue
            subject_file = subject_dir / f"{subject}.json"
            compact_subject = self._compact_subject(subject, subject_obj)
            subject_file.write_text(_json_text(compact_subject), encoding="utf-8")
            index_subjects.append({
                "subject": subject,
                "description": _trim((subject_obj.get("_meta") or {}).get("desc")),
                "rules": (subject_obj.get("_meta") or {}).get("rules") or [],
                "dataviews": [key for key in subject_obj.keys() if not str(key).startswith("_")],
                "file": str(subject_file.relative_to(bundle_dir)),
            })

        index = {
            "version": catalog.get("version"),
            "source_catalog": str(self.catalog_path),
            "usage": [
                "先读取本 index，按任务判断需要哪些 subject/dataview。",
                "只读取相关 subject 文件，不要全量读取所有 subject。",
                "生成自定义工具代码时，优先调用 custom_tool_sdk.finance_query(request=...)，不要直接访问数据库或底层 provider。",
            ],
            "subjects": index_subjects,
        }
        api_dir.joinpath("index.json").write_text(_json_text(index), encoding="utf-8")
        api_dir.joinpath("request_patterns.json").write_text(_json_text(patterns), encoding="utf-8")
        bundle_dir.joinpath("task.json").write_text(
            _json_text({
                "stage": stage,
                "user_request": user_request,
                "context": dict(context or {}),
            }),
            encoding="utf-8",
        )
        bundle_dir.joinpath("runtime_contract.md").write_text(self._runtime_contract(), encoding="utf-8")
        bundle_dir.joinpath("custom_tool_sdk.md").write_text(self._sdk_doc(), encoding="utf-8")
        return {
            "bundle_id": bundle_id,
            "bundle_dir": str(bundle_dir),
            "task": str((bundle_dir / "task.json").relative_to(bundle_dir)),
            "api_index": str((api_dir / "index.json").relative_to(bundle_dir)),
            "request_patterns": str((api_dir / "request_patterns.json").relative_to(bundle_dir)),
            "runtime_contract": "runtime_contract.md",
            "custom_tool_sdk": "custom_tool_sdk.md",
        }

    def _load_catalog(self) -> Dict[str, Any]:
        if not self.catalog_path.exists():
            return {"version": "", "api_class_patterns": {}, "subjects": {}}
        return json.loads(self.catalog_path.read_text(encoding="utf-8"))

    def _compact_subject(self, subject: str, subject_obj: Mapping[str, Any]) -> Dict[str, Any]:
        dataviews = {}
        for name, dataview in subject_obj.items():
            if str(name).startswith("_") or not isinstance(dataview, dict):
                continue
            dataviews[name] = {
                "desc": dataview.get("desc") or dataview.get("description") or "",
                "rules": dataview.get("rules") or [],
                "fields": self._compact_fields(dataview.get("fields")),
                "api": dataview.get("api") or [],
                "kd": dataview.get("kd") if "kd" in dataview else None,
            }
        return {
            "subject": subject,
            "meta": subject_obj.get("_meta") or {},
            "dataviews": dataviews,
        }

    @staticmethod
    def _compact_fields(fields: Any) -> Dict[str, Any]:
        if not isinstance(fields, dict):
            return {}
        result: Dict[str, Any] = {}
        for name, spec in fields.items():
            if isinstance(spec, dict):
                result[name] = {
                    "desc": spec.get("desc") or spec.get("description") or "",
                    "aliases": spec.get("aliases") or [],
                    "type": spec.get("type") or "",
                }
            else:
                result[name] = spec
        return result

    @staticmethod
    def _safe_id(value: str) -> str:
        raw = _trim(value)
        raw = re.sub(r"[^a-zA-Z0-9_-]+", "_", raw).strip("_")
        return raw[:80]

    @staticmethod
    def _runtime_contract() -> str:
        return """# Custom Tool Runtime Contract

- Python entrypoint: `run(inputs: dict) -> dict`.
- Return value must be JSON serializable.
- Do not read secrets.
- Do not directly access database tables, raw provider modules, or network from generated tool code.
- If finance data is needed, use `custom_tool_sdk.finance_query(request=...)`.
- If search is needed, use `custom_tool_sdk.web_search(query=...)`; it may return an unavailable error when no search provider is configured.
"""

    @staticmethod
    def _sdk_doc() -> str:
        return """# custom_tool_sdk

Generated custom-tool code can use these stable helpers:

```python
from custom_tool_sdk import finance_query, web_search

def run(inputs: dict) -> dict:
    quote = finance_query(
        request='r1 = stock.quote(filter = "code = 600519.SH", order = "tradedate desc", limit = 1) -> code, name, tradedate, close, pct'
    )
    return {"quote": quote}
```

## finance_query

`finance_query(request: str) -> dict`

Use the finance data protocol request string. The API catalog files describe available subjects, dataviews, fields, and request patterns.

## web_search

`web_search(query: str, limit: int = 5) -> dict`

Stable placeholder for web search. The current runtime may return `ok=false` if no provider is configured.
"""
