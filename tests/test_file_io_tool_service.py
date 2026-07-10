import json
from pathlib import Path

from src.services.active_tool_registry_service import ActiveToolRegistryService
from src.services.file_artifact_service import FileArtifactService
from src.services.file_io_tool_service import FileIoToolService
from src.tools.registry import run_tool
from tests.test_file_intake_tools import DOCX_MIME, _attachment, _docx_bytes, _runtime, _xlsx_bytes


def test_file_io_reads_csv_by_extension(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.csv"
    file_path.write_text("name,score\nA,1\nB,2\n", encoding="utf-8")

    result = FileIoToolService().run({"action": "read", "file_path": str(file_path)})

    assert result["ok"] is True
    assert result["tool"] == "file_io"
    assert result["data"]["file_type"] == "csv"
    assert result["data"]["legacy_tool"] == "file_read_csv"
    assert result["data"]["data"] == {"header": ["name", "score"], "rows": [["A", 1], ["B", 2]]}


def test_file_io_reads_excel_by_extension(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.xlsx"
    file_path.write_bytes(_xlsx_bytes())

    result = FileIoToolService().run({"action": "read", "file_path": str(file_path)})

    assert result["ok"] is True
    assert result["data"]["file_type"] == "excel"
    assert result["data"]["data"]["header"] == ["name", "score"]


def test_file_io_reads_word_by_upload_id(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    file_id = _attachment(data_root, "att_docx", "sample.docx", _docx_bytes(), DOCX_MIME)

    result = FileIoToolService().run({"action": "read", "file_id": file_id, "max_chars": 4, **_runtime(data_root)})

    assert result["ok"] is True
    assert result["data"]["file_type"] == "word"
    assert result["data"]["data"]["document"]["preview_text"] == "季度总结"


def test_file_io_reads_text_and_rejects_pdf(tmp_path: Path) -> None:
    text_path = tmp_path / "note.txt"
    text_path.write_text("hello\nworld", encoding="utf-8")
    pdf_path = tmp_path / "report.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")

    text_result = FileIoToolService().run({"action": "read", "file_path": str(text_path)})
    pdf_result = FileIoToolService().run({"action": "read", "file_path": str(pdf_path)})

    assert text_result["ok"] is True
    assert text_result["data"]["data"]["document"]["preview_text"] == "hello\nworld"
    assert pdf_result["ok"] is False
    assert pdf_result["meta"]["failure_kind"] == "unsupported_format"


def test_file_io_writes_text_runtime_artifact(tmp_path: Path) -> None:
    result = FileIoToolService().run(
        {
            "action": "write",
            "file_type": "text",
            "content": "hello artifact",
            "_runtime": {"data_root": str(tmp_path / "data")},
        }
    )

    assert result["ok"] is True
    artifact_ref = result["data"]["artifact_ref"]
    manifest, text = FileArtifactService(data_root=tmp_path / "data").read_document_text(artifact_ref)
    assert manifest["created_by_tool"] == "file_io"
    assert text == "hello artifact"


def test_file_io_tool_registry_and_legacy_file_tools_visibility(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.csv"
    file_path.write_text("name,score\nA,1\n", encoding="utf-8")

    result = run_tool("file_io", {"action": "read", "file_path": str(file_path)})
    assert result["ok"] is True

    active_names = {item["tool_name"] for item in ActiveToolRegistryService().list_active_tools()}
    assert "file_io" in active_names
    assert "file_read_csv" not in active_names
    assert "file_read_excel" not in active_names
    assert "file_read_word" not in active_names


def test_file_io_definition_is_valid_json() -> None:
    payload = json.loads(Path("src/tools/definitions/file_io.tool.json").read_text(encoding="utf-8"))

    assert payload["name"] == "file_io"
    assert payload["availability"]["retrieval_mode"] == "retrievable"
