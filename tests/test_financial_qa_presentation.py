from __future__ import annotations

import pytest

from src.scenarios.financial_qa.presentation import FinancialQaPresentationService


def _schema(*columns: tuple[str, str]) -> dict:
    return {
        "columns": [
            {"name": name, "type": column_type}
            for name, column_type in columns
        ]
    }


def test_single_quote_preserves_narrative_and_uses_real_values_as_metrics():
    service = FinancialQaPresentationService()
    message = "贵州茅台最新报 1319.81 元，涨幅 2.35%。"

    blocks = service.build(
        message,
        [
            {
                "api": "stock.quote",
                "goal": "查询贵州茅台最新行情",
                "row_count": 1,
                "sample_complete": True,
                "schema": _schema(
                    ("code", "string"),
                    ("name", "string"),
                    ("tradedate", "string"),
                    ("open", "number"),
                    ("close", "number"),
                    ("high", "number"),
                    ("low", "number"),
                    ("pct", "number"),
                    ("amount", "number"),
                ),
                "sample": {
                    "rows": [
                        {
                            "code": "600519",
                            "name": "贵州茅台",
                            "tradedate": "2026-07-28",
                            "open": 1319.28,
                            "close": 1319.81,
                            "high": 1320.0,
                            "low": 1289.52,
                            "pct": 2.350523,
                            "amount": 355687656.0,
                        }
                    ]
                },
            }
        ],
    )

    assert blocks[0]["content"] == message
    assert blocks[0]["payload"]["text"] == message
    assert [block["block_type"] for block in blocks] == ["narrative", "data"]
    assert blocks[1]["semantic"] == "finance.quote.metrics"
    assert blocks[1]["payload"]["shape"] == "record"
    assert blocks[1]["payload"]["data"]["items"] == [
        {"id": "close", "label": "收盘价", "value": 1319.81, "unit": "元"},
        {"id": "pct", "label": "涨跌幅", "value": 2.350523, "unit": "%"},
        {"id": "amount", "label": "成交额", "value": 355687656.0, "unit": "元"},
        {"id": "open", "label": "开盘价", "value": 1319.28, "unit": "元"},
        {"id": "high", "label": "最高价", "value": 1320.0, "unit": "元"},
        {"id": "low", "label": "最低价", "value": 1289.52, "unit": "元"},
    ]
    assert all(
        block.get("semantic") not in {"finance.ohlcv", "finance.intraday"}
        for block in blocks
    )


def test_multiple_margin_rows_become_one_ordered_table_without_value_changes():
    service = FinancialQaPresentationService()
    rows = [
        {
            "code": "300750.SZ",
            "name": "宁德时代",
            "tradedate": "2026-07-27",
            "fin_balance": 9123456789.0,
            "fin_net_buy": -12345678.0,
        },
        {
            "code": "002594.SZ",
            "name": "比亚迪",
            "tradedate": "2026-07-27",
            "fin_balance": 8123456789.0,
            "fin_net_buy": 23456789.0,
        },
    ]

    blocks = service.build(
        "宁德时代融资余额更高，但当日为融资净偿还。",
        [
            {
                "api": "stock.margin",
                "goal": "比较两只股票最新融资数据",
                "row_count": 2,
                "sample_complete": True,
                "schema": _schema(
                    ("code", "string"),
                    ("name", "string"),
                    ("tradedate", "string"),
                    ("fin_balance", "number"),
                    ("fin_net_buy", "number"),
                ),
                "sample": {"rows": rows},
            }
        ],
    )

    assert [block["block_type"] for block in blocks] == ["narrative", "data"]
    table = blocks[1]
    assert table["semantic"] == "finance.margin.records"
    assert table["payload"]["shape"] == "records"
    assert table["payload"]["data"]["rows"] == rows
    assert table["payload"]["data"]["row_count"] == 2
    assert table["meta"] == {
        "row_count": 2,
        "sample_row_count": 2,
        "sample_complete": True,
    }


def test_report_record_stays_a_table_and_preserves_null_and_long_text():
    service = FinancialQaPresentationService()
    report_row = {
        "code": "688981",
        "name": "中芯国际",
        "report_date": "2026-02-25T16:00:00",
        "institution": "中邮证券",
        "rating": "买入",
        "investment_highlights": "产业链切换迭代效应持续，产能利用率保持高位。",
        "target_price_lower": None,
    }

    blocks = service.build(
        "近半年当前数据源收录 1 份研报。",
        [
            {
                "api": "stock.report",
                "goal": "查询中芯国际最近半年的研报",
                "row_count": 1,
                "schema": _schema(
                    ("code", "string"),
                    ("name", "string"),
                    ("report_date", "string"),
                    ("institution", "string"),
                    ("rating", "string"),
                    ("investment_highlights", "string"),
                    ("target_price_lower", "unknown"),
                ),
                "sample": {"rows": [report_row]},
            }
        ],
    )

    assert blocks[1]["semantic"] == "finance.research.records"
    assert blocks[1]["payload"]["shape"] == "records"
    assert blocks[1]["payload"]["data"]["rows"] == [report_row]
    assert blocks[1]["payload"]["data"]["rows"][0]["target_price_lower"] is None


