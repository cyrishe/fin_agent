from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, Protocol

from .contracts import BacktestError, Bar, Order, ZERO, as_decimal


@dataclass(frozen=True)
class ExecutionQuote:
    market_price: Decimal
    fill_price: Decimal
    gross_value: Decimal
    commission: Decimal
    tax: Decimal
    slippage_cost: Decimal
    cash_delta: Decimal


class ExecutionModel(Protocol):
    """Pure deterministic next-open pricing and fee contract."""

    def quote(self, order: Order, bar: Bar, *, quantity: int) -> ExecutionQuote:
        ...

    def describe(self) -> Mapping[str, Any]:
        ...


class BpsExecutionModel:
    """Simple replaceable fee/slippage model; all rates are explicit run inputs."""

    def __init__(
        self,
        *,
        commission_rate: Decimal | int | float | str = ZERO,
        minimum_commission: Decimal | int | float | str = ZERO,
        sell_tax_rate: Decimal | int | float | str = ZERO,
        slippage_rate: Decimal | int | float | str = ZERO,
    ) -> None:
        self.commission_rate = self._rate(commission_rate, "commission_rate")
        self.minimum_commission = as_decimal(
            minimum_commission,
            field_name="minimum_commission",
        )
        if self.minimum_commission < ZERO:
            raise BacktestError("invalid_execution_model", "minimum_commission must not be negative")
        self.sell_tax_rate = self._rate(sell_tax_rate, "sell_tax_rate")
        self.slippage_rate = self._rate(slippage_rate, "slippage_rate")

    def quote(self, order: Order, bar: Bar, *, quantity: int) -> ExecutionQuote:
        if isinstance(quantity, bool) or int(quantity) <= 0:
            raise BacktestError("invalid_fill_quantity", "fill quantity must be positive")
        quantity_decimal = Decimal(int(quantity))
        if order.side == "BUY":
            raw_fill = bar.open * (Decimal("1") + self.slippage_rate)
        elif order.side == "SELL":
            raw_fill = bar.open * (Decimal("1") - self.slippage_rate)
        else:
            raise BacktestError("invalid_order_side", f"unsupported order side: {order.side}")
        fill_price = raw_fill
        gross_value = fill_price * quantity_decimal
        commission = max(gross_value * self.commission_rate, self.minimum_commission)
        tax = gross_value * self.sell_tax_rate if order.side == "SELL" else ZERO
        slippage_cost = abs(fill_price - bar.open) * quantity_decimal
        if order.side == "BUY":
            cash_delta = -(gross_value + commission + tax)
        else:
            cash_delta = gross_value - commission - tax
        return ExecutionQuote(
            market_price=bar.open,
            fill_price=fill_price,
            gross_value=gross_value,
            commission=commission,
            tax=tax,
            slippage_cost=slippage_cost,
            cash_delta=cash_delta,
        )

    def describe(self) -> Mapping[str, Any]:
        return {
            "type": "bps_next_open",
            "commission_rate": str(self.commission_rate),
            "minimum_commission": str(self.minimum_commission),
            "sell_tax_rate": str(self.sell_tax_rate),
            "slippage_rate": str(self.slippage_rate),
            "price_input": "execution_session_open_only",
        }

    def _rate(self, value: Decimal | int | float | str, field_name: str) -> Decimal:
        rate = as_decimal(value, field_name=field_name)
        if rate < ZERO or rate >= Decimal("1"):
            raise BacktestError("invalid_execution_model", f"{field_name} must be in [0, 1)")
        return rate
