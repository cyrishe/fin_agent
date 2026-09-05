from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from src.services.attachment_service import AttachmentService
from src.services.file_io_tool_service import FileIoToolService
from src.services.finance_universe_input_resolver_service import (
    FinanceUniverseInputResolverService,
)


class InvocationInputError(ValueError):
    pass


class InvocationInputResolverService:
    """Parse invocation attachments without exposing file mechanics to an asset."""

    def __init__(
        self,
        *,
        attachment_service: Optional[AttachmentService] = None,
        file_io_service: Optional[FileIoToolService] = None,
        finance_universe_resolver: Optional[FinanceUniverseInputResolverService] = None,
    ) -> None:
        self.attachment_service = attachment_service or AttachmentService()
        self.file_io_service = file_io_service or FileIoToolService()
        self.finance_universe_resolver = (
            finance_universe_resolver or FinanceUniverseInputResolverService()
        )

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _is_missing(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return not value.strip()
        if isinstance(value, (list, tuple, dict, set)):
            return not value
        return False

    def inspect(self, attachments: List[Dict[str, Any]]) -> Dict[str, Any]:
        parsed_attachments = self._read_attachments(attachments)
        return {
            "attachments": parsed_attachments,
            "prompt_attachments": self._prompt_payload(parsed_attachments),
        }

    def materialize(
        self,
        source: Mapping[str, Any],
        attachments: List[Dict[str, Any]],
    ) -> List[Any]:
        return list(self.materialize_with_evidence(source, attachments).get("items") or [])

    def materialize_with_evidence(
        self,
        source: Mapping[str, Any],
        attachments: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        source_kind = self._trim(source.get("kind")).lower()
        if source_kind == "finance_universe":
            return self.finance_universe_resolver.resolve(source)
        attachment_id = self._trim(source.get("attachment_id"))
        if not attachment_id:
            return {"status": "none", "items": []}
        attachment = next(
            (
                item
                for item in attachments
                if self._trim(item.get("attachment_id")) == attachment_id
            ),
            None,
        )
        if not isinstance(attachment, Mapping):
            raise InvocationInputError(f"附件来源不存在：{attachment_id}")
        parsed = attachment.get("parsed") if isinstance(attachment.get("parsed"), Mapping) else {}
        if source_kind == "attachment_lines":
            values: List[Any] = []
            seen: set[str] = set()
            for value in parsed.get("document_lines") or []:
                normalized = self._trim(value)
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                values.append(normalized)
            return {
                "status": "ready",
                "items": values,
                "member_count": len(values),
                "evidence": {
                    "kind": "attachment_lines",
                    "attachment_id": attachment_id,
                },
            }
        column = source.get("column")
        if self._is_missing(column):
            return {"status": "none", "items": []}
        table_index = int(source.get("table_index") or 0)
        tables: List[tuple[List[Any], List[Any]]] = []
        header = list(parsed.get("header") or [])
        rows = list(parsed.get("rows") or [])
        if header:
            tables.append((header, rows))
        for matrix in parsed.get("table_preview") or []:
            if not isinstance(matrix, list) or not matrix:
                continue
            tables.append((list(matrix[0]), list(matrix[1:])))
        if table_index < 0 or table_index >= len(tables):
            raise InvocationInputError(f"附件 {attachment_id} 中不存在第 {table_index + 1} 个表格")
        selected_header, selected_rows = tables[table_index]
        column_index = self._resolve_column_index(selected_header, column)
        if column_index is None:
            raise InvocationInputError(f"附件 {attachment_id} 中未找到列：{self._trim(column)}")
        values: List[Any] = []
        seen: set[str] = set()
        for row in selected_rows:
            if not isinstance(row, (list, tuple)) or column_index >= len(row):
                continue
            value = row[column_index]
            if self._is_missing(value):
                continue
            key = (
                json.dumps(value, ensure_ascii=False, sort_keys=True)
                if isinstance(value, (dict, list))
                else str(value).strip()
            )
            if key in seen:
                continue
            seen.add(key)
            values.append(value)
        return {
            "status": "ready",
            "items": values,
            "member_count": len(values),
            "evidence": {
                "kind": "attachment",
                "attachment_id": attachment_id,
                "table_index": table_index,
                "column": column,
            },
        }

    def materialize_records(
        self,
        source: Mapping[str, Any],
        attachments: List[Dict[str, Any]],
        columns: Mapping[str, Any],
    ) -> List[Dict[str, Any]]:
        """Read aligned table columns from an already authenticated attachment."""

        attachment_id = self._trim(source.get("attachment_id"))
        if not attachment_id or not columns:
            return []
        attachment = next(
            (
                item
                for item in attachments
                if self._trim(item.get("attachment_id")) == attachment_id
            ),
            None,
        )
        if not isinstance(attachment, Mapping):
            raise InvocationInputError(f"附件来源不存在：{attachment_id}")
        parsed = attachment.get("parsed") if isinstance(attachment.get("parsed"), Mapping) else {}
        tables: List[tuple[List[Any], List[Any]]] = []
        header = list(parsed.get("header") or [])
        rows = list(parsed.get("rows") or [])
        if header:
            tables.append((header, rows))
        for matrix in parsed.get("table_preview") or []:
            if isinstance(matrix, list) and matrix:
                tables.append((list(matrix[0]), list(matrix[1:])))
        table_index = int(source.get("table_index") or 0)
        if table_index < 0 or table_index >= len(tables):
            raise InvocationInputError(f"附件 {attachment_id} 中不存在第 {table_index + 1} 个表格")
        selected_header, selected_rows = tables[table_index]
        indexes: Dict[str, int] = {}
        for name, column in columns.items():
            index = self._resolve_column_index(selected_header, column)
            if index is None:
                raise InvocationInputError(f"附件 {attachment_id} 中未找到列：{self._trim(column)}")
            indexes[str(name)] = index
        records: List[Dict[str, Any]] = []
        for row in selected_rows:
            if not isinstance(row, (list, tuple)):
                continue
            record = {
                name: row[index] if index < len(row) else None
                for name, index in indexes.items()
            }
            if any(not self._is_missing(value) for value in record.values()):
                records.append(record)
        return records

    def _read_attachments(self, attachments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            item = {
                "attachment_id": self._trim(attachment.get("attachment_id")),
                "file_name": self._trim(attachment.get("file_name")),
                "kind": self._trim(attachment.get("kind")),
            }
            if item["kind"] in {"table", "document"}:
                try:
                    file_path = self.attachment_service.resolve_absolute_path(attachment)
                    read_args = {
                        "action": "read",
                        "max_preview_rows": 5000,
                        "max_chars": 200000,
                        "_data_root": str(self.attachment_service.data_root),
                    }
                    if Path(file_path).suffix.lower() in {".doc", ".docx"}:
                        read_args["file_id"] = item["attachment_id"]
                    else:
                        read_args["file_path"] = file_path
                    parsed = self.file_io_service.run(read_args)
                    item["parsed"] = self._compact_file_result(parsed)
                except Exception as exc:
                    item["parse_error"] = str(exc)
            results.append(item)
        return results

    def _prompt_payload(self, attachments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        prompt_items: List[Dict[str, Any]] = []
        for attachment in attachments:
            item = {
                "attachment_id": self._trim(attachment.get("attachment_id")),
                "file_name": self._trim(attachment.get("file_name")),
                "kind": self._trim(attachment.get("kind")),
            }
            parsed = attachment.get("parsed") if isinstance(attachment.get("parsed"), Mapping) else {}
            if parsed:
                rows = list(parsed.get("rows") or [])
                prompt_parsed = {
                    key: value
                    for key, value in parsed.items()
                    if key != "document_lines"
                }
                item["parsed"] = {
                    **prompt_parsed,
                    "rows": rows[:20],
                    "row_count": len(rows),
                    "preview_truncated": len(rows) > 20,
                    "preview_text": self._trim(parsed.get("preview_text"))[:10000],
                }
            if attachment.get("parse_error"):
                item["parse_error"] = self._trim(attachment.get("parse_error"))
            prompt_items.append(item)
        return prompt_items

    @staticmethod
    def _resolve_column_index(header: List[Any], column: Any) -> Optional[int]:
        if isinstance(column, int):
            return column if 0 <= column < len(header) else None
        normalized = str(column or "").strip()
        if normalized.isdigit():
            index = int(normalized)
            if 0 <= index < len(header):
                return index
        for index, value in enumerate(header):
            if str(value or "").strip() == normalized:
                return index
        lowered = normalized.casefold()
        for index, value in enumerate(header):
            if str(value or "").strip().casefold() == lowered:
                return index
        return None

    def _compact_file_result(self, result: Any) -> Dict[str, Any]:
        if not isinstance(result, Mapping):
            return {"ok": False}
        data = result.get("data") if isinstance(result.get("data"), Mapping) else {}
        content = data.get("data") if isinstance(data.get("data"), Mapping) else {}
        document = content.get("document") if isinstance(content.get("document"), Mapping) else {}
        document_text = str(document.get("preview_text") or "")
        document_lines = [
            line.strip()
            for line in document_text.splitlines()
            if line.strip()
        ]
        return {
            "ok": bool(result.get("ok")),
            "file_type": self._trim(data.get("file_type")),
            "header": list(content.get("header") or [])[:100],
            "rows": list(content.get("rows") or [])[:5000],
            "preview_text": document_text[:10000].strip(),
            "document_lines": document_lines,
            "document_line_count": int(document.get("line_count") or len(document_lines)),
            "table_preview": list(document.get("table_preview") or []),
            "error": self._trim(result.get("error")),
        }
