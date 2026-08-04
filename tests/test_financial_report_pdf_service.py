from io import BytesIO

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
