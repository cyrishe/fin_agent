from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any, Mapping, Protocol

from .contracts import (
    BacktestError,
    PortfolioSnapshot,
    TargetPortfolio,
    ZERO,
    as_decimal,
    as_date,
    as_positive_int,
)
from .data import MarketView


class Strategy(Protocol):
    def reset(self) -> None:
        ...

    def on_close(
        self,
        market: MarketView,
        portfolio: PortfolioSnapshot,
    ) -> TargetPortfolio | None:
        ...

    def describe(self) -> Mapping[str, Any]:
        ...


class FixedWeightStrategy:
    """Buy-and-hold by default, or repeat the same target every N decisions."""

    def __init__(
        self,
        weights: Mapping[str, Decimal | int | float | str],
        *,
        rebalance_every: int | None = None,
    ) -> None:
        self._target = TargetPortfolio(
            weights={
                symbol: as_decimal(weight, field_name=f"weights.{symbol}")
                for symbol, weight in weights.items()
            },
            reason="fixed target weights",
        )
        self._rebalance_every = (
            None
            if rebalance_every is None
            else as_positive_int(rebalance_every, field_name="rebalance_every")
        )
        self._decision_count = 0

    def reset(self) -> None:
        self._decision_count = 0

    def on_close(
        self,
        market: MarketView,
        portfolio: PortfolioSnapshot,
    ) -> TargetPortfolio | None:
        should_emit = self._decision_count == 0 or (
            self._rebalance_every is not None
            and self._decision_count % self._rebalance_every == 0
        )
        self._decision_count += 1
        return self._target if should_emit else None

    def describe(self) -> Mapping[str, Any]:
        return {
            "type": "fixed_weight",
            "weights": {symbol: str(weight) for symbol, weight in self._target.weights.items()},
            "rebalance_every": self._rebalance_every,
        }


class ScheduledTargetStrategy:
    """Explicit signal-date targets, useful for external selectors and allocators."""

    def __init__(
        self,
        schedule: Mapping[
            dt.date | str,
            Mapping[str, Decimal | int | float | str] | TargetPortfolio,
        ],
    ) -> None:
        normalized: dict[dt.date, TargetPortfolio] = {}
        for raw_date, raw_target in schedule.items():
            date = as_date(raw_date, field_name="schedule.date")
            assert date is not None
            if date in normalized:
                raise BacktestError("duplicate_schedule_date", f"duplicate target date: {date}")
            normalized[date] = (
                raw_target
                if isinstance(raw_target, TargetPortfolio)
                else TargetPortfolio(
                    weights={
                        symbol: as_decimal(weight, field_name=f"weights.{symbol}")
                        for symbol, weight in raw_target.items()
                    },
                    reason="scheduled target",
                )
            )
        self._schedule = dict(sorted(normalized.items()))

    def reset(self) -> None:
        return None

    def on_close(
        self,
        market: MarketView,
        portfolio: PortfolioSnapshot,
    ) -> TargetPortfolio | None:
        return self._schedule.get(market.current_date)

    def describe(self) -> Mapping[str, Any]:
        return {
            "type": "scheduled_target",
            "schedule": {
                date.isoformat(): {
                    symbol: str(weight)
                    for symbol, weight in target.weights.items()
                }
                for date, target in self._schedule.items()
            },
        }


class TopNMomentumStrategy:
    """Small reference strategy proving dynamic selection uses only visible history."""

    def __init__(
        self,
        *,
        lookback_days: int,
        top_n: int,
        rebalance_every: int = 1,
        require_positive: bool = False,
    ) -> None:
        self._lookback_days = as_positive_int(lookback_days, field_name="lookback_days")
        self._top_n = as_positive_int(top_n, field_name="top_n")
        self._rebalance_every = as_positive_int(
            rebalance_every,
            field_name="rebalance_every",
        )
        self._require_positive = bool(require_positive)
        self._decision_count = 0

    def reset(self) -> None:
        self._decision_count = 0

    def on_close(
        self,
        market: MarketView,
        portfolio: PortfolioSnapshot,
    ) -> TargetPortfolio | None:
        should_rebalance = self._decision_count % self._rebalance_every == 0
        self._decision_count += 1
        if not should_rebalance:
            return None

        scores: list[tuple[Decimal, str]] = []
        required_points = self._lookback_days + 1
        for symbol in market.symbols:
            history = market.history(symbol, field="close", lookback=required_points)
            if len(history) < required_points:
                continue
            if tuple(date for date, _ in history) != market.dates[-required_points:]:
                continue
            start = history[0][1]
            end = history[-1][1]
            score = end / start - Decimal("1")
            if self._require_positive and score <= ZERO:
                continue
            scores.append((score, symbol))
        if not scores:
            return None

        selected = sorted(scores, key=lambda item: (-item[0], item[1]))[: self._top_n]
        weight = Decimal("1") / Decimal(len(selected))
        return TargetPortfolio(
            weights={symbol: weight for _, symbol in selected},
            reason=f"top {len(selected)} by {self._lookback_days}-session momentum",
            evidence={
                "scores": {
                    symbol: str(score)
                    for score, symbol in selected
                }
            },
        )

    def describe(self) -> Mapping[str, Any]:
        return {
            "type": "top_n_momentum",
            "lookback_days": self._lookback_days,
            "top_n": self._top_n,
            "rebalance_every": self._rebalance_every,
            "require_positive": self._require_positive,
        }
