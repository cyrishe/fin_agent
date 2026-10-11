from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable, Mapping, Sequence

import pymysql

from src.backtest import (
    BacktestConfig,
    BacktestEngine,
    BacktestError,
    Bar,
    BpsExecutionModel,
    FixedWeightStrategy,
    InMemoryMarketData,
    Instrument,
)
from src.services.kingdomai_stock_kline_service import KingdomaiStockKlineService
from src.utils.mysql_utils import StockInfoDbUtils


DbFactory = Callable[[], Any]


@dataclass(frozen=True)
class ResolvedBacktestStock:
    requested: str
    code: str
    name: str
    row_count: int
    missing_session_count: int


@dataclass(frozen=True)
class LoadedAshareMarketData:
    market_data: InMemoryMarketData
    stocks: tuple[ResolvedBacktestStock, ...]
    requested_start: dt.date
    requested_end: dt.date
    effective_start: dt.date
    effective_end: dt.date
    session_count: int
    warnings: tuple[str, ...]


class AshareDailyMarketDataLoader:
    """Load a small A-share daily-bar universe for the portfolio backtest core."""

    MARKET_CODE = "CN_A"
    MAX_KLINE_ROWS = 1000

    def __init__(
        self,
        *,
        kline_service: KingdomaiStockKlineService | None = None,
        db_factory: DbFactory | None = None,
    ) -> None:
        self.db_factory = db_factory or StockInfoDbUtils
        self.kline_service = kline_service or KingdomaiStockKlineService(
            db_factory=self.db_factory
        )

    def load(
        self,
        *,
        subjects: Sequence[str],
        start_date: dt.date,
        end_date: dt.date,
    ) -> LoadedAshareMarketData:
        calendar = self._load_calendar(start_date=start_date, end_date=end_date)
        if len(calendar) < 2:
            raise BacktestError(
                "insufficient_market_data",
                "所选区间内至少需要两个交易日。",
                details={"session_count": len(calendar)},
            )

        calendar_set = set(calendar)
        bars_by_symbol: dict[str, dict[dt.date, Bar]] = {}
        identities: list[dict[str, Any]] = []
        invalid_rows: Counter[str] = Counter()
        limit_price_rows = 0

        for subject in subjects:
            payload = self.kline_service.query_daily(
                subject=subject,
                start=start_date.isoformat(),
                end=end_date.isoformat(),
                limit=self.MAX_KLINE_ROWS,
            )
            symbol = str(payload.get("stock_code") or "").strip().upper()
            if not symbol:
                raise BacktestError(
                    "stock_not_found",
                    f"没有找到股票“{subject}”。",
                    details={"subject": subject},
                )
            if symbol in bars_by_symbol:
                raise BacktestError(
                    "duplicate_stock",
                    f"股票“{subject}”与已选股票指向同一标的 {symbol}。",
                    details={"symbol": symbol},
                )

            symbol_bars: dict[dt.date, Bar] = {}
            for row in payload.get("rows") or []:
                if not isinstance(row, Mapping):
                    invalid_rows[symbol] += 1
                    continue
                bar = self._to_bar(symbol=symbol, row=row, calendar=calendar_set)
                if bar is None:
                    invalid_rows[symbol] += 1
                    continue
                if bar.date in symbol_bars:
                    raise BacktestError(
                        "duplicate_bar",
                        f"duplicate bar for {symbol} on {bar.date.isoformat()}",
                        details={
                            "symbol": symbol,
                            "trade_date": bar.date.isoformat(),
                        },
                    )
                symbol_bars[bar.date] = bar
                if bool(row.get("is_limit_price")):
                    limit_price_rows += 1

            if not symbol_bars:
                raise BacktestError(
                    "stock_history_unavailable",
                    f"股票 {symbol} 在所选区间没有可用日线。",
                    details={"symbol": symbol},
                )
            bars_by_symbol[symbol] = symbol_bars
            identities.append(
                {
                    "requested": str(subject).strip(),
                    "code": symbol,
                    "name": str(payload.get("stock_name") or "").strip(),
                }
            )

        common_dates = [
            date
            for date in calendar
            if all(date in bars_by_symbol[symbol] for symbol in bars_by_symbol)
        ]
        if not common_dates:
            raise BacktestError(
                "no_common_start_date",
                "所选股票在该区间没有共同可用的起始交易日。",
            )

        effective_start = common_dates[0]
        effective_calendar = tuple(date for date in calendar if date >= effective_start)
        if len(effective_calendar) < 2:
            raise BacktestError(
                "insufficient_market_data",
                "共同可用的起始日之后不足两个交易日。",
                details={"effective_start": effective_start.isoformat()},
            )

        effective_set = set(effective_calendar)
        bars = [
            bar
            for symbol_bars in bars_by_symbol.values()
            for date, bar in symbol_bars.items()
            if date in effective_set
        ]
        stocks = tuple(
            ResolvedBacktestStock(
                requested=identity["requested"],
                code=identity["code"],
                name=identity["name"],
                row_count=sum(
                    1
                    for date in effective_calendar
                    if date in bars_by_symbol[identity["code"]]
                ),
                missing_session_count=sum(
                    1
                    for date in effective_calendar
                    if date not in bars_by_symbol[identity["code"]]
                ),
            )
            for identity in identities
        )

        warnings: list[str] = []
        if effective_start != calendar[0]:
            warnings.append(
                f"所选股票在首个交易日没有完整行情，实际从 {effective_start.isoformat()} 开始。"
            )
        for stock in stocks:
            if stock.missing_session_count:
                warnings.append(
                    f"{stock.name or stock.code} 有 {stock.missing_session_count} 个交易日缺少日线，持仓估值会沿用最近收盘价。"
                )
            if invalid_rows[stock.code]:
                warnings.append(
                    f"{stock.name or stock.code} 有 {invalid_rows[stock.code]} 条无效日线未计入。"
                )
        if limit_price_rows:
            warnings.append(
                "数据包含涨跌停日；首版只有日线标记，不据此推断开盘是否一定能成交。"
            )

        market_data = InMemoryMarketData(
            bars,
            instruments=(Instrument(symbol=stock.code, lot_size=100) for stock in stocks),
            calendar=effective_calendar,
            source_name="kingdomai.kcrp_stock_price.raw.v1",
        )
        return LoadedAshareMarketData(
            market_data=market_data,
            stocks=stocks,
            requested_start=start_date,
            requested_end=end_date,
            effective_start=effective_start,
            effective_end=effective_calendar[-1],
            session_count=len(effective_calendar),
            warnings=tuple(warnings),
        )

    def _load_calendar(
        self,
        *,
        start_date: dt.date,
        end_date: dt.date,
    ) -> tuple[dt.date, ...]:
        db = self.db_factory()
        try:
            conn = getattr(db, "conn", db)
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT calendar_date
                    FROM aiia_trade_calendar
                    WHERE market_code = %s
                      AND is_trade_day = 1
                      AND calendar_date BETWEEN %s AND %s
                    ORDER BY calendar_date ASC
                    """,
                    (self.MARKET_CODE, start_date, end_date),
                )
                rows = cursor.fetchall()
        finally:
            close_db = getattr(db, "close_db", None)
            if callable(close_db):
                close_db()

        dates: list[dt.date] = []
        for row in rows:
            value = row.get("calendar_date") if isinstance(row, Mapping) else row[0]
            if isinstance(value, dt.datetime):
                value = value.date()
            elif not isinstance(value, dt.date):
                try:
                    value = dt.date.fromisoformat(str(value))
                except (TypeError, ValueError):
                    continue
            dates.append(value)
        return tuple(dates)

    @staticmethod
    def _to_bar(
        *,
        symbol: str,
        row: Mapping[str, Any],
        calendar: set[dt.date],
    ) -> Bar | None:
        try:
            date = dt.date.fromisoformat(str(row.get("trade_date") or ""))
            if date not in calendar:
                return None
            open_price = Decimal(str(row.get("open")))
            high_price = Decimal(str(row.get("high")))
            low_price = Decimal(str(row.get("low")))
            close_price = Decimal(str(row.get("close")))
            volume = Decimal(str(row.get("volume") or "0"))
            if any(
                value <= 0 or not value.is_finite()
                for value in (open_price, high_price, low_price, close_price)
            ):
                return None
            if volume < 0 or not volume.is_finite():
                return None
            tradable = volume > 0
            return Bar(
                date=date,
                symbol=symbol,
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
                volume=volume,
                tradable=tradable,
                can_buy=tradable,
                can_sell=tradable,
                status_reason="" if tradable else "当日成交量为零",
            )
        except (BacktestError, InvalidOperation, TypeError, ValueError):
            return None


class BuyAndHoldBacktestService:
    """Consumer-facing fixed-basket buy-and-hold backtest."""

    DEFAULT_INITIAL_CASH = Decimal("100000")
    MAX_STOCKS = 10
    MAX_PERIOD_DAYS = 1096

    def __init__(
        self,
        *,
        loader: AshareDailyMarketDataLoader | None = None,
        engine: BacktestEngine | None = None,
    ) -> None:
        self.loader = loader or AshareDailyMarketDataLoader()
        self.engine = engine or BacktestEngine()

    def run(self, request_payload: Mapping[str, Any]) -> dict[str, Any]:
        subjects = self._subjects(request_payload.get("stocks"))
        start_date = self._date(request_payload.get("start_date"), field="start_date")
        end_date = self._date(request_payload.get("end_date"), field="end_date")
        if start_date > end_date:
            raise BacktestError("invalid_date_range", "开始日期不能晚于结束日期。")
        if (end_date - start_date).days > self.MAX_PERIOD_DAYS:
            raise BacktestError(
                "period_too_long",
                "首版单次回测最长支持三年。",
                details={"max_days": self.MAX_PERIOD_DAYS},
            )

        initial_cash = self._initial_cash(request_payload.get("initial_cash"))
        loaded = self.loader.load(
            subjects=subjects,
            start_date=start_date,
            end_date=end_date,
        )
        requested_weights = self._weights(
            request_payload.get("weights"),
            stock_count=len(subjects),
        )
        if requested_weights is None:
            weight = Decimal("1") / Decimal(len(loaded.stocks))
            weights = {stock.code: weight for stock in loaded.stocks}
            allocation = "equal_weight"
        else:
            weights = {
                stock.code: requested_weights[index]
                for index, stock in enumerate(loaded.stocks)
            }
            allocation = "specified_weight"
        config = BacktestConfig(
            universe=list(weights),
            initial_cash=initial_cash,
            start_date=loaded.effective_start,
            end_date=loaded.effective_end,
        )
        result = self.engine.run(
            data=loaded.market_data,
            strategy=FixedWeightStrategy(weights),
            config=config,
            execution_model=BpsExecutionModel(
                commission_rate="0.0003",
                minimum_commission="5",
                sell_tax_rate="0.0005",
                slippage_rate="0.0005",
            ),
        )
        return self._present(
            result=result,
            loaded=loaded,
            allocation=allocation,
        )

    def _subjects(self, value: Any) -> tuple[str, ...]:
        if not isinstance(value, list):
            raise BacktestError("invalid_stocks", "stocks 必须是股票名称或代码组成的列表。")
        subjects = tuple(str(item or "").strip() for item in value)
        if not subjects or any(not item for item in subjects):
            raise BacktestError("invalid_stocks", "请至少选择一只有效股票。")
        if len(subjects) > self.MAX_STOCKS:
            raise BacktestError(
                "too_many_stocks",
                f"首版一次最多回测 {self.MAX_STOCKS} 只股票。",
            )
        if len(set(subjects)) != len(subjects):
            raise BacktestError("duplicate_stock", "股票列表中有重复项。")
        return subjects

    @staticmethod
    def _date(value: Any, *, field: str) -> dt.date:
        try:
            return dt.date.fromisoformat(str(value or ""))
        except (TypeError, ValueError) as exc:
            label = "开始日期" if field == "start_date" else "结束日期"
            raise BacktestError("invalid_date", f"{label}必须是 YYYY-MM-DD 格式。") from exc

    def _initial_cash(self, value: Any) -> Decimal:
        if value in (None, ""):
            return self.DEFAULT_INITIAL_CASH
        try:
            cash = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise BacktestError("invalid_initial_cash", "初始资金必须是有效数字。") from exc
        if not cash.is_finite() or cash <= 0:
            raise BacktestError("invalid_initial_cash", "初始资金必须大于零。")
        return cash

    @staticmethod
    def _weights(value: Any, *, stock_count: int) -> tuple[Decimal, ...] | None:
        if value in (None, ""):
            return None
        if not isinstance(value, list) or len(value) != stock_count:
            raise BacktestError(
                "invalid_weights",
                "weights 必须与 stocks 一一对应；不提供时系统默认等权。",
            )
        weights: list[Decimal] = []
        for raw in value:
            try:
                weight = Decimal(str(raw))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise BacktestError("invalid_weights", "每个权重都必须是有效数字。") from exc
            if not weight.is_finite() or weight <= 0:
                raise BacktestError("invalid_weights", "每个权重都必须大于零。")
            weights.append(weight)
        total = sum(weights, Decimal("0"))
        if total > Decimal("1"):
            raise BacktestError(
                "weight_sum_exceeds_one",
                "权重合计不能超过 1；首版不会自动加入杠杆。",
                details={"total_weight": str(total)},
            )
        return tuple(weights)

    def _present(
        self,
        *,
        result: Any,
        loaded: LoadedAshareMarketData,
        allocation: str,
    ) -> dict[str, Any]:
        first_snapshot = result.daily_snapshots[0]
        final_snapshot = result.daily_snapshots[-1]
        profit_loss = final_snapshot.equity - first_snapshot.equity
        stock_by_code = {stock.code: stock for stock in loaded.stocks}
        warnings = list(loaded.warnings)
        issue_counts = Counter(issue.code for issue in result.issues)
        issue_labels = {
            "cash_constrained": "因整手、费用或可用现金限制而缩减买入",
            "missing_bar": "执行日缺少行情，订单未成交",
            "missing_reference_bar": "信号日缺少行情，未生成订单",
            "not_tradable": "执行日不可交易，订单未成交",
            "buy_blocked": "执行日无法买入，订单未成交",
            "sell_blocked": "执行日无法卖出，订单未成交",
            "stale_mark": "缺少当日行情，沿用最近收盘价估值",
            "target_below_lot": "分配资金不足一手，未能买入",
            "lot_rounding_no_order": "目标持仓与当前持仓相差不足一手，未生成订单",
        }
        for code, count in sorted(issue_counts.items()):
            warnings.append(f"{issue_labels.get(code, code)}：{count} 次。")
        if not result.trades:
            warnings.append("本次没有产生成交，请检查资金是否足够买入一手以及首个执行日是否可交易。")

        return {
            "strategy": {
                "name": "买入并持有",
                "allocation": allocation,
                "description": (
                    "所选股票按用户给定权重买入，持有到回测结束，中途不主动调仓。"
                    if allocation == "specified_weight"
                    else "所选股票等权买入，持有到回测结束，中途不主动调仓。"
                ),
            },
            "period": {
                "requested_start": loaded.requested_start.isoformat(),
                "requested_end": loaded.requested_end.isoformat(),
                "actual_start": loaded.effective_start.isoformat(),
                "actual_end": loaded.effective_end.isoformat(),
                "trading_days": loaded.session_count,
            },
            "stocks": [
                {
                    "requested": stock.requested,
                    "code": stock.code,
                    "name": stock.name,
                    "target_weight": float(
                        result.decisions[0].target_weights.get(stock.code, Decimal("0"))
                        if result.decisions
                        else Decimal("0")
                    ),
                }
                for stock in loaded.stocks
            ],
            "summary": {
                "initial_cash": self._money(first_snapshot.equity),
                "final_value": self._money(final_snapshot.equity),
                "profit_loss": self._money(profit_loss),
                "total_return": result.metrics.total_return,
                "max_drawdown": result.metrics.max_drawdown,
                "trade_count": result.metrics.trade_count,
            },
            "equity_curve": [
                {"date": snapshot.date.isoformat(), "value": self._money(snapshot.equity)}
                for snapshot in result.daily_snapshots
            ],
            "trades": [
                {
                    "date": trade.date.isoformat(),
                    "code": trade.symbol,
                    "name": stock_by_code[trade.symbol].name,
                    "action": "买入" if trade.side == "BUY" else "卖出",
                    "shares": trade.filled_quantity,
                    "price": self._money(trade.fill_price),
                    "amount": self._money(trade.gross_value),
                    "fee": self._money(trade.commission + trade.tax),
                }
                for trade in result.trades
            ],
            "ending_holdings": [
                {
                    "code": position.symbol,
                    "name": stock_by_code[position.symbol].name,
                    "shares": position.quantity,
                    "market_value": self._money(position.market_value),
                    "weight": float(position.actual_weight),
                }
                for position in final_snapshot.positions
            ],
            "warnings": warnings,
            "assumptions": [
                (
                    "在首个交易日收盘确定目标权重，下一个交易日开盘买入，之后不主动卖出或调仓。"
                    if allocation == "specified_weight"
                    else "在首个交易日收盘确定等权组合，下一个交易日开盘买入，之后不主动卖出或调仓。"
                ),
                "A 股股票按 100 股一手向下取整；未用完的资金保留为现金。",
                "使用未复权日线；首版不处理分红、送转、配股和退市结算，因此结果只用于验证回测流程。",
                "演示成本口径为佣金万三、最低 5 元、卖出税费万五、单边滑点万五，不代表用户实际券商费率。",
                "停牌以成交量为零识别；涨跌停开盘可成交性、成交量容量和 A 股 T+1 尚未精确建模。",
            ],
            "evidence": {
                "engine_version": result.engine_version,
                "data_source": "kingdomai.kcrp_stock_price + aiia_trade_calendar",
                "data_fingerprint": result.data_fingerprint,
                "run_fingerprint": result.run_fingerprint,
            },
        }

    @staticmethod
    def _money(value: Decimal) -> str:
        return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
