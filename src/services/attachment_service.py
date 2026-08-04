import datetime as dt
import json
import mimetypes
import secrets
from pathlib import Path
from typing import Any

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename


class AttachmentServiceError(Exception):
    pass


class AttachmentService:
    ALLOWED_IMAGE_MIME_TYPES = {
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/gif",
    }
    ALLOWED_DOCUMENT_MIME_TYPES = {
        "text/csv",
        "application/csv",
        "text/plain",
        "text/tab-separated-values",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    EXTENSION_KIND = {
        ".csv": "table",
        ".tsv": "table",
        ".xlsx": "table",
        ".xls": "table",
        ".txt": "document",
        ".docx": "document",
        ".doc": "document",
    }

    def __init__(self, *, data_root: str | Path = "data") -> None:
        self.data_root = Path(data_root)
        self.upload_root = self.data_root / "assistant_uploads"
        self.meta_root = self.upload_root / "_meta"

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def _detect_mime_type(self, upload: FileStorage, file_name: str) -> str:
        mime_type = self._trim(getattr(upload, "mimetype", ""))
        if mime_type:
            return mime_type
        guessed, _ = mimetypes.guess_type(file_name)
        return self._trim(guessed)

    def _detect_kind(self, file_name: str, mime_type: str) -> str:
        suffix = Path(file_name).suffix.lower()
        if mime_type in self.ALLOWED_IMAGE_MIME_TYPES:
            return "image"
        return self.EXTENSION_KIND.get(suffix, "unknown")

    def _relative_file_path(self, attachment_id: str, file_name: str) -> Path:
        today = dt.datetime.now()
        safe_name = secure_filename(file_name) or f"{attachment_id}.bin"
        return Path(str(today.year)) / f"{today.month:02d}" / f"{attachment_id}_{safe_name}"

    def save_upload(
        self,
        upload: FileStorage,
        *,
        owner_id: str = "",
    ) -> dict[str, Any]:
        file_name = self._trim(getattr(upload, "filename", ""))
        if not file_name:
            raise AttachmentServiceError("上传文件缺少文件名")
        mime_type = self._detect_mime_type(upload, file_name)
        allowed_mime_types = self.ALLOWED_IMAGE_MIME_TYPES | self.ALLOWED_DOCUMENT_MIME_TYPES
        if mime_type not in allowed_mime_types:
            raise AttachmentServiceError("当前仅支持 png/jpg/webp/gif 图片以及 csv/tsv/xlsx/txt/docx 文件")
        kind = self._detect_kind(file_name, mime_type)
        if kind == "unknown":
            raise AttachmentServiceError("当前仅支持 png/jpg/webp/gif 图片以及 csv/tsv/xlsx/txt/docx 文件")
        attachment_id = f"att_{secrets.token_hex(12)}"
        relative_path = self._relative_file_path(attachment_id, file_name)
        absolute_path = self.upload_root / relative_path
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        upload.save(absolute_path)
        file_size = absolute_path.stat().st_size if absolute_path.exists() else 0
        item = {
            "attachment_id": attachment_id,
            "kind": kind,
            "mime_type": mime_type,
            "file_name": file_name,
            "file_size": file_size,
            "storage_type": "local",
            "storage_path": str(relative_path.as_posix()),
            "preview_url": f"/data/assistant_uploads/{relative_path.as_posix()}",
            "owner_id": self._trim(owner_id),
            "created_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.meta_root.mkdir(parents=True, exist_ok=True)
        meta_path = self.meta_root / f"{attachment_id}.json"
        meta_path.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
        return item

    def get_attachment(
        self,
        attachment_id: str,
        *,
        owner_id: str | None = None,
    ) -> dict[str, Any] | None:
        normalized_id = self._trim(attachment_id)
        if not normalized_id:
            return None
        meta_path = self.meta_root / f"{normalized_id}.json"
        if not meta_path.exists():
            return None
        try:
            item = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(item, dict):
            return None
        if owner_id is not None and self._trim(item.get("owner_id")) != self._trim(owner_id):
            return None
        return item

    def resolve_absolute_path(self, item_or_attachment_id: Any) -> str:
        item = item_or_attachment_id
        if not isinstance(item_or_attachment_id, dict):
            item = self.get_attachment(str(item_or_attachment_id))
        if not isinstance(item, dict):
            raise AttachmentServiceError("attachment not found")
        storage_path = self._trim(item.get("storage_path"))
        if not storage_path:
            raise AttachmentServiceError("attachment storage_path missing")
        relative_path = Path(storage_path)
        if relative_path.is_absolute():
            raise AttachmentServiceError("attachment storage_path invalid")
        root = self.upload_root.resolve()
        absolute_path = (self.upload_root / relative_path).resolve()
        try:
            absolute_path.relative_to(root)
        except ValueError as exc:
            raise AttachmentServiceError("attachment storage_path escapes upload root") from exc
        if not absolute_path.exists():
            raise AttachmentServiceError(f"attachment file not found: {storage_path}")
        if absolute_path.is_symlink():
            raise AttachmentServiceError("attachment storage_path invalid")
        return str(absolute_path)

    def list_attachments(
        self,
        attachment_ids: list[Any] | None,
        *,
        owner_id: str | None = None,
        require_all: bool = False,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for attachment_id in attachment_ids or []:
            item = self.get_attachment(str(attachment_id), owner_id=owner_id)
            if isinstance(item, dict):
                items.append(item)
            elif require_all:
                # Missing and foreign IDs intentionally share one response.
                raise AttachmentServiceError("附件不存在或无权访问")
        return items
