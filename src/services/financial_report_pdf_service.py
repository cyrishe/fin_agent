from __future__ import annotations

from dataclasses import dataclass
from html import escape
from io import BytesIO
import os
from pathlib import Path
import re
from typing import Iterable

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


_MARKDOWN_HEADING = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
_MARKDOWN_LIST = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)(.+?)\s*$")
_MARKDOWN_TABLE_DIVIDER = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$"
)
_MARKDOWN_LINK = re.compile(r"\[([^\]]+)]\(([^)]+)\)")
_MARKDOWN_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MARKDOWN_CODE = re.compile(r"`([^`]+)`")


@dataclass(frozen=True)
class PdfReportInput:
    title: str
    report_text: str
    user_question: str = ""
    generated_at: str = ""


class FinancialReportPdfService:
    """Render a persisted finance answer into a deterministic, downloadable PDF."""

    FONT_NAME = "FinAgentCJK"
    FONT_CANDIDATES = (
        "assets/fonts/NotoSansSC-Regular.ttf",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    )

    def __init__(self) -> None:
        if self.FONT_NAME not in pdfmetrics.getRegisteredFontNames():
            font_path = self._font_path()
            pdfmetrics.registerFont(
                TTFont(self.FONT_NAME, str(font_path), subfontIndex=0)
            )

    @classmethod
    def _font_path(cls) -> Path:
        configured = str(os.environ.get("FIN_AGENT_PDF_FONT_PATH") or "").strip()
        candidates = ([configured] if configured else []) + list(cls.FONT_CANDIDATES)
        for candidate in candidates:
            path = Path(candidate).expanduser()
            if path.is_file():
                return path
        raise RuntimeError(
            "缺少可嵌入的中文 PDF 字体；请通过 FIN_AGENT_PDF_FONT_PATH 配置 TTF/TTC 字体。"
        )

    @staticmethod
    def _inline_markup(value: str) -> str:
        text = escape(str(value or "").strip())
        text = _MARKDOWN_LINK.sub(lambda match: escape(match.group(1)), text)
        text = _MARKDOWN_BOLD.sub(r"<b>\1</b>", text)
        text = _MARKDOWN_CODE.sub(r'<font color="#24677A">\1</font>', text)
        return text

    @staticmethod
    def _table_cells(line: str) -> list[str]:
        return [cell.strip() for cell in line.strip().strip("|").split("|")]

    @classmethod
    def _consume_table(
        cls,
        lines: list[str],
        start: int,
    ) -> tuple[list[list[str]], int] | None:
        if start + 1 >= len(lines) or "|" not in lines[start]:
            return None
        if not _MARKDOWN_TABLE_DIVIDER.match(lines[start + 1]):
            return None
        rows = [cls._table_cells(lines[start])]
        cursor = start + 2
        while cursor < len(lines) and "|" in lines[cursor] and lines[cursor].strip():
            rows.append(cls._table_cells(lines[cursor]))
            cursor += 1
        width = max((len(row) for row in rows), default=0)
        if width < 2:
            return None
        return ([row + [""] * (width - len(row)) for row in rows], cursor)

    @staticmethod
    def _iter_paragraphs(lines: Iterable[str]) -> Iterable[str]:
        buffer: list[str] = []
        for line in lines:
            normalized = line.strip()
            if normalized:
                buffer.append(normalized)
            elif buffer:
                yield " ".join(buffer)
                buffer = []
        if buffer:
            yield " ".join(buffer)

    def _styles(self) -> dict[str, ParagraphStyle]:
        base = getSampleStyleSheet()
        return {
            "title": ParagraphStyle(
                "FinanceReportTitle",
                parent=base["Title"],
                fontName=self.FONT_NAME,
                fontSize=21,
                leading=29,
                textColor=colors.HexColor("#102D3E"),
                alignment=TA_LEFT,
                spaceAfter=8,
                wordWrap="CJK",
            ),
            "subtitle": ParagraphStyle(
                "FinanceReportSubtitle",
                parent=base["Normal"],
                fontName=self.FONT_NAME,
                fontSize=8.5,
                leading=13,
                textColor=colors.HexColor("#67808F"),
                spaceAfter=13,
                wordWrap="CJK",
            ),
            "body": ParagraphStyle(
                "FinanceReportBody",
                parent=base["BodyText"],
                fontName=self.FONT_NAME,
                fontSize=10.2,
                leading=17,
                textColor=colors.HexColor("#253D4C"),
                spaceAfter=7,
                wordWrap="CJK",
            ),
            "bullet": ParagraphStyle(
                "FinanceReportBullet",
                parent=base["BodyText"],
                fontName=self.FONT_NAME,
                fontSize=10,
                leading=16,
                leftIndent=12,
                firstLineIndent=-8,
                textColor=colors.HexColor("#253D4C"),
                spaceAfter=4,
                wordWrap="CJK",
            ),
            "quote": ParagraphStyle(
                "FinanceReportQuote",
                parent=base["BodyText"],
                fontName=self.FONT_NAME,
                fontSize=9.4,
                leading=15,
                leftIndent=10,
                borderColor=colors.HexColor("#41ACC3"),
                borderWidth=0,
                borderPadding=(5, 8, 5, 8),
                backColor=colors.HexColor("#EEF8FA"),
                textColor=colors.HexColor("#496473"),
                spaceAfter=7,
                wordWrap="CJK",
            ),
            "table": ParagraphStyle(
                "FinanceReportTable",
                parent=base["BodyText"],
                fontName=self.FONT_NAME,
                fontSize=8.2,
                leading=12,
                textColor=colors.HexColor("#294452"),
                alignment=TA_LEFT,
                wordWrap="CJK",
            ),
            **{
                f"h{level}": ParagraphStyle(
                    f"FinanceReportHeading{level}",
                    parent=base["Heading2"],
                    fontName=self.FONT_NAME,
                    fontSize={1: 16, 2: 14, 3: 12, 4: 11}[level],
                    leading={1: 23, 2: 20, 3: 18, 4: 17}[level],
                    textColor=colors.HexColor("#153C50"),
                    spaceBefore={1: 15, 2: 13, 3: 10, 4: 8}[level],
                    spaceAfter=6,
                    wordWrap="CJK",
                )
                for level in range(1, 5)
            },
        }

    def _header_footer(self, canvas, document) -> None:
        canvas.saveState()
        width, height = A4
        canvas.setStrokeColor(colors.HexColor("#D7E5EA"))
        canvas.setLineWidth(0.45)
        canvas.line(19 * mm, height - 15 * mm, width - 19 * mm, height - 15 * mm)
        canvas.setFont(self.FONT_NAME, 7.5)
        canvas.setFillColor(colors.HexColor("#6F8793"))
        canvas.drawString(19 * mm, height - 11.5 * mm, "FIN AGENT · 个股深度研究")
        canvas.drawRightString(width - 19 * mm, 11 * mm, f"第 {document.page} 页")
        canvas.restoreState()

    def render(self, value: PdfReportInput) -> bytes:
        if not str(value.report_text or "").strip():
            raise ValueError("报告正文不能为空。")
        styles = self._styles()
        output = BytesIO()
        document = BaseDocTemplate(
            output,
            pagesize=A4,
            leftMargin=19 * mm,
            rightMargin=19 * mm,
            topMargin=22 * mm,
            bottomMargin=18 * mm,
            title=str(value.title or "Fin Agent 个股深度研究报告"),
            author="Fin Agent",
            subject="个股深度研究",
        )
        frame = Frame(
            document.leftMargin,
            document.bottomMargin,
            document.width,
            document.height,
            id="report-body",
        )
        document.addPageTemplates(
            [PageTemplate(id="finance-report", frames=[frame], onPage=self._header_footer)]
        )

        story: list[object] = [
            Paragraph(self._inline_markup(value.title), styles["title"]),
            Paragraph(
                self._inline_markup(
                    " · ".join(
                        part
                        for part in (
                            "Fin Agent 自适应个股深度研究",
                            f"生成时间 {value.generated_at}" if value.generated_at else "",
                        )
                        if part
                    )
                ),
                styles["subtitle"],
            ),
        ]
        if value.user_question:
            story.extend(
                [
                    Paragraph(
                        self._inline_markup(f"研究问题：{value.user_question}"),
                        styles["quote"],
                    ),
                    Spacer(1, 2 * mm),
                ]
            )

        lines = str(value.report_text).replace("\r\n", "\n").split("\n")
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            first_heading = _MARKDOWN_HEADING.match(line)
            if (
                first_heading
                and len(first_heading.group(1)) == 1
                and re.sub(r"[*_`]", "", first_heading.group(2)).strip()
                == re.sub(r"[*_`]", "", str(value.title or "")).strip()
            ):
                lines.pop(index)
            break
        cursor = 0
        paragraph_buffer: list[str] = []

        def flush_paragraphs() -> None:
            nonlocal paragraph_buffer
            for paragraph in self._iter_paragraphs(paragraph_buffer):
                story.append(Paragraph(self._inline_markup(paragraph), styles["body"]))
            paragraph_buffer = []

        while cursor < len(lines):
            line = lines[cursor]
            table_data = self._consume_table(lines, cursor)
            if table_data:
                flush_paragraphs()
                rows, cursor = table_data
                wrapped = [
                    [Paragraph(self._inline_markup(cell), styles["table"]) for cell in row]
                    for row in rows
                ]
                column_width = document.width / len(rows[0])
                table = Table(wrapped, colWidths=[column_width] * len(rows[0]), repeatRows=1)
                table.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EAF4F7")),
                            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#153C50")),
                            ("GRID", (0, 0), (-1, -1), 0.45, colors.HexColor("#D6E3E8")),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("LEFTPADDING", (0, 0), (-1, -1), 5),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                            ("TOPPADDING", (0, 0), (-1, -1), 5),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                        ]
                    )
                )
                story.extend([table, Spacer(1, 3 * mm)])
                continue

            heading = _MARKDOWN_HEADING.match(line)
            list_item = _MARKDOWN_LIST.match(line)
            stripped = line.strip()
            if heading:
                flush_paragraphs()
                level = min(4, len(heading.group(1)))
                story.append(
                    KeepTogether(
                        [Paragraph(self._inline_markup(heading.group(2)), styles[f"h{level}"])]
                    )
                )
            elif list_item:
                flush_paragraphs()
                story.append(
                    Paragraph(
                        f"• {self._inline_markup(list_item.group(1))}",
                        styles["bullet"],
                    )
                )
            elif stripped.startswith(">"):
                flush_paragraphs()
                story.append(
                    Paragraph(self._inline_markup(stripped.lstrip("> ")), styles["quote"])
                )
            elif stripped in {"---", "***", "___"}:
                flush_paragraphs()
                story.append(Spacer(1, 2 * mm))
            else:
                paragraph_buffer.append(line)
            cursor += 1
        flush_paragraphs()
        document.build(story)
        return output.getvalue()
