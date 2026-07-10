import json
import zipfile
from pathlib import Path

from src.services.file_artifact_service import FileArtifactService
from src.tools.file_intake_tools import run_csv, run_excel, run_word


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _attachment(data_root: Path, attachment_id: str, file_name: str, payload: bytes, mime_type: str) -> str:
    upload_root = data_root / "assistant_uploads"
    meta_root = upload_root / "_meta"
    relative_path = Path("2026") / "05" / f"{attachment_id}_{file_name}"
    path = upload_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    meta_root.mkdir(parents=True, exist_ok=True)
    item = {
        "attachment_id": attachment_id,
        "kind": "table" if Path(file_name).suffix.lower() in {".csv", ".tsv", ".xlsx"} else "document",
        "mime_type": mime_type,
        "file_name": file_name,
        "file_size": len(payload),
        "storage_type": "local",
        "storage_path": relative_path.as_posix(),
    }
    (meta_root / f"{attachment_id}.json").write_text(json.dumps(item, ensure_ascii=False), encoding="utf-8")
    return attachment_id


def _xlsx_bytes() -> bytes:
    from io import BytesIO

    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\"/>")
        archive.writestr(
            "xl/workbook.xml",
            """<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Alpha" sheetId="1" r:id="rId1"/><sheet name="Beta" sheetId="2" r:id="rId2"/></sheets></workbook>""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Target="worksheets/sheet2.xml"/></Relationships>""",
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            """<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>name</t></si><si><t>score</t></si><si><t>A</t></si><si><t>B</t></si><si><t>code</t></si><si><t>value</t></si><si><t>X</t></si></sst>""",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            """<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row><row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>3</v></c></row><row r="3"><c r="A3" t="s"><v>3</v></c><c r="B3"><v>7</v></c></row></sheetData></worksheet>""",
        )
        archive.writestr(
            "xl/worksheets/sheet2.xml",
            """<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>4</v></c><c r="B1" t="s"><v>5</v></c></row><row r="2"><c r="A2" t="s"><v>6</v></c><c r="B2"><v>9</v></c></row></sheetData></worksheet>""",
        )
    return output.getvalue()


def _docx_bytes() -> bytes:
    from io import BytesIO

    document_xml = """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>季度总结</w:t></w:r></w:p><w:p><w:r><w:t>第一段内容</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>公司</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>收入</w:t></w:r></w:p></w:tc></w:tr><w:tr><w:tc><w:p><w:r><w:t>A</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>100</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>"""
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\"/>")
        archive.writestr("word/document.xml", document_xml)
    return output.getvalue()


def _runtime(data_root: Path) -> dict:
    return {"_runtime": {"data_root": str(data_root)}}


def test_file_read_excel_returns_header_and_rows(tmp_path):
    file_path = tmp_path / "sample.xlsx"
    file_path.write_bytes(_xlsx_bytes())

    result = run_excel({"file_path": str(file_path)})

    assert result["ok"] is True, result
    assert result["data"] == {
        "header": ["name", "score"],
        "rows": [["A", 3], ["B", 7]],
    }
    assert "tables" not in result["data"]
    assert "render_blocks" not in result["data"]


def test_file_read_csv_returns_header_and_rows(tmp_path):
    payload = "名称\t数量\n甲\t10\n乙\t20\n".encode("gb18030")
    file_path = tmp_path / "sample.tsv"
    file_path.write_bytes(payload)

    result = run_csv({"file_path": str(file_path)})

    assert result["ok"] is True
    assert result["data"] == {
        "header": ["名称", "数量"],
        "rows": [["甲", 10], ["乙", 20]],
    }


def test_file_read_word_extracts_docx_and_document_artifact(tmp_path):
    data_root = tmp_path / "data"
    file_id = _attachment(data_root, "att_docx", "sample.docx", _docx_bytes(), DOCX_MIME)

    result = run_word({"file_id": file_id, "max_chars": 4, **_runtime(data_root)})

    assert result["ok"] is True
    document = result["data"]["document"]
    assert document["headings"] == ["季度总结"]
    assert document["preview_paragraphs"][:2] == ["季度总结", "第一段内容"]
    assert document["table_preview"][0][1] == ["A", "100"]
    assert document["preview_text"] == "季度总结"
    assert document["paragraph_count"] == 2
    assert document["table_count"] == 1
    assert "paragraphs" not in document
    assert "tables" not in document
    assert document["text_artifact_ref"].startswith("runtime://artifact/art_")
    manifest = FileArtifactService(data_root=data_root).read_manifest(document["text_artifact_ref"])
    assert manifest["kind"] == "document"
    assert manifest["physical_format"] == "txt"


def test_file_intake_rejects_paths_and_unsupported_doc(tmp_path):
    data_root = tmp_path / "data"
    missing_path_result = run_csv({"file_path": str(tmp_path / "missing.csv"), **_runtime(data_root)})
    missing_input_result = run_csv({**_runtime(data_root)})
    doc_id = _attachment(data_root, "att_doc", "legacy.doc", b"binary", "application/msword")
    doc_result = run_word({"file_id": doc_id, **_runtime(data_root)})

    assert missing_path_result["ok"] is False
    assert missing_path_result["meta"]["failure_kind"] == "invalid_file_path"
    assert missing_input_result["ok"] is False
    assert missing_input_result["meta"]["failure_kind"] == "missing_required_input"
    assert doc_result["ok"] is False
    assert doc_result["meta"]["failure_kind"] == "unsupported_format"

