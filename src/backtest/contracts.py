from __future__ import annotations

import dataclasses
import datetime as dt
import json
import math
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Mapping, Sequence


ENGINE_VERSION = "backtest_core.v1"
ZERO = Decimal("0")
ONE = Decimal("1")


class BacktestError(ValueError):
    """A stable, caller-visible failure from the deterministic backtest core."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.details = dict(details or {})


def as_decimal(value: Decimal | int | float | str, *, field_name: str) -> Decimal:
    if isinstance(value, bool):
        raise BacktestError("invalid_number", f"{field_name} must be numeric")
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception as exc:
        raise BacktestError("invalid_number", f"{field_name} must be numeric") from exc
    if not number.is_finite():
        raise BacktestError("invalid_number", f"{field_name} must be finite")
    return number


def as_positive_int(value: Any, *, field_name: str) -> int:
    number = as_decimal(value, field_name=field_name)
    if number != number.to_integral_value() or number <= ZERO:
        raise BacktestError("invalid_integer", f"{field_name} must be a positive integer")
    return int(number)


def as_date(value: dt.date | str | None, *, field_name: str) -> dt.date | None:
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value))
    except Exception as exc:
        raise BacktestError("invalid_date", f"{field_name} must be an ISO date") from exc


def _normalized_symbol(value: str) -> str:
    symbol = str(value or "").strip().upper()
    if not symbol:
        raise BacktestError("invalid_symbol", "symbol must not be empty")
    return symbol


@dataclass(frozen=True)
class Instrument:
    symbol: str
    lot_size: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _normalized_symbol(self.symbol))
        object.__setattr__(
            self,
            "lot_size",
            as_positive_int(self.lot_size, field_name="lot_size"),
        )


@dataclass(frozen=True)
class Bar:
    date: dt.date
    symbol: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None = None
    tradable: bool = True
    can_buy: bool = True
    can_sell: bool = True
    status_reason: str = ""

    def __post_init__(self) -> None:
        parsed_date = as_date(self.date, field_name="bar.date")
        assert parsed_date is not None
        object.__setattr__(self, "date", parsed_date)
        object.__setattr__(self, "symbol", _normalized_symbol(self.symbol))
        for field_name in ("open", "high", "low", "close"):
            number = as_decimal(getattr(self, field_name), field_name=f"bar.{field_name}")
            if number <= ZERO:
                raise BacktestError("invalid_bar", f"bar.{field_name} must be positive")
            object.__setattr__(self, field_name, number)
        if self.high < max(self.open, self.close, self.low):
            raise BacktestError("invalid_bar", "bar.high is inconsistent with OHLC")
        if self.low > min(self.open, self.close, self.high):
            raise BacktestError("invalid_bar", "bar.low is inconsistent with OHLC")
        if self.volume is not None:
            volume = as_decimal(self.volume, field_name="bar.volume")
            if volume < ZERO:
                raise BacktestError("invalid_bar", "bar.volume must not be negative")
            object.__setattr__(self, "volume", volume)
        for field_name in ("tradable", "can_buy", "can_sell"):
            if not isinstance(getattr(self, field_name), bool):
                raise BacktestError(
                    "invalid_boolean",
                    f"bar.{field_name} must be a boolean",
                )
        object.__setattr__(self, "status_reason", str(self.status_reason or "").strip())


@dataclass(frozen=True)
class TargetPortfolio:
    """A complete target-weight snapshot. Missing holdings target zero."""

    weights: Mapping[str, Decimal]
    reason: str = ""
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        normalized: dict[str, Decimal] = {}
        for raw_symbol, raw_weight in self.weights.items():
            symbol = _normalized_symbol(raw_symbol)
            if symbol in normalized:
                raise BacktestError("duplicate_target_symbol", f"duplicate target symbol: {symbol}")
            weight = as_decimal(raw_weight, field_name=f"target.weights.{symbol}")
            if weight < ZERO:
                raise BacktestError("negative_target_weight", "target weights must be non-negative")
            normalized[symbol] = weight
        total = sum(normalized.values(), ZERO)
        if total > ONE:
            raise BacktestError(
                "target_weight_exceeds_one",
                "target weights must sum to at most 1",
                details={"total_weight": str(total)},
            )
        object.__setattr__(
            self,
            "weights",
            MappingProxyType(dict(sorted(normalized.items()))),
        )
        object.__setattr__(self, "reason", str(self.reason or "").strip())
        object.__setattr__(self, "evidence", MappingProxyType(dict(self.evidence or {})))


@dataclass(frozen=True)
class BacktestConfig:
    universe: Sequence[str]
    initial_cash: Decimal
    start_date: dt.date | None = None
    end_date: dt.date | None = None
    annualization_days: int = 252
    risk_free_rate: Decimal = ZERO

    def __post_init__(self) -> None:
        universe = tuple(sorted({_normalized_symbol(symbol) for symbol in self.universe}))
        if not universe:
            raise BacktestError("empty_universe", "universe must contain at least one symbol")
        object.__setattr__(self, "universe", universe)
        cash = as_decimal(self.initial_cash, field_name="initial_cash")
        if cash <= ZERO:
            raise BacktestError("invalid_initial_cash", "initial_cash must be positive")
        object.__setattr__(self, "initial_cash", cash)
        start = as_date(self.start_date, field_name="start_date")
        end = as_date(self.end_date, field_name="end_date")
        if start is not None and end is not None and start > end:
            raise BacktestError("invalid_date_range", "start_date must not be after end_date")
        object.__setattr__(self, "start_date", start)
        object.__setattr__(self, "end_date", end)
        object.__setattr__(
            self,
            "annualization_days",
            as_positive_int(self.annualization_days, field_name="annualization_days"),
        )
        risk_free = as_decimal(self.risk_free_rate, field_name="risk_free_rate")
        if risk_free <= Decimal("-1"):
            raise BacktestError("invalid_risk_free_rate", "risk_free_rate must be greater than -1")
        object.__setattr__(self, "risk_free_rate", risk_free)


@dataclass(frozen=True)
class PositionSnapshot:
    symbol: str
    quantity: int
    average_cost: Decimal
    mark_price: Decimal
    market_value: Decimal
    actual_weight: Decimal
    mark_is_stale: bool = False


@dataclass(frozen=True)
class PortfolioSnapshot:
    date: dt.date
    cash: Decimal
    equity: Decimal
    positions: tuple[PositionSnapshot, ...]
    realized_pnl: Decimal = ZERO

    def quantity(self, symbol: str) -> int:
        normalized = _normalized_symbol(symbol)
        for position in self.positions:
            if position.symbol == normalized:
                return position.quantity
        return 0


@dataclass(frozen=True)
class DecisionRecord:
    decision_id: str
    date: dt.date
    execute_on: dt.date
    data_cutoff: str
    target_weights: Mapping[str, Decimal]
    reason: str
    evidence: Mapping[str, Any]


@dataclass(frozen=True)
class Order:
    order_id: str
    decision_id: str
    signal_date: dt.date
    execution_date: dt.date
    symbol: str
    side: str
    quantity: int
    reference_price: Decimal
    target_weight: Decimal


@dataclass(frozen=True)
class Trade:
    trade_id: str
    order_id: str
    date: dt.date
    symbol: str
    side: str
    requested_quantity: int
    filled_quantity: int
    market_price: Decimal
    fill_price: Decimal
    gross_value: Decimal
    commission: Decimal
    tax: Decimal
    slippage_cost: Decimal
    cash_delta: Decimal


@dataclass(frozen=True)
class ExecutionIssue:
    date: dt.date
    code: str
    message: str
    symbol: str = ""
    order_id: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BacktestMetrics:
    total_return: float
    annualized_return: float | None
    annualized_volatility: float | None
    sharpe_ratio: float | None
    max_drawdown: float
    turnover_ratio: float
    trade_count: int
    total_commission: Decimal
    total_tax: Decimal
    total_slippage_cost: Decimal


@dataclass(frozen=True)
class BacktestResult:
    engine_version: str
    run_fingerprint: str
    data_fingerprint: str
    strategy: Mapping[str, Any]
    execution_model: Mapping[str, Any]
    config: BacktestConfig
    assumptions: tuple[str, ...]
    decisions: tuple[DecisionRecord, ...]
    orders: tuple[Order, ...]
    trades: tuple[Trade, ...]
    issues: tuple[ExecutionIssue, ...]
    daily_snapshots: tuple[PortfolioSnapshot, ...]
    metrics: BacktestMetrics

    def to_dict(self) -> dict[str, Any]:
        return json_value(self)


def json_value(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {
            field_info.name: json_value(getattr(value, field_info.name))
            for field_info in dataclasses.fields(value)
        }
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [json_value(item) for item in value]
        return sorted(
            items,
            key=lambda item: json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise BacktestError(
        "non_serializable_value",
        f"unsupported result value type: {type(value).__name__}",
    )
