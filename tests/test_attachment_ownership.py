import io

import pytest
from werkzeug.datastructures import FileStorage

from src.services.attachment_service import AttachmentService, AttachmentServiceError


def _upload(name: str = "stocks.txt") -> FileStorage:
    return FileStorage(
        stream=io.BytesIO(b"600519\n"),
        filename=name,
        content_type="text/plain",
    )


def test_attachment_lookup_is_owner_scoped_when_owner_is_supplied(tmp_path) -> None:
    service = AttachmentService(data_root=tmp_path)
    item = service.save_upload(_upload(), owner_id="user_a")

    assert service.get_attachment(item["attachment_id"], owner_id="user_a") is not None
    assert service.get_attachment(item["attachment_id"], owner_id="user_b") is None


def test_attachment_batch_rejects_missing_or_foreign_ids(tmp_path) -> None:
    service = AttachmentService(data_root=tmp_path)
    item = service.save_upload(_upload(), owner_id="user_a")

    with pytest.raises(AttachmentServiceError, match="附件不存在或无权访问"):
        service.list_attachments(
            [item["attachment_id"]],
            owner_id="user_b",
            require_all=True,
        )

    with pytest.raises(AttachmentServiceError, match="附件不存在或无权访问"):
        service.list_attachments(
            ["att_missing"],
            owner_id="user_a",
            require_all=True,
        )