@pytest.mark.parametrize(
    ("api", "expected_semantic", "expected_renderer"),
    [
        ("stock.quote", "finance.ohlcv", "finance.kline"),
        ("stock.intraday", "finance.intraday", "finance.intraday"),
    ],
)
def test_complete_ohlc_time_series_uses_finance_chart(
    api: str,
    expected_semantic: str,
    expected_renderer: str,
):
    service = FinancialQaPresentationService()
    rows = [
        {
            "tradedate": "2026-07-27",
            "open": 10.0,
            "high": 11.0,
            "low": 9.8,
            "close": 10.8,
            "volumn": 1000,
        },
        {
            "tradedate": "2026-07-28",
            "open": 10.8,
            "high": 11.4,
            "low": 10.6,
            "close": 11.2,
            "volumn": 1200,
        },
    ]

    blocks = service.build(
        "价格连续两个交易日上涨。",
        [
            {
                "api": api,
                "goal": "查看价格走势",
                "row_count": 2,
                "schema": _schema(
                    ("tradedate", "string"),
                    ("open", "number"),
                    ("high", "number"),
                    ("low", "number"),
                    ("close", "number"),
                    ("volumn", "number"),
                ),
                "sample": {"rows": rows},
            }
        ],
    )

    chart = blocks[1]
    assert chart["semantic"] == expected_semantic
    assert chart["payload"]["shape"] == "timeseries"
    assert chart["presentation_hint"]["preferred_renderer"] == expected_renderer
    assert chart["payload"]["data"]["candles"] == [
        {
            "time": "2026-07-27",
            "open": 10.0,
            "high": 11.0,
            "low": 9.8,
            "close": 10.8,
            "volume": 1000,
        },
        {
            "time": "2026-07-28",
            "open": 10.8,
            "high": 11.4,
            "low": 10.6,
            "close": 11.2,
            "volume": 1200,
        },
    ]


def test_partial_ohlc_rows_fall_back_to_table_instead_of_fabricating_chart():
    service = FinancialQaPresentationService()
    rows = [
        {"tradedate": "2026-07-27", "open": 10.0, "high": 11.0, "close": 10.8},
        {"tradedate": "2026-07-28", "open": 10.8, "high": 11.4, "close": 11.2},
    ]

    blocks = service.build(
        "当前结果没有最低价字段。",
        [
            {
                "api": "stock.quote",
                "goal": "查看价格记录",
                "row_count": 2,
                "schema": _schema(
                    ("tradedate", "string"),
                    ("open", "number"),
                    ("high", "number"),
                    ("close", "number"),
                ),
                "sample": {"rows": rows},
            }
        ],
    )

    assert blocks[1]["semantic"] == "finance.quote.records"
    assert blocks[1]["payload"]["shape"] == "records"
    assert blocks[1]["payload"]["data"]["rows"] == rows


def test_empty_or_invalid_evidence_keeps_only_the_narrative():
    service = FinancialQaPresentationService()
    message = "当前条件下没有查到记录。"

    blocks = service.build(
        message,
        [
            {
                "api": "stock.report",
                "goal": "查询研报",
                "row_count": 0,
                "schema": _schema(("report_date", "string")),
                "sample": {"rows": []},
            },
            {"sample": {"rows": [None, "not-a-row"]}},
        ],
    )

    assert blocks == [
        {
            "block_id": "financial_qa_answer",
            "block_type": "narrative",
            "kind": "narrative",
            "semantic": "finance.answer",
            "mode": "replace",
            "content": message,
            "payload": {"format": "markdown", "text": message},
        }
    ]


def test_structured_evidence_removes_duplicate_markdown_table_but_keeps_explanation():
    service = FinancialQaPresentationService()
    message = """## 融资数据对比

| 股票 | 融资余额 |
|---|---:|
| 比亚迪 | 126.63 亿元 |

**比亚迪融资情绪相对更强。** 当日净偿还规模更小。"""

    blocks = service.build(
        message,
        [{
            "api": "stock.margin",
            "goal": "查询融资数据",
            "row_count": 1,
            "sample_complete": True,
            "schema": _schema(
                ("code", "string"),
                ("financing_balance", "number"),
            ),
            "sample": {
                "rows": [{
                    "code": "002594.SZ",
                    "financing_balance": 12_663_000_000,
                }]
            },
        }],
    )

    assert "|" not in blocks[0]["content"]
    assert "融资数据对比" not in blocks[0]["content"]
    assert "比亚迪融资情绪相对更强" in blocks[0]["content"]
