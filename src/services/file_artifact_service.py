from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import mimetypes
import shutil
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class FileArtifactError(Exception):
    def __init__(self, failure_kind: str, message: str) -> None:
        super().__init__(message)
        self.failure_kind = failure_kind
        self.message = message


@dataclass
class ResolvedFile:
    source_type: str
    source_id: str
    path: Path
    file_name: str
    mime_type: str
    size_bytes: int
    sha256: str


class FileArtifactService:
    SCHEMA_VERSION = "file_artifact.v1"
    ARTIFACT_REF_PREFIX = "runtime://artifact/"
    MAX_UPLOAD_BYTES = 25 * 1024 * 1024
    MAX_ARTIFACT_BYTES = 50 * 1024 * 1024
    MAX_ROWS = 20000
    MAX_COLUMNS = 200
    MAX_PREVIEW_ROWS = 100
    MAX_DOCUMENT_CHARS = 200000

    TABLE_CONTENT_TYPE = "application/x-stock-agent-table"
    DOCUMENT_CONTENT_TYPE = "text/plain"

    EXTENSION_MIME_TYPES: dict[str, set[str]] = {
        ".csv": {"text/csv", "application/csv", "text/plain", "application/vnd.ms-excel"},
        ".tsv": {"text/tab-separated-values", "text/plain"},
        ".xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
        ".xls": {"application/vnd.ms-excel", "application/octet-stream"},
        ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
        ".doc": {"application/msword", "application/octet-stream"},
        ".txt": {"text/plain", "application/octet-stream"},
        ".md": {"text/markdown", "text/plain", "application/octet-stream"},
        ".json": {"application/json", "text/plain", "application/octet-stream"},
    }

    def __init__(self, *, data_root: str | Path = "data") -> None:
        self.data_root = Path(data_root)
        self.upload_root = self.data_root / "assistant_uploads"
        self.upload_meta_root = self.upload_root / "_meta"
        self.artifact_root = self.data_root / "runtime_file_artifacts"

    @staticmethod
    def trim(value: Any) -> str:
        return str(value or "").strip()

    @classmethod
    def is_artifact_ref(cls, value: Any) -> bool:
        return cls.trim(value).startswith(cls.ARTIFACT_REF_PREFIX)

    @classmethod
    def artifact_id_from_ref(cls, artifact_ref: Any) -> str:
        ref = cls.trim(artifact_ref)
        if not ref.startswith(cls.ARTIFACT_REF_PREFIX):
            raise FileArtifactError("invalid_file_id", "artifact ref must use runtime://artifact/<id>")
        artifact_id = ref[len(cls.ARTIFACT_REF_PREFIX):].strip()
        if not cls._is_safe_id(artifact_id, prefix="art_"):
            raise FileArtifactError("invalid_file_id", "artifact id is invalid")
        return artifact_id

    @staticmethod
    def _is_safe_id(value: str, *, prefix: str = "") -> bool:
        if prefix and not value.startswith(prefix):
            return False
        if not value or len(value) > 80:
            return False
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
        return all(ch in allowed for ch in value)

    @classmethod
    def reject_path_like_id(cls, file_id: Any) -> str:
        value = cls.trim(file_id)
        if not value:
            raise FileArtifactError("invalid_file_id", "file_id is required")
        if value.startswith(cls.ARTIFACT_REF_PREFIX):
            return value
        path_markers = ("/", "\\")
        if Path(value).is_absolute() or ".." in value.split("/") or ".." in value.split("\\"):
            raise FileArtifactError("path_input_rejected", "file tools only accept file_id or runtime artifact ref")
        if any(marker in value for marker in path_markers) or (len(value) > 2 and value[1:3] == ":\\"):
            raise FileArtifactError("path_input_rejected", "file tools only accept file_id or runtime artifact ref")
        return value

    def resolve_upload_file(self, file_id: Any, *, allowed_extensions: set[str]) -> ResolvedFile:
        normalized_id = self.reject_path_like_id(file_id)
        if normalized_id.startswith(self.ARTIFACT_REF_PREFIX):
            raise FileArtifactError("unsupported_format", "artifact refs are not raw upload files")
        if not self._is_safe_id(normalized_id, prefix="att_"):
            raise FileArtifactError("invalid_file_id", "upload file_id is invalid")
        meta_path = self._contained_path(self.upload_meta_root, Path(f"{normalized_id}.json"), must_exist=True)
        try:
            item = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise FileArtifactError("invalid_file_id", f"attachment metadata is invalid: {exc}") from exc
        if not isinstance(item, dict):
            raise FileArtifactError("invalid_file_id", "attachment metadata must be an object")
        storage_path = self.trim(item.get("storage_path"))
        if not storage_path:
            raise FileArtifactError("invalid_file_id", "attachment storage_path missing")
        absolute_path = self._contained_path(self.upload_root, Path(storage_path), must_exist=True)
        file_name = self.trim(item.get("file_name")) or absolute_path.name
        suffix = absolute_path.suffix.lower()
        if suffix not in allowed_extensions:
            raise FileArtifactError("unsupported_format", f"unsupported file extension: {suffix or '<none>'}")
        mime_type = self.trim(item.get("mime_type")) or self._guess_mime_type(file_name)
        if not self._mime_matches_extension(suffix, mime_type):
            raise FileArtifactError("invalid_file_id", "attachment extension and MIME type do not match")
        size_bytes = int(absolute_path.stat().st_size)
        if size_bytes > self.MAX_UPLOAD_BYTES:
            raise FileArtifactError("size_limit_exceeded", f"file exceeds size limit: {size_bytes} bytes")
        return ResolvedFile(
            source_type="upload",
            source_id=normalized_id,
            path=absolute_path,
            file_name=file_name,
            mime_type=mime_type,
            size_bytes=size_bytes,
            sha256=self._sha256(absolute_path),
        )

    def read_manifest(self, artifact_ref: Any) -> dict[str, Any]:
        artifact_id = self.artifact_id_from_ref(artifact_ref)
        artifact_dir = self._artifact_dir(artifact_id, must_exist=True)
        manifest_path = self._contained_path(artifact_dir, Path("manifest.json"), must_exist=True)
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise FileArtifactError("invalid_file_id", f"artifact manifest is invalid: {exc}") from exc
        if not isinstance(manifest, dict):
            raise FileArtifactError("invalid_file_id", "artifact manifest must be an object")
        if manifest.get("schema_version") != self.SCHEMA_VERSION:
            raise FileArtifactError("invalid_file_id", "artifact manifest schema_version is unsupported")
        if self.trim(manifest.get("artifact_id")) != artifact_id:
            raise FileArtifactError("invalid_file_id", "artifact manifest id mismatch")
        self._validate_manifest_files(artifact_dir, manifest)
        return manifest

    def read_table_preview(self, artifact_ref: Any, *, max_preview_rows: int = 50) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        manifest = self.read_manifest(artifact_ref)
        if manifest.get("kind") != "table":
            raise FileArtifactError("unsupported_format", "artifact is not a table")
        artifact_dir = self._artifact_dir(str(manifest.get("artifact_id")), must_exist=True)
        files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
        data_path = self._contained_path(artifact_dir, Path(self.trim(files.get("data"))), must_exist=True)
        rows = []
        limit = self._bounded_int(max_preview_rows, default=50, min_value=1, max_value=self.MAX_PREVIEW_ROWS)
        with data_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if len(rows) >= limit:
                    break
                if line.strip():
                    rows.append(json.loads(line))
        return manifest, rows

    def read_document_text(self, artifact_ref: Any, *, max_chars: int = 50000) -> tuple[dict[str, Any], str]:
        manifest = self.read_manifest(artifact_ref)
        if manifest.get("kind") != "document":
            raise FileArtifactError("unsupported_format", "artifact is not a document")
        artifact_dir = self._artifact_dir(str(manifest.get("artifact_id")), must_exist=True)
        files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
        text_path = self._contained_path(artifact_dir, Path(self.trim(files.get("text"))), must_exist=True)
        limit = self._bounded_int(max_chars, default=50000, min_value=1, max_value=self.MAX_DOCUMENT_CHARS)
        return manifest, text_path.read_text(encoding="utf-8")[:limit]

    def write_table_artifact(
        self,
        *,
        columns: list[dict[str, Any]],
        rows: list[dict[str, Any]],
        preview_rows: list[dict[str, Any]],
        source_file: dict[str, Any],
        created_by_tool: str,
        table_id: str = "",
        sheet_name: str = "",
    ) -> dict[str, Any]:
        artifact_id = self._new_artifact_id()
        artifact_dir = self._artifact_dir(artifact_id, must_exist=False)
        artifact_dir.mkdir(parents=True, exist_ok=False)
        data_rel = Path("data.jsonl")
        data_path = artifact_dir / data_rel
        with data_path.open("w", encoding="utf-8") as handle:
            for row in rows[: self.MAX_ROWS]:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        artifact_ref = self.artifact_ref(artifact_id)
        manifest = {
            "schema_version": self.SCHEMA_VERSION,
            "artifact_id": artifact_id,
            "artifact_ref": artifact_ref,
            "kind": "table",
            "content_type": self.TABLE_CONTENT_TYPE,
            "physical_format": "jsonl",
            "files": {"data": data_rel.as_posix()},
            "columns": columns[: self.MAX_COLUMNS],
            "row_count": min(len(rows), self.MAX_ROWS),
            "preview_row_count": len(preview_rows),
            "source_file": source_file,
            "created_by_tool": created_by_tool,
            "created_at": self._now(),
        }
        if table_id:
            manifest["table_id"] = table_id
        if sheet_name:
            manifest["sheet_name"] = sheet_name
        (artifact_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return manifest

    def write_document_artifact(
        self,
        *,
        text: str,
        source_file: dict[str, Any],
        created_by_tool: str,
        section_count: int = 0,
        table_count: int = 0,
    ) -> dict[str, Any]:
        artifact_id = self._new_artifact_id()
        artifact_dir = self._artifact_dir(artifact_id, must_exist=False)
        artifact_dir.mkdir(parents=True, exist_ok=False)
        text_rel = Path("document.txt")
        text_path = artifact_dir / text_rel
        bounded_text = str(text or "")[: self.MAX_DOCUMENT_CHARS]
        text_path.write_text(bounded_text, encoding="utf-8")
        artifact_ref = self.artifact_ref(artifact_id)
        manifest = {
            "schema_version": self.SCHEMA_VERSION,
            "artifact_id": artifact_id,
            "artifact_ref": artifact_ref,
            "kind": "document",
            "content_type": self.DOCUMENT_CONTENT_TYPE,
            "physical_format": "txt",
            "files": {"text": text_rel.as_posix()},
            "char_count": len(bounded_text),
            "section_count": int(section_count or 0),
            "table_count": int(table_count or 0),
            "source_file": source_file,
            "created_by_tool": created_by_tool,
            "created_at": self._now(),
        }
        (artifact_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return manifest

    def materialize(self, artifact_ref: Any, *, input_dir: str | Path) -> dict[str, Any]:
        manifest = self.read_manifest(artifact_ref)
        artifact_id = self.trim(manifest.get("artifact_id"))
        source_dir = self._artifact_dir(artifact_id, must_exist=True)
        manifest_files = self._validate_manifest_files(source_dir, manifest)
        files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
        target_base = Path(input_dir) / "artifacts"
        target_base.mkdir(parents=True, exist_ok=True)
        target_dir = self._contained_path(target_base, Path(artifact_id), must_exist=False)
        if target_dir.exists():
            shutil.rmtree(target_dir)
        target_dir.mkdir(parents=True, exist_ok=False)
        (target_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        copied_rel_paths: dict[str, str] = {}
        for key, source_path in manifest_files.items():
            rel = self.trim(files.get(key))
            if rel == "manifest.json":
                raise FileArtifactError("invalid_file_id", "artifact data file cannot replace manifest.json")
            target_path = self._contained_path(target_dir, Path(rel), must_exist=False)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path, follow_symlinks=False)
            copied_rel_paths[key] = rel
        copied_manifest = json.loads((target_dir / "manifest.json").read_text(encoding="utf-8"))
        self._validate_manifest_files(target_dir, copied_manifest)
        rel_manifest = Path("artifacts") / artifact_id / "manifest.json"
        result = {
            "artifact_ref": manifest.get("artifact_ref"),
            "artifact_id": artifact_id,
            "kind": manifest.get("kind"),
            "manifest_path": rel_manifest.as_posix(),
        }
        if manifest.get("kind") == "table":
            result["data_path"] = (Path("artifacts") / artifact_id / copied_rel_paths["data"]).as_posix()
        if manifest.get("kind") == "document":
            result["text_path"] = (Path("artifacts") / artifact_id / copied_rel_paths["text"]).as_posix()
        return result

    def source_file_meta(self, resolved: ResolvedFile) -> dict[str, Any]:
        return {
            "source_type": resolved.source_type,
            "source_id": resolved.source_id,
            "file_name": resolved.file_name,
            "mime_type": resolved.mime_type,
            "size_bytes": resolved.size_bytes,
            "sha256": resolved.sha256,
        }

    @classmethod
    def artifact_ref(cls, artifact_id: str) -> str:
        return f"{cls.ARTIFACT_REF_PREFIX}{artifact_id}"

    @staticmethod
    def infer_column_type(values: list[Any]) -> str:
        non_empty = [value for value in values if value not in (None, "")]
        if not non_empty:
            return "string"
        if all(isinstance(value, bool) for value in non_empty):
            return "boolean"
        if all(isinstance(value, int) and not isinstance(value, bool) for value in non_empty):
            return "integer"
        if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in non_empty):
            return "number"
        return "string"

    @staticmethod
    def normalize_cell(value: Any) -> Any:
        if value is None:
            return ""
        if isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

    @classmethod
    def build_columns(cls, names: list[Any], data_rows: list[list[Any]]) -> list[dict[str, Any]]:
        columns = []
        seen: dict[str, int] = {}
        width = min(max(len(names), max((len(row) for row in data_rows), default=0)), cls.MAX_COLUMNS)
        for index in range(width):
            raw_name = cls.trim(names[index] if index < len(names) else "") or f"column_{index + 1}"
            name = raw_name
            if name in seen:
                seen[name] += 1
                name = f"{name}_{seen[raw_name]}"
            else:
                seen[name] = 1
            values = [row[index] if index < len(row) else "" for row in data_rows]
            columns.append({"name": name, "type": cls.infer_column_type(values), "index": index})
        return columns

    @staticmethod
    def rows_to_dicts(columns: list[dict[str, Any]], data_rows: list[list[Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for data_row in data_rows:
            item = {}
            for column in columns:
                index = int(column.get("index") or 0)
                item[str(column.get("name"))] = FileArtifactService.normalize_cell(data_row[index] if index < len(data_row) else "")
            rows.append(item)
        return rows

    @classmethod
    def bounded_preview_count(cls, value: Any) -> int:
        return cls._bounded_int(value, default=50, min_value=1, max_value=cls.MAX_PREVIEW_ROWS)

    @staticmethod
    def _bounded_int(value: Any, *, default: int, min_value: int, max_value: int) -> int:
        try:
            parsed = int(value)
        except Exception:
            parsed = default
        return min(max(parsed, min_value), max_value)

    def _artifact_dir(self, artifact_id: str, *, must_exist: bool) -> Path:
        if not self._is_safe_id(artifact_id, prefix="art_"):
            raise FileArtifactError("invalid_file_id", "artifact id is invalid")
        return self._contained_path(self.artifact_root, Path(artifact_id), must_exist=must_exist)

    def _contained_path(self, root: Path, relative_path: Path, *, must_exist: bool) -> Path:
        if relative_path.is_absolute():
            raise FileArtifactError("invalid_file_id", "absolute paths are not allowed")
        root_resolved = root.resolve()
        candidate = root / relative_path
        if self._has_symlink_component(root, candidate):
            raise FileArtifactError("invalid_file_id", "symlink paths are not allowed")
        path = (root / relative_path).resolve()
        try:
            path.relative_to(root_resolved)
        except ValueError as exc:
            raise FileArtifactError("invalid_file_id", "path escapes controlled root") from exc
        if must_exist and not path.exists():
            raise FileArtifactError("invalid_file_id", "file does not exist")
        if path.is_symlink():
            raise FileArtifactError("invalid_file_id", "symlink paths are not allowed")
        return path

    def _has_symlink_component(self, root: Path, candidate: Path) -> bool:
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            return True
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                return True
        return False

    def _validate_manifest_files(self, artifact_dir: Path, manifest: dict[str, Any]) -> dict[str, Path]:
        files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
        keys = ["data"] if manifest.get("kind") == "table" else ["text"] if manifest.get("kind") == "document" else []
        if not keys:
            raise FileArtifactError("invalid_file_id", "artifact kind is unsupported")
        total = 0
        validated: dict[str, Path] = {}
        for key in keys:
            rel = self.trim(files.get(key))
            if not rel:
                raise FileArtifactError("invalid_file_id", f"artifact file `{key}` is missing")
            path = self._contained_path(artifact_dir, Path(rel), must_exist=True)
            if not path.is_file():
                raise FileArtifactError("invalid_file_id", f"artifact file `{key}` is not a file")
            total += int(path.stat().st_size)
            validated[key] = path
        if total > self.MAX_ARTIFACT_BYTES:
            raise FileArtifactError("size_limit_exceeded", "artifact exceeds materialization size limit")
        return validated

    def _new_artifact_id(self) -> str:
        return f"art_{secrets.token_hex(12)}"

    @staticmethod
    def _now() -> str:
        return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _guess_mime_type(file_name: str) -> str:
        guessed, _ = mimetypes.guess_type(file_name)
        return guessed or "application/octet-stream"

    def _mime_matches_extension(self, suffix: str, mime_type: str) -> bool:
        allowed = self.EXTENSION_MIME_TYPES.get(suffix.lower()) or set()
        if not allowed:
            return False
        if not mime_type:
            return True
        return mime_type in allowed
