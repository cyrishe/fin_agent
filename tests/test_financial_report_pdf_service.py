from io import BytesIO
import base64

from pypdf import PdfReader

from src.services.financial_report_pdf_service import (
    FinancialReportPdfService,
    PdfReportInput,
)


def test_financial_report_pdf_renders_chinese_markdown_and_table() -> None:
    report = """# 贵州茅台深度研究

## 核心判断

截至 **2026-07-31**，公司长期品牌壁垒仍在，但当前估值需要盈利兑现。

- 支持事实：高端白酒需求仍具韧性。
- 反面事实：渠道库存变化需要继续验证。

| 指标 | 当前值 | 观察条件 |
| --- | --- | --- |
| 收盘价 | 1,338 元 | 仅代表所示时点 |
| 核心风险 | 需求放缓 | 若连续两个报告期恶化则重估 |

> 本报告是研究信息整理，不构成投资建议。
"""
    content = FinancialReportPdfService().render(
        PdfReportInput(
            title="贵州茅台深度研究",
            report_text=report,
            user_question="请深度分析贵州茅台",
            generated_at="2026-08-03 12:00:00",
        )
    )

    assert content.startswith(b"%PDF-")
    reader = PdfReader(BytesIO(content))
    assert len(reader.pages) >= 1
    assert reader.metadata.title == "贵州茅台深度研究"


def test_financial_report_pdf_uses_matching_h1_as_body_boundary() -> None:
    report = """证据已经齐备，现在开始综合报告。

---

# 贵州茅台深度研究

## 核心判断

这是应当保留的正式报告正文。
"""
    content = FinancialReportPdfService().render(
        PdfReportInput(
            title="贵州茅台深度研究",
            report_text=report,
        )
    )

    extracted = "\n".join(
        page.extract_text() or "" for page in PdfReader(BytesIO(content)).pages
    )
    assert extracted.count("贵州茅台深度研究") == 1
    assert "这是应当保留的正式报告正文" in extracted
    assert "证据已经齐备" not in extracted


def test_financial_report_pdf_headings_stay_with_following_content() -> None:
    styles = FinancialReportPdfService()._styles()
    assert all(styles[f"h{level}"].keepWithNext for level in range(1, 5))


def test_pdf_embeds_the_saved_figure_in_the_same_section():
    from PIL import Image
    image = BytesIO()
    Image.new("RGB", (700, 250), "blue").save(image, format="PNG")
    payload = FinancialReportPdfService().render(PdfReportInput(
        title="图文验证", report_text="## 技术面\n\n![同源日K](finance-figure:fig_a)\n\n当前仍低于MA5。",
        figures=[{"id": "fig_a", "image_url": "data:image/png;base64," + base64.b64encode(image.getvalue()).decode(),
                  "code": "000001.SZ", "as_of": "2026-09-29"}],
    ))
    reader = PdfReader(BytesIO(payload))
    assert sum(len(page.images) for page in reader.pages) == 1
    text = "\n".join(page.extract_text() for page in reader.pages)
    assert "同源日K" in text and "2026-09-29" in text
    assert "finance-figure:" not in text
