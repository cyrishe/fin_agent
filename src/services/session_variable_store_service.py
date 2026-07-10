from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from src.services.file_artifact_service import FileArtifactService


class SessionVariableStoreService:
    """Session-scoped index for data produced by top-level tools.

    The store keeps prompt-sized schema/sample metadata separate from full data.
    Full table/document payloads are stored as runtime file artifacts when
    possible, so callers can reference them without injecting them into prompts.
    """

    SCHEMA_VERSION = "session_variable_store.v1"
    REF_PREFIX = "session://"
    MAX_SAMPLE_ROWS = 3
    MAX_SAMPLE_TEXT_CHARS = 1000

    def __init__(self, *, data_root: str | Path = "data") -> None:
        self.data_root = Path(data_root)
        self.root = self.data_root / "session_variables"
        self.artifact_service = FileArtifactService(data_root=self.data_root)

    def register_tool_result(
        self,
        *,
        session_id: str,
        tool_name: str,
        result: Mapping[str, Any],
        task: str = "",
        runtime_ctx: Mapping[str, Any] | None = None,
        local_alias: str = "",
    ) -> dict[str, Any] | None:
        normalized_session_id = self._safe_id(session_id, prefix="sess")
        if not normalized_session_id or not isinstance(result, Mapping):
            return None
        if result.get("ok") is False:
            return None

        extracted = self._extract_payload(tool_name=tool_name, result=result)
        if not extracted:
            return None

        session_dir = self._session_dir(normalized_session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        index = self._read_index(session_dir)
        var_id = self._next_var_id(index)
        artifact_ref = extracted.get("artifact_ref") or self._write_artifact(
            tool_name=tool_name,
            var_id=var_id,
            extracted=extracted,
        )
        data_ref = self.variable_ref(normalized_session_id, var_id)
        manifest = {
            "schema_version": self.SCHEMA_VERSION,
            "session_id": normalized_session_id,
            "raw_session_id": str(session_id or ""),
            "var_id": var_id,
            "data_ref": data_ref,
            "artifact_ref": artifact_ref,
            "tool_name": str(tool_name or "").strip(),
            "task": str(task or "").strip(),
            "local_alias": str(local_alias or extracted.get("local_alias") or "").strip(),
            "data_type": extracted["data_type"],
            "schema": extracted["schema"],
            "sample": extracted["sample"],
            "row_count": extracted.get("row_count"),
            "status": "ok",
            "runtime": self._runtime_summary(runtime_ctx or {}),
            "created_at": self._now(),
        }
        self._write_manifest(session_dir, var_id, manifest)
        index["variables"].append(self._index_item(manifest))
        self._write_index(session_dir, index)
        return self._public_summary(manifest)

    def list_variables(self, *, session_id: str) -> list[dict[str, Any]]:
        session_dir = self._session_dir(self._safe_id(session_id, prefix="sess"))
        index = self._read_index(session_dir)
        return list(index.get("variables") or [])

    def read_manifest(self, *, session_id: str, var_id: str) -> dict[str, Any]:
        session_dir = self._session_dir(self._safe_id(session_id, prefix="sess"))
        path = session_dir / f"{self._safe_var_id(var_id)}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def resolve_data_ref(self, data_ref: str) -> dict[str, Any]:
        session_id, var_id = self.parse_data_ref(data_ref)
        return self.read_manifest(session_id=session_id, var_id=var_id)

    def format_variables_for_prompt(self, *, session_id: str) -> str:
        rows = self.list_variables(session_id=session_id)
        blocks: list[str] = []
        for item in rows:
            blocks.append(f"## {item.get('var_id')}")
            blocks.append(f"- tool: {item.get('tool_name') or ''}")
            if item.get("task"):
                blocks.append(f"- task: {item.get('task')}")
            blocks.append(f"- data_type: {item.get('data_type') or ''}")
            if item.get("row_count") is not None:
                blocks.append(f"- row_count: {item.get('row_count')}")
            blocks.append("- schema:")
            blocks.append("```json\n" + json.dumps(item.get("schema") or {}, ensure_ascii=False, indent=2) + "\n```")
            blocks.append("- sample:")
            blocks.append("```json\n" + json.dumps(item.get("sample") or {}, ensure_ascii=False, default=str, indent=2) + "\n```")
            blocks.append(f"- data_ref: `{item.get('data_ref') or ''}`")
            if item.get("artifact_ref"):
                blocks.append(f"- artifact_ref: `{item.get('artifact_ref')}`")
            blocks.append("")
        return "\n".join(blocks).strip()

    @classmethod
    def variable_ref(cls, session_id: str, var_id: str) -> str:
        return f"{cls.REF_PREFIX}{session_id}/vars/{var_id}"

    @classmethod
    def parse_data_ref(cls, data_ref: str) -> tuple[str, str]:
        raw = str(data_ref or "").strip()
        if not raw.startswith(cls.REF_PREFIX):
            raise ValueError("invalid session data_ref")
        tail = raw[len(cls.REF_PREFIX):]
        match = re.fullmatch(r"([^/]+)/vars/(v\d+)", tail)
        if not match:
            raise ValueError("invalid session data_ref")
        return match.group(1), match.group(2)

    def _extract_payload(self, *, tool_name: str, result: Mapping[str, Any]) -> dict[str, Any]:
        if tool_name == "finance_data_query":
            payload = result.get("data") if isinstance(result.get("data"), Mapping) else {}
            finance_result = payload.get("result") if isinstance(payload.get("result"), Mapping) else {}
            data = finance_result.get("data") if isinstance(finance_result.get("data"), Mapping) else {}
            rows = data.get("rows") if isinstance(data.get("rows"), list) else []
            columns = self._normalize_columns(finance_result.get("columns"), rows=rows)
            return self._table_payload(
                columns=columns,
                rows=[row for row in rows if isinstance(row, Mapping)],
                row_count=self._int_or_none(data.get("row_count")) or len(rows),
                local_alias=str(finance_result.get("name") or ""),
            )

        data = result.get("data") if isinstance(result.get("data"), Mapping) else {}
        table = self._find_table(data)
        if table:
            return table
        document = self._find_document(data)
        if document:
            return document
        return self._object_payload(data if data else result)

    def _table_payload(
        self,
        *,
        columns: list[dict[str, Any]],
        rows: list[Mapping[str, Any]],
        row_count: int | None = None,
        artifact_ref: str = "",
        local_alias: str = "",
    ) -> dict[str, Any]:
        normalized_rows = [dict(row) for row in rows]
        return {
            "data_type": "table",
            "schema": {"columns": columns},
            "sample": {"rows": normalized_rows[: self.MAX_SAMPLE_ROWS]},
            "rows": normalized_rows,
            "row_count": row_count if row_count is not None else len(normalized_rows),
            "artifact_ref": artifact_ref,
            "local_alias": local_alias,
        }

    def _find_table(self, value: Mapping[str, Any]) -> dict[str, Any]:
        artifact_ref = str(value.get("artifact_ref") or "").strip()
        manifest = value.get("manifest") if isinstance(value.get("manifest"), Mapping) else {}
        nested = value.get("data") if isinstance(value.get("data"), Mapping) else {}
        rows = nested.get("rows") if isinstance(nested.get("rows"), list) else value.get("rows")
        if not isinstance(rows, list):
            return {}
        columns_source = manifest.get("columns") or nested.get("header") or value.get("columns") or value.get("header")
        dict_rows = self._normalize_rows(columns_source, rows)
        columns = self._normalize_columns(columns_source, rows=dict_rows)
        return self._table_payload(
            columns=columns,
            rows=dict_rows,
            row_count=self._int_or_none(manifest.get("row_count")) or len(rows),
            artifact_ref=artifact_ref,
        )

    def _find_document(self, value: Mapping[str, Any]) -> dict[str, Any]:
        artifact_ref = str(value.get("artifact_ref") or "").strip()
        nested = value.get("data") if isinstance(value.get("data"), Mapping) else {}
        document = nested.get("document") if isinstance(nested.get("document"), Mapping) else value.get("document")
        if not isinstance(document, Mapping):
            return {}
        text = str(document.get("preview_text") or document.get("text") or "")
        if not text:
            return {}
        return {
            "data_type": "document",
            "schema": {"fields": ["preview_text", "char_count", "line_count"]},
            "sample": {
                "preview_text": text[: self.MAX_SAMPLE_TEXT_CHARS],
                "char_count": self._int_or_none(document.get("char_count")) or len(text),
                "line_count": self._int_or_none(document.get("line_count")),
            },
            "text": text,
            "row_count": None,
            "artifact_ref": artifact_ref or str(document.get("text_artifact_ref") or "").strip(),
        }

    def _object_payload(self, value: Mapping[str, Any]) -> dict[str, Any]:
        fields = list(value.keys())[:20]
        sample = {str(key): self._sample_value(value.get(key)) for key in fields[:8]}
        return {
            "data_type": "object",
            "schema": {"fields": [str(key) for key in fields]},
            "sample": sample,
            "object": dict(value),
            "row_count": None,
            "artifact_ref": self._find_artifact_ref(value),
        }

    def _write_artifact(self, *, tool_name: str, var_id: str, extracted: Mapping[str, Any]) -> str:
        data_type = str(extracted.get("data_type") or "")
        if data_type == "table":
            rows = extracted.get("rows") if isinstance(extracted.get("rows"), list) else []
            manifest = self.artifact_service.write_table_artifact(
                columns=list((extracted.get("schema") or {}).get("columns") or []),
                rows=[dict(row) for row in rows if isinstance(row, Mapping)],
                preview_rows=[dict(row) for row in rows[: FileArtifactService.MAX_PREVIEW_ROWS] if isinstance(row, Mapping)],
                source_file=self._generated_source(tool_name=tool_name, var_id=var_id),
                created_by_tool=tool_name,
            )
            return str(manifest.get("artifact_ref") or "")
        if data_type == "document":
            text = str(extracted.get("text") or extracted.get("sample", {}).get("preview_text") or "")
        else:
            text = json.dumps(extracted.get("object") or extracted.get("sample") or {}, ensure_ascii=False, default=str, indent=2)
        if not text:
            return ""
        manifest = self.artifact_service.write_document_artifact(
            text=text,
            source_file=self._generated_source(tool_name=tool_name, var_id=var_id),
            created_by_tool=tool_name,
        )
        return str(manifest.get("artifact_ref") or "")

    def _normalize_columns(self, columns: Any, *, rows: list[Any]) -> list[dict[str, Any]]:
        if isinstance(columns, list) and columns and all(isinstance(item, Mapping) for item in columns):
            return [
                {
                    "name": str(item.get("name") or item.get("id") or item.get("field") or "").strip(),
                    "type": str(item.get("type") or "unknown").strip() or "unknown",
                }
                for item in columns
                if str(item.get("name") or item.get("id") or item.get("field") or "").strip()
            ]
        names = [str(item or "").strip() for item in columns] if isinstance(columns, list) else []
        if not names and rows and isinstance(rows[0], Mapping):
            names = [str(key) for key in rows[0].keys()]
        normalized_rows = [row for row in rows if isinstance(row, Mapping)]
        out = []
        for name in names:
            values = [row.get(name) for row in normalized_rows[:50]]
            out.append({"name": name, "type": FileArtifactService.infer_column_type(values)})
        return out

    def _normalize_rows(self, columns: Any, rows: list[Any]) -> list[dict[str, Any]]:
        if all(isinstance(row, Mapping) for row in rows):
            return [dict(row) for row in rows]
        header = [str(item or "").strip() for item in columns] if isinstance(columns, list) else []
        if not header:
            return []
        normalized = []
        for row in rows:
            if not isinstance(row, list):
                continue
            normalized.append({name: row[index] if index < len(row) else "" for index, name in enumerate(header) if name})
        return normalized

    def _read_index(self, session_dir: Path) -> dict[str, Any]:
        path = session_dir / "index.json"
        if not path.exists():
            return {"schema_version": self.SCHEMA_VERSION, "variables": []}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"schema_version": self.SCHEMA_VERSION, "variables": []}
        if not isinstance(payload, dict):
            return {"schema_version": self.SCHEMA_VERSION, "variables": []}
        variables = payload.get("variables") if isinstance(payload.get("variables"), list) else []
        return {"schema_version": self.SCHEMA_VERSION, "variables": variables}

    def _write_index(self, session_dir: Path, index: Mapping[str, Any]) -> None:
        (session_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, default=str, indent=2), encoding="utf-8")

    def _write_manifest(self, session_dir: Path, var_id: str, manifest: Mapping[str, Any]) -> None:
        (session_dir / f"{var_id}.json").write_text(json.dumps(manifest, ensure_ascii=False, default=str, indent=2), encoding="utf-8")

    def _next_var_id(self, index: Mapping[str, Any]) -> str:
        variables = index.get("variables") if isinstance(index.get("variables"), list) else []
        max_id = 0
        for item in variables:
            if not isinstance(item, Mapping):
                continue
            match = re.fullmatch(r"v(\d+)", str(item.get("var_id") or ""))
            if match:
                max_id = max(max_id, int(match.group(1)))
        return f"v{max_id + 1}"

    def _index_item(self, manifest: Mapping[str, Any]) -> dict[str, Any]:
        keys = [
            "var_id",
            "data_ref",
            "artifact_ref",
            "tool_name",
            "task",
            "local_alias",
            "data_type",
            "schema",
            "sample",
            "row_count",
            "status",
            "runtime",
            "created_at",
        ]
        return {key: manifest.get(key) for key in keys if key in manifest}

    def _public_summary(self, manifest: Mapping[str, Any]) -> dict[str, Any]:
        return self._index_item(manifest)

    def _session_dir(self, session_id: str) -> Path:
        return self.root / session_id

    def _runtime_summary(self, runtime_ctx: Mapping[str, Any]) -> dict[str, Any]:
        keys = ["conversation_id", "session_id", "thread_id", "task_id", "turn_id", "goal", "source_type"]
        return {key: runtime_ctx.get(key) for key in keys if runtime_ctx.get(key) not in (None, "")}

    def _generated_source(self, *, tool_name: str, var_id: str) -> dict[str, Any]:
        return {
            "source_type": "session_variable",
            "source_id": var_id,
            "file_name": f"{tool_name}_{var_id}",
            "mime_type": "",
            "size_bytes": 0,
            "sha256": "",
        }

    def _find_artifact_ref(self, value: Any) -> str:
        if isinstance(value, str) and value.startswith(FileArtifactService.ARTIFACT_REF_PREFIX):
            return value
        if isinstance(value, Mapping):
            direct = value.get("artifact_ref")
            if isinstance(direct, str) and direct.startswith(FileArtifactService.ARTIFACT_REF_PREFIX):
                return direct
            for child in value.values():
                found = self._find_artifact_ref(child)
                if found:
                    return found
        if isinstance(value, list):
            for child in value:
                found = self._find_artifact_ref(child)
                if found:
                    return found
        return ""

    def _sample_value(self, value: Any) -> Any:
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        if isinstance(value, str):
            return value[:180]
        if isinstance(value, Mapping):
            return {str(key): self._sample_value(child) for key, child in list(value.items())[:5]}
        if isinstance(value, list):
            return {"len": len(value), "first": self._sample_value(value[0]) if value else None}
        return str(value)[:180]

    @staticmethod
    def _int_or_none(value: Any) -> int | None:
        try:
            return int(value)
        except Exception:
            return None

    @staticmethod
    def _now() -> str:
        return dt.datetime.now(dt.timezone.utc).isoformat()

    @staticmethod
    def _safe_var_id(value: str) -> str:
        normalized = str(value or "").strip()
        if re.fullmatch(r"v\d+", normalized):
            return normalized
        raise ValueError("invalid session variable id")

    @staticmethod
    def _safe_id(value: str, *, prefix: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", raw).strip("_")
        if not safe:
            digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
            safe = f"{prefix}_{digest}"
        if len(safe) > 80:
            digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
            safe = f"{safe[:60]}_{digest}"
        return safe
