from __future__ import annotations

import math
import statistics
from decimal import Decimal
from typing import Sequence

from .contracts import BacktestMetrics, PortfolioSnapshot, Trade, ZERO


def calculate_metrics(
    snapshots: Sequence[PortfolioSnapshot],
    trades: Sequence[Trade],
    *,
    annualization_days: int,
    risk_free_rate: Decimal,
) -> BacktestMetrics:
    equities = [float(item.equity) for item in snapshots]
    if not equities:
        raise ValueError("snapshots must not be empty")
    total_return = equities[-1] / equities[0] - 1.0
    returns = [
        current / previous - 1.0
        for previous, current in zip(equities, equities[1:])
        if previous != 0
    ]

    annualized_return: float | None = None
    if returns and total_return > -1:
        annualized_return = (1.0 + total_return) ** (annualization_days / len(returns)) - 1.0

    annualized_volatility: float | None = None
    sharpe_ratio: float | None = None
    if len(returns) >= 2:
        daily_std = statistics.stdev(returns)
        annualized_volatility = daily_std * math.sqrt(annualization_days)
        if daily_std > 0:
            annual_risk_free = float(risk_free_rate)
            daily_risk_free = (1.0 + annual_risk_free) ** (1.0 / annualization_days) - 1.0
            sharpe_ratio = (
                (statistics.mean(returns) - daily_risk_free)
                / daily_std
                * math.sqrt(annualization_days)
            )

    peak = equities[0]
    max_drawdown = 0.0
    for equity in equities:
        peak = max(peak, equity)
        drawdown = equity / peak - 1.0
        max_drawdown = min(max_drawdown, drawdown)

    average_equity = sum(equities) / len(equities)
    gross_turnover = sum(float(item.gross_value) for item in trades)
    turnover_ratio = gross_turnover / average_equity if average_equity > 0 else 0.0
    return BacktestMetrics(
        total_return=total_return,
        annualized_return=annualized_return,
        annualized_volatility=annualized_volatility,
        sharpe_ratio=sharpe_ratio,
        max_drawdown=max_drawdown,
        turnover_ratio=turnover_ratio,
        trade_count=len(trades),
        total_commission=sum((item.commission for item in trades), ZERO),
        total_tax=sum((item.tax for item in trades), ZERO),
        total_slippage_cost=sum((item.slippage_cost for item in trades), ZERO),
    )
