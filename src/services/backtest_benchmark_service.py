from __future__ import annotations

import hashlib
import json
import math
import statistics
from typing import Any, Mapping, Sequence

from src.services.kingdomai_index_daily_market_service import (
    KingdomaiIndexDailyMarketService,
)


class BacktestBenchmarkService:
    """Compare one completed backtest with independent index reference series."""

    DEFAULT_PRIMARY = "沪深300"
    MAX_CONTEXT_BENCHMARKS = 2
    MAX_QUERY_ROWS = 1000
    ANNUALIZATION_DAYS = 252

    def __init__(
        self,
        *,
        index_market_service: KingdomaiIndexDailyMarketService | None = None,
    ) -> None:
        self.index_market_service = (
            index_market_service or KingdomaiIndexDailyMarketService()
        )

    def analyze(
        self,
        backtest_result: Mapping[str, Any],
        *,
        primary_subject: str = "",
        context_benchmarks: Sequence[Mapping[str, Any] | str] | None = None,
    ) -> dict[str, Any]:
        portfolio_values = self._portfolio_values(backtest_result)
        primary = str(primary_subject or "").strip() or self.DEFAULT_PRIMARY
        primary_source = "user_specified" if str(primary_subject or "").strip() else "default"
        requested = [
            {
                "subject": primary,
                "role": "primary",
                "source": primary_source,
                "reason": (
                    "用户指定的主要比较基准"
                    if primary_source == "user_specified"
                    else "A 股组合默认大盘基准"
                ),
            },
            *self._context_requests(context_benchmarks),
        ]
        warnings: list[str] = []
        benchmarks: list[dict[str, Any]] = []
        seen_codes: set[str] = set()

        if len(portfolio_values) < 2:
            warnings.append("组合净值不足两个有效交易日，无法计算基准比较。")
        else:
            start_date = portfolio_values[0][0]
            end_date = portfolio_values[-1][0]
            for item in requested:
                subject = str(item.get("subject") or "").strip()
                if not subject:
                    continue
                try:
                    payload = self.index_market_service.query(
                        subject=subject,
                        start=start_date,
                        end=end_date,
                        limit=self.MAX_QUERY_ROWS,
                    )
                    comparison = self._comparison(
                        portfolio_values=portfolio_values,
                        market_payload=payload,
                        request=item,
                    )
                except Exception:
                    warnings.append(f"基准“{subject}”的数据源查询失败，已跳过该基准。")
                    continue
                if comparison is None:
                    warnings.append(f"基准“{subject}”在实际回测区间没有足够行情。")
                    continue
                code = str(comparison.get("code") or "").strip().upper()
                if code and code in seen_codes:
                    continue
                if code:
                    seen_codes.add(code)
                benchmarks.append(comparison)
                if comparison.get("coverage_warning"):
                    warnings.append(str(comparison["coverage_warning"]))

        primary_benchmark = next(
            (item for item in benchmarks if item.get("role") == "primary"),
            None,
        )
        contextual = [
            item for item in benchmarks if item.get("role") == "contextual"
        ]
        portfolio_series = self._normalized_series(portfolio_values)
        fingerprint_payload = {
            "backtest_run_fingerprint": (
                backtest_result.get("evidence", {}).get("run_fingerprint")
                if isinstance(backtest_result.get("evidence"), Mapping)
                else ""
            ),
            "benchmarks": [
                {
                    "code": item.get("code"),
                    "data_fingerprint": item.get("data_fingerprint"),
                    "role": item.get("role"),
                }
                for item in benchmarks
            ],
        }
        return {
            "primary_benchmark": primary_benchmark,
            "contextual_benchmarks": contextual,
            "summary": (
                dict(primary_benchmark.get("relative_metrics") or {})
                if isinstance(primary_benchmark, Mapping)
                else {}
            ),
            "series": [
                {
                    "series_id": "portfolio",
                    "name": "组合",
                    "kind": "strategy",
                    "normalized_curve": portfolio_series,
                },
                *[
                    {
                        "series_id": f"benchmark:{item.get('code') or item.get('subject')}",
                        "name": item.get("name") or item.get("subject"),
                        "kind": "benchmark",
                        "role": item.get("role"),
                        "normalized_curve": item.get("normalized_curve") or [],
                    }
                    for item in benchmarks
                ],
            ],
            "warnings": warnings,
            "assumptions": [
                "基准是独立指数价格序列，不进入组合交易账本，也不计交易费用。",
                "组合与基准均从各自比较区间首个有效值归一化为 100。",
                "当前使用价格指数与未复权股票行情，尚未形成包含分红和公司行动的全收益口径。",
            ],
            "evidence": {
                "data_source": "kingdomai.kcrp_index_price",
                "comparison_fingerprint": self._fingerprint(fingerprint_payload),
            },
        }

    def _context_requests(
        self,
        value: Sequence[Mapping[str, Any] | str] | None,
    ) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        for raw in list(value or [])[: self.MAX_CONTEXT_BENCHMARKS]:
            if isinstance(raw, Mapping):
                subject = str(raw.get("subject") or "").strip()
                reason = str(raw.get("reason") or "").strip()
            else:
                subject = str(raw or "").strip()
                reason = "根据组合持仓特征补充的相关指数"
            if not subject:
                continue
            items.append(
                {
                    "subject": subject,
                    "role": "contextual",
                    "source": "cc_selected",
                    "reason": reason or "根据组合持仓特征补充的相关指数",
                }
            )
        return items

    def _comparison(
        self,
        *,
        portfolio_values: list[tuple[str, float]],
        market_payload: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        benchmark_values = self._benchmark_values(market_payload)
        portfolio_by_date = dict(portfolio_values)
        benchmark_by_date = dict(benchmark_values)
        common_dates = sorted(set(portfolio_by_date) & set(benchmark_by_date))
        if len(common_dates) < 2:
            return None
        aligned_portfolio = [portfolio_by_date[date] for date in common_dates]
        aligned_benchmark = [benchmark_by_date[date] for date in common_dates]
        portfolio_returns = self._returns(aligned_portfolio)
        benchmark_returns = self._returns(aligned_benchmark)
        excess_returns = [
            portfolio_return - benchmark_return
            for portfolio_return, benchmark_return in zip(
                portfolio_returns,
                benchmark_returns,
            )
        ]
        tracking_error = None
        information_ratio = None
        if len(excess_returns) >= 2:
            daily_tracking_error = statistics.stdev(excess_returns)
            tracking_error = daily_tracking_error * math.sqrt(
                self.ANNUALIZATION_DAYS
            )
            if daily_tracking_error > 0:
                information_ratio = (
                    statistics.mean(excess_returns)
                    / daily_tracking_error
                    * math.sqrt(self.ANNUALIZATION_DAYS)
                )
        portfolio_total_return = aligned_portfolio[-1] / aligned_portfolio[0] - 1
        benchmark_total_return = aligned_benchmark[-1] / aligned_benchmark[0] - 1
        requested_start = portfolio_values[0][0]
        requested_end = portfolio_values[-1][0]
        actual_start = common_dates[0]
        actual_end = common_dates[-1]
        name = str(market_payload.get("index_short_name") or "").strip()
        code = str(market_payload.get("index_code") or "").strip().upper()
        subject = str(request.get("subject") or "").strip()
        coverage_warning = ""
        if actual_start != requested_start or actual_end != requested_end:
            coverage_warning = (
                f"基准“{name or subject}”的可比较区间为 {actual_start} 至 {actual_end}，"
                "与组合完整区间不完全一致。"
            )
        return {
            "subject": subject,
            "code": code,
            "name": name or subject,
            "role": request.get("role"),
            "source": request.get("source"),
            "reason": request.get("reason"),
            "period": {
                "actual_start": actual_start,
                "actual_end": actual_end,
                "trading_days": len(common_dates),
            },
            "relative_metrics": {
                "portfolio_total_return": portfolio_total_return,
                "benchmark_total_return": benchmark_total_return,
                "excess_return": portfolio_total_return - benchmark_total_return,
                "portfolio_max_drawdown": self._max_drawdown(aligned_portfolio),
                "benchmark_max_drawdown": self._max_drawdown(aligned_benchmark),
                "tracking_error": tracking_error,
                "information_ratio": information_ratio,
            },
            "normalized_curve": self._normalized_series(
                list(zip(common_dates, aligned_benchmark))
            ),
            "data_fingerprint": self._fingerprint(
                {
                    "code": code,
                    "rows": [
                        {"date": date, "close": benchmark_by_date[date]}
                        for date in common_dates
                    ],
                }
            ),
            "coverage_warning": coverage_warning,
        }

    @staticmethod
    def _portfolio_values(result: Mapping[str, Any]) -> list[tuple[str, float]]:
        values: list[tuple[str, float]] = []
        for row in result.get("equity_curve") or []:
            if not isinstance(row, Mapping):
                continue
            date = str(row.get("date") or "").strip()
            try:
                value = float(row.get("value"))
            except (TypeError, ValueError):
                continue
            if date and math.isfinite(value) and value > 0:
                values.append((date, value))
        return sorted(dict(values).items())

    @staticmethod
    def _benchmark_values(payload: Mapping[str, Any]) -> list[tuple[str, float]]:
        values: list[tuple[str, float]] = []
        for row in payload.get("rows") or []:
            if not isinstance(row, Mapping):
                continue
            date = str(row.get("trade_date") or "").strip()
            try:
                close = float(row.get("close"))
            except (TypeError, ValueError):
                continue
            if date and math.isfinite(close) and close > 0:
                values.append((date, close))
        return sorted(dict(values).items())

    @staticmethod
    def _returns(values: Sequence[float]) -> list[float]:
        return [
            current / previous - 1
            for previous, current in zip(values, values[1:])
            if previous > 0
        ]

    @staticmethod
    def _max_drawdown(values: Sequence[float]) -> float:
        peak = values[0]
        drawdown = 0.0
        for value in values:
            peak = max(peak, value)
            drawdown = min(drawdown, value / peak - 1)
        return drawdown

    @staticmethod
    def _normalized_series(values: Sequence[tuple[str, float]]) -> list[dict[str, Any]]:
        if not values or values[0][1] <= 0:
            return []
        base = values[0][1]
        return [
            {"date": date, "value": round(value / base * 100, 8)}
            for date, value in values
        ]

    @staticmethod
    def _fingerprint(value: Any) -> str:
        raw = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
