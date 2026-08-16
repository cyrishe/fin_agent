from __future__ import annotations

import pytest

from src.services.backtest_benchmark_service import BacktestBenchmarkService


class _IndexMarket:
    def __init__(self, *, fail_subject=""):
        self.fail_subject = fail_subject
        self.calls = []

    def query(self, *, subject, start, end, limit):
        self.calls.append(
            {"subject": subject, "start": start, "end": end, "limit": limit}
        )
        if subject == self.fail_subject:
            raise RuntimeError("provider unavailable")
        identities = {
            "沪深300": ("000300.SH", "沪深300", [100, 103, 102, 108]),
            "上证指数": ("000001.SH", "上证指数", [100, 99, 101, 104]),
            "创业板指": ("399006.SZ", "创业板指", [100, 105, 103, 112]),
            "000300.SH": ("000300.SH", "沪深300", [100, 103, 102, 108]),
        }
        code, name, closes = identities[subject]
        dates = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]
        return {
            "index_code": code,
            "index_short_name": name,
            "rows": [
                {"trade_date": date, "close": close}
                for date, close in zip(dates, closes)
            ],
        }


def _backtest_result():
    return {
        "equity_curve": [
            {"date": "2026-01-05", "value": "100000"},
            {"date": "2026-01-06", "value": "102000"},
            {"date": "2026-01-07", "value": "101000"},
            {"date": "2026-01-08", "value": "112000"},
        ],
        "evidence": {"run_fingerprint": "sha256:strategy"},
    }


def test_benchmark_defaults_to_csi300_and_computes_relative_metrics():
    market = _IndexMarket()
    result = BacktestBenchmarkService(index_market_service=market).analyze(
        _backtest_result()
    )

    assert market.calls == [
        {
            "subject": "沪深300",
            "start": "2026-01-05",
            "end": "2026-01-08",
            "limit": 1000,
        }
    ]
    primary = result["primary_benchmark"]
    assert primary["code"] == "000300.SH"
    assert primary["source"] == "default"
    assert primary["normalized_curve"][0]["value"] == 100.0
    assert result["summary"]["portfolio_total_return"] == pytest.approx(0.12)
    assert round(result["summary"]["benchmark_total_return"], 8) == 0.08
    assert round(result["summary"]["excess_return"], 8) == 0.04
    assert result["summary"]["tracking_error"] is not None
    assert result["evidence"]["comparison_fingerprint"].startswith("sha256:")


def test_user_primary_and_cc_context_benchmarks_are_kept_distinct_and_deduplicated():
    market = _IndexMarket()
    result = BacktestBenchmarkService(index_market_service=market).analyze(
        _backtest_result(),
        primary_subject="上证指数",
        context_benchmarks=[
            {"subject": "创业板指", "reason": "组合主要持有创业板股票"},
            {"subject": "上证指数", "reason": "重复项"},
            {"subject": "沪深300", "reason": "超过最多两个的项目"},
        ],
    )

    assert result["primary_benchmark"]["name"] == "上证指数"
    assert result["primary_benchmark"]["source"] == "user_specified"
    assert [item["name"] for item in result["contextual_benchmarks"]] == [
        "创业板指"
    ]
    assert result["contextual_benchmarks"][0]["reason"] == "组合主要持有创业板股票"


def test_unavailable_primary_does_not_discard_completed_backtest():
    result = BacktestBenchmarkService(
        index_market_service=_IndexMarket(fail_subject="沪深300")
    ).analyze(_backtest_result())

    assert result["primary_benchmark"] is None
    assert result["summary"] == {}
    assert "数据源查询失败" in result["warnings"][0]
    assert result["series"][0]["kind"] == "strategy"


def test_benchmark_with_no_overlapping_dates_is_reported_as_unavailable():
    class _NoOverlap:
        def query(self, **_kwargs):
            return {
                "index_code": "000300.SH",
                "index_short_name": "沪深300",
                "rows": [
                    {"trade_date": "2025-01-01", "close": 100},
                    {"trade_date": "2025-01-02", "close": 101},
                ],
            }

    result = BacktestBenchmarkService(index_market_service=_NoOverlap()).analyze(
        _backtest_result()
    )

    assert result["primary_benchmark"] is None
    assert "没有足够行情" in result["warnings"][0]
