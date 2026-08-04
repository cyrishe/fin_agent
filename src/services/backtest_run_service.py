from __future__ import annotations

from typing import Any, Mapping

from src.backtest import BacktestError
from src.services.buy_and_hold_backtest_service import BuyAndHoldBacktestService


class BacktestRunService:
    """Transport-neutral entry point for the simple fixed-basket backtest."""

    def __init__(
        self,
        *,
        buy_and_hold_service: BuyAndHoldBacktestService | None = None,
    ) -> None:
        self.buy_and_hold_service = buy_and_hold_service or BuyAndHoldBacktestService()

    def run(self, request_payload: Mapping[str, Any]) -> dict[str, Any]:
        holdings = request_payload.get("holdings")
        if not isinstance(holdings, list) or not holdings:
            raise BacktestError("invalid_stocks", "请至少提供一只股票。")

        stocks: list[str] = []
        weights: list[Any] = []
        supplied: list[bool] = []
        for item in holdings:
            if isinstance(item, Mapping):
                stock = str(item.get("stock") or "").strip()
                has_weight = item.get("weight") not in (None, "")
                weight = item.get("weight")
            else:
                stock = str(item or "").strip()
                has_weight = False
                weight = None
            if not stock:
                raise BacktestError("invalid_stocks", "股票名称或代码不能为空。")
            stocks.append(stock)
            supplied.append(has_weight)
            weights.append(weight)

        if any(supplied) and not all(supplied):
            raise BacktestError(
                "incomplete_weights",
                "部分股票缺少权重；请为全部股票提供权重，或全部留空使用等权。",
            )

        payload = {
            "stocks": stocks,
            "start_date": request_payload.get("start_date"),
            "end_date": request_payload.get("end_date"),
            "initial_cash": request_payload.get("initial_cash"),
            **({"weights": weights} if all(supplied) else {}),
        }
        result = self.buy_and_hold_service.run(payload)
        return {
            "ok": True,
            "backtest_type": "fixed_basket",
            **result,
        }
