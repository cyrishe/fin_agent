from __future__ import annotations

import csv
import json
from io import StringIO
from pathlib import Path
from typing import Any, Dict, Mapping

from src.services.file_artifact_service import FileArtifactError, FileArtifactService
from src.tools.file_intake_tools import run_csv, run_excel, run_word


class FileIoToolService:
    """Top-level file I/O gateway.

    The tool routes by action and file extension. It intentionally writes only
    runtime artifacts, not arbitrary local paths.
    """

    READ_EXTENSIONS = {
        ".csv": "csv",
        ".tsv": "csv",
        ".xlsx": "excel",
        ".xls": "excel",
        ".docx": "word",
        ".doc": "word",
        ".txt": "text",
        ".md": "text",
        ".json": "text",
        ".pdf": "pdf",
    }

    def run(self, args: Mapping[str, Any] | None = None) -> Dict[str, Any]:
        payload = dict(args or {})
        action = self._trim(payload.get("action") or "read").lower()
        try:
            if action == "read":
                return self.read(payload)
            if action == "write":
                return self.write(payload)
            return self._error("unsupported_action", f"unsupported file_io action: {action}")
        except FileArtifactError as exc:
            return self._error(exc.failure_kind, exc.message)
        except Exception as exc:
            return self._error("file_io_error", str(exc))

    def read(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        file_path = self._trim(payload.get("file_path"))
        file_id = self._trim(payload.get("file_id"))
        file_type = self._normalize_file_type(payload.get("file_type"))
        if file_path:
            path = Path(file_path).expanduser()
            if not path.exists() or not path.is_file():
                raise FileArtifactError("invalid_file_path", "file_path does not exist")
            resolved_type = file_type or self._file_type_from_suffix(path.suffix)
            return self._read_path(path=path, file_type=resolved_type, payload=payload)
        if file_id:
            return self._read_file_id(file_id=file_id, file_type=file_type, payload=payload)
        raise FileArtifactError("missing_required_input", "file_path or file_id is required")

    def write(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        file_type = self._normalize_file_type(payload.get("file_type"))
        file_name = self._trim(payload.get("file_name"))
        if not file_type and file_name:
            file_type = self._file_type_from_suffix(Path(file_name).suffix)
        if file_type in {"txt", "text", "md", "markdown", "json"}:
            return self._write_document(payload=payload, file_type=file_type or "text")
        if file_type in {"csv", "tsv", "table"}:
            return self._write_table(payload=payload, file_type=file_type or "csv")
        raise FileArtifactError("unsupported_format", f"unsupported write file_type: {file_type or '<empty>'}")

    def _read_path(self, *, path: Path, file_type: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        if file_type in {"csv", "tsv"}:
            return self._wrap_legacy(run_csv({"file_path": str(path), **self._runtime_args(payload)}), file_type=file_type)
        if file_type == "excel":
            return self._wrap_legacy(run_excel({"file_path": str(path), **self._runtime_args(payload)}), file_type=file_type)
        if file_type in {"text", "json"}:
            return self._ok(self._read_text_path(path=path, payload=payload))
        if file_type == "word":
            raise FileArtifactError("missing_required_input", "word reading requires file_id so document artifacts can be tracked")
        if file_type == "pdf":
            raise FileArtifactError("unsupported_format", "pdf reader is not implemented yet")
        raise FileArtifactError("unsupported_format", f"unsupported file extension: {path.suffix or '<none>'}")

    def _read_file_id(self, *, file_id: str, file_type: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        service = self._artifact_service(payload)
        if service.is_artifact_ref(file_id):
            return self._read_artifact_ref(service=service, artifact_ref=file_id, payload=payload)
        suffix = self._upload_suffix(service=service, file_id=file_id)
        resolved_type = file_type or self._file_type_from_suffix(suffix)
        if resolved_type == "word":
            return self._wrap_legacy(run_word({"file_id": file_id, **dict(payload)}), file_type=resolved_type)
        resolved = service.resolve_upload_file(file_id, allowed_extensions=set(self.READ_EXTENSIONS))
        if resolved_type in {"csv", "excel", "text"}:
            return self._read_path(path=resolved.path, file_type=resolved_type, payload=payload)
        if resolved_type == "pdf":
            raise FileArtifactError("unsupported_format", "pdf reader is not implemented yet")
        raise FileArtifactError("unsupported_format", f"unsupported file extension: {suffix or '<none>'}")

    def _read_artifact_ref(self, *, service: FileArtifactService, artifact_ref: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        manifest = service.read_manifest(artifact_ref)
        if manifest.get("kind") == "table":
            max_rows = FileArtifactService._bounded_int(payload.get("max_preview_rows"), default=50, min_value=1, max_value=FileArtifactService.MAX_PREVIEW_ROWS)
            table_manifest, rows = service.read_table_preview(artifact_ref, max_preview_rows=max_rows)
            columns = table_manifest.get("columns") if isinstance(table_manifest.get("columns"), list) else []
            header = [str(item.get("name") or item.get("id") or "") for item in columns if isinstance(item, dict)]
            return self._ok(
                {
                    "mode": "read",
                    "file_type": "table_artifact",
                    "artifact_ref": artifact_ref,
                    "data": {"header": header, "rows": rows},
                    "manifest": self._manifest_summary(table_manifest),
                }
            )
        if manifest.get("kind") == "document":
            max_chars = FileArtifactService._bounded_int(payload.get("max_chars"), default=50000, min_value=1, max_value=FileArtifactService.MAX_DOCUMENT_CHARS)
            document_manifest, text = service.read_document_text(artifact_ref, max_chars=max_chars)
            return self._ok(
                {
                    "mode": "read",
                    "file_type": "document_artifact",
                    "artifact_ref": artifact_ref,
                    "data": {
                        "document": {
                            "preview_text": text,
                            "char_count": int(document_manifest.get("char_count") or len(text)),
                            "text_artifact_ref": artifact_ref,
                        }
                    },
                    "manifest": self._manifest_summary(document_manifest),
                }
            )
        raise FileArtifactError("unsupported_format", "artifact kind is not supported")

    def _read_text_path(self, *, path: Path, payload: Mapping[str, Any]) -> Dict[str, Any]:
        max_chars = FileArtifactService._bounded_int(payload.get("max_chars"), default=50000, min_value=1, max_value=FileArtifactService.MAX_DOCUMENT_CHARS)
        raw = path.read_bytes()
        last_error: Exception | None = None
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError as exc:
                last_error = exc
        else:
            raise FileArtifactError("unsupported_format", f"unable to decode text file: {last_error}")
        preview = text[:max_chars]
        return {
            "mode": "read",
            "file_type": "text",
            "data": {
                "document": {
                    "preview_text": preview,
                    "char_count": len(text),
                    "line_count": len(text.splitlines()),
                }
            },
        }

    def _write_document(self, *, payload: Mapping[str, Any], file_type: str) -> Dict[str, Any]:
        content = payload.get("content")
        if content is None and "json" in payload:
            content = json.dumps(payload.get("json"), ensure_ascii=False, indent=2)
        if content is None:
            raise FileArtifactError("missing_required_input", "content is required for document write")
        text = str(content)
        service = self._artifact_service(payload)
        manifest = service.write_document_artifact(
            text=text,
            source_file=self._generated_source_meta(payload),
            created_by_tool="file_io",
            section_count=len([line for line in text.splitlines() if line.strip()]),
            table_count=0,
        )
        return self._ok(
            {
                "mode": "write",
                "file_type": file_type,
                "artifact_ref": manifest["artifact_ref"],
                "manifest": self._manifest_summary(manifest),
            }
        )

    def _write_table(self, *, payload: Mapping[str, Any], file_type: str) -> Dict[str, Any]:
        rows = payload.get("rows")
        if not isinstance(rows, list):
            content = self._trim(payload.get("content"))
            if content:
                rows = list(csv.DictReader(StringIO(content), delimiter="\t" if file_type == "tsv" else ","))
            else:
                raise FileArtifactError("missing_required_input", "rows or content is required for table write")
        normalized_rows = self._normalize_table_rows(rows)
        header = list(normalized_rows[0].keys()) if normalized_rows else [self._trim(item) for item in (payload.get("header") or []) if self._trim(item)]
        columns = [{"name": name, "type": "unknown"} for name in header]
        service = self._artifact_service(payload)
        manifest = service.write_table_artifact(
            columns=columns,
            rows=normalized_rows,
            preview_rows=normalized_rows[: FileArtifactService.MAX_PREVIEW_ROWS],
            source_file=self._generated_source_meta(payload),
            created_by_tool="file_io",
            table_id=self._trim(payload.get("table_id")),
            sheet_name=self._trim(payload.get("sheet_name")),
        )
        return self._ok(
            {
                "mode": "write",
                "file_type": file_type,
                "artifact_ref": manifest["artifact_ref"],
                "manifest": self._manifest_summary(manifest),
            }
        )

    def _normalize_table_rows(self, rows: list[Any]) -> list[dict[str, Any]]:
        if not rows:
            return []
        if all(isinstance(item, Mapping) for item in rows):
            return [dict(item) for item in rows]
        header = rows[0] if isinstance(rows[0], list) else []
        header_names = [str(item or f"column_{index + 1}") for index, item in enumerate(header)]
        normalized = []
        for row in rows[1:]:
            if not isinstance(row, list):
                continue
            normalized.append({name: row[index] if index < len(row) else "" for index, name in enumerate(header_names)})
        return normalized

    def _wrap_legacy(self, result: Dict[str, Any], *, file_type: str) -> Dict[str, Any]:
        if result.get("ok") is not True:
            return {
                "tool": "file_io",
                "ok": False,
                "data": {},
                "error": str(result.get("error") or ""),
                "meta": dict(result.get("meta") or {}),
            }
        return self._ok(
            {
                "mode": "read",
                "file_type": file_type,
                "legacy_tool": result.get("tool"),
                "data": result.get("data") if isinstance(result.get("data"), dict) else {},
            }
        )

    def _upload_suffix(self, *, service: FileArtifactService, file_id: str) -> str:
        normalized = service.reject_path_like_id(file_id)
        if not FileArtifactService._is_safe_id(normalized, prefix="att_"):
            raise FileArtifactError("invalid_file_id", "upload file_id is invalid")
        meta_path = service.upload_meta_root / f"{normalized}.json"
        try:
            item = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise FileArtifactError("invalid_file_id", f"attachment metadata is invalid: {exc}") from exc
        return Path(self._trim(item.get("file_name")) or self._trim(item.get("storage_path"))).suffix.lower()

    def _file_type_from_suffix(self, suffix: str) -> str:
        normalized = str(suffix or "").lower()
        file_type = self.READ_EXTENSIONS.get(normalized)
        if not file_type:
            raise FileArtifactError("unsupported_format", f"unsupported file extension: {normalized or '<none>'}")
        return file_type

    def _normalize_file_type(self, value: Any) -> str:
        normalized = self._trim(value).lower()
        if normalized == "auto":
            return ""
        if normalized in {"txt", "md", "markdown"}:
            return "text"
        if normalized == "xlsx":
            return "excel"
        if normalized == "docx":
            return "word"
        return normalized

    def _runtime_args(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        runtime = payload.get("_runtime") if isinstance(payload.get("_runtime"), dict) else None
        return {"_runtime": runtime} if runtime else {}

    def _artifact_service(self, payload: Mapping[str, Any]) -> FileArtifactService:
        runtime = payload.get("_runtime") if isinstance(payload.get("_runtime"), dict) else {}
        data_root = runtime.get("data_root") or payload.get("_data_root") or "data"
        return FileArtifactService(data_root=data_root)

    def _generated_source_meta(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        file_name = self._trim(payload.get("file_name")) or "generated"
        return {
            "source_type": "generated",
            "source_id": self._trim(payload.get("source_id")) or "",
            "file_name": file_name,
            "mime_type": self._trim(payload.get("mime_type")) or "",
            "size_bytes": 0,
            "sha256": "",
        }

    @staticmethod
    def _manifest_summary(manifest: Mapping[str, Any]) -> Dict[str, Any]:
        keys = [
            "schema_version",
            "artifact_id",
            "artifact_ref",
            "kind",
            "content_type",
            "physical_format",
            "files",
            "row_count",
            "preview_row_count",
            "char_count",
            "section_count",
            "table_count",
            "created_by_tool",
            "created_at",
        ]
        return {key: manifest[key] for key in keys if key in manifest}

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _ok(data: Dict[str, Any]) -> Dict[str, Any]:
        return {"tool": "file_io", "ok": True, "data": data, "error": ""}

    @staticmethod
    def _error(failure_kind: str, message: str) -> Dict[str, Any]:
        return {
            "tool": "file_io",
            "ok": False,
            "data": {},
            "error": message,
            "meta": {"failure_kind": failure_kind},
        }
