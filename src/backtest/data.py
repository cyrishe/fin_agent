from __future__ import annotations

import datetime as dt
import hashlib
import json
from decimal import Decimal
from typing import Iterable, Protocol, Sequence

from .contracts import BacktestError, Bar, Instrument, as_date, as_positive_int


class MarketData(Protocol):
    @property
    def calendar(self) -> tuple[dt.date, ...]:
        ...

    @property
    def symbols(self) -> tuple[str, ...]:
        ...

    def instrument(self, symbol: str) -> Instrument:
        ...

    def bar(self, symbol: str, date: dt.date) -> Bar | None:
        ...

    def fingerprint(self) -> str:
        ...


class InMemoryMarketData:
    """Deterministic fixture data source used by the standalone engine and tests."""

    def __init__(
        self,
        bars: Iterable[Bar],
        *,
        instruments: Iterable[Instrument] | None = None,
        calendar: Sequence[dt.date | str] | None = None,
        source_name: str = "in_memory",
    ) -> None:
        bar_index: dict[tuple[str, dt.date], Bar] = {}
        symbols: set[str] = set()
        for bar in bars:
            if not isinstance(bar, Bar):
                raise BacktestError("invalid_market_data", "bars must contain Bar objects")
            key = (bar.symbol, bar.date)
            if key in bar_index:
                raise BacktestError(
                    "duplicate_bar",
                    f"duplicate bar for {bar.symbol} on {bar.date.isoformat()}",
                )
            bar_index[key] = bar
            symbols.add(bar.symbol)

        instrument_index: dict[str, Instrument] = {}
        for item in (
            instruments
            if instruments is not None
            else (Instrument(symbol=symbol) for symbol in symbols)
        ):
            if not isinstance(item, Instrument):
                raise BacktestError(
                    "invalid_market_data",
                    "instruments must contain Instrument objects",
                )
            if item.symbol in instrument_index:
                raise BacktestError(
                    "duplicate_instrument",
                    f"duplicate instrument: {item.symbol}",
                )
            instrument_index[item.symbol] = item
        missing_instruments = symbols.difference(instrument_index)
        if missing_instruments:
            raise BacktestError(
                "missing_instrument",
                "every bar symbol must have an instrument",
                details={"symbols": sorted(missing_instruments)},
            )

        if calendar is None:
            normalized_calendar = tuple(sorted({bar.date for bar in bar_index.values()}))
        else:
            parsed_dates = {
                parsed
                for raw_date in calendar
                if (parsed := as_date(raw_date, field_name="calendar.date")) is not None
            }
            normalized_calendar = tuple(sorted(parsed_dates))
            off_calendar = sorted(
                {
                    bar.date
                    for bar in bar_index.values()
                    if bar.date not in set(normalized_calendar)
                }
            )
            if off_calendar:
                raise BacktestError(
                    "bar_outside_calendar",
                    "all bars must fall on the explicit trading calendar",
                    details={"dates": [item.isoformat() for item in off_calendar]},
                )
        if not normalized_calendar:
            raise BacktestError("empty_calendar", "market data calendar must not be empty")

        self._bars = bar_index
        self._instruments = dict(sorted(instrument_index.items()))
        self._calendar = normalized_calendar
        self._symbols = tuple(sorted(instrument_index))
        self._source_name = str(source_name or "in_memory")

    @property
    def calendar(self) -> tuple[dt.date, ...]:
        return self._calendar

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._symbols

    def instrument(self, symbol: str) -> Instrument:
        normalized = str(symbol or "").strip().upper()
        try:
            return self._instruments[normalized]
        except KeyError as exc:
            raise BacktestError("unknown_symbol", f"unknown instrument: {normalized}") from exc

    def bar(self, symbol: str, date: dt.date) -> Bar | None:
        return self._bars.get((str(symbol or "").strip().upper(), date))

    def fingerprint(self) -> str:
        payload = {
            "source": self._source_name,
            "calendar": [date.isoformat() for date in self._calendar],
            "instruments": [
                {
                    "symbol": item.symbol,
                    "lot_size": item.lot_size,
                }
                for item in self._instruments.values()
            ],
            "bars": [
                {
                    "symbol": bar.symbol,
                    "date": bar.date.isoformat(),
                    "open": str(bar.open),
                    "high": str(bar.high),
                    "low": str(bar.low),
                    "close": str(bar.close),
                    "volume": None if bar.volume is None else str(bar.volume),
                    "tradable": bar.tradable,
                    "can_buy": bar.can_buy,
                    "can_sell": bar.can_sell,
                    "status_reason": bar.status_reason,
                }
                for _, bar in sorted(self._bars.items())
            ],
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


class MarketView:
    """Read-only point-in-time view that makes future bars unaddressable."""

    _ALLOWED_FIELDS = {"open", "high", "low", "close", "volume"}

    def __init__(
        self,
        data: MarketData,
        *,
        current_date: dt.date,
        universe: Sequence[str],
    ) -> None:
        if current_date not in data.calendar:
            raise BacktestError("date_not_in_calendar", "current_date is not a trading session")
        self._data = data
        self._current_date = current_date
        self._visible_dates = tuple(date for date in data.calendar if date <= current_date)
        self._universe = tuple(sorted(str(symbol).strip().upper() for symbol in universe))

    @property
    def current_date(self) -> dt.date:
        return self._current_date

    @property
    def dates(self) -> tuple[dt.date, ...]:
        return self._visible_dates

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._universe

    def current(self, symbol: str) -> Bar | None:
        return self._data.bar(self._checked_symbol(symbol), self._current_date)

    def bar(self, symbol: str, date: dt.date | str) -> Bar | None:
        parsed_date = as_date(date, field_name="market_view.date")
        assert parsed_date is not None
        if parsed_date > self._current_date:
            raise BacktestError(
                "future_data_access",
                "strategy attempted to access market data after its decision cutoff",
            )
        if parsed_date not in self._visible_dates:
            return None
        return self._data.bar(self._checked_symbol(symbol), parsed_date)

    def history(
        self,
        symbol: str,
        *,
        field: str = "close",
        lookback: int | None = None,
    ) -> tuple[tuple[dt.date, Decimal], ...]:
        normalized_symbol = self._checked_symbol(symbol)
        if field not in self._ALLOWED_FIELDS:
            raise BacktestError("unsupported_bar_field", f"unsupported bar field: {field}")
        if lookback is not None:
            lookback = as_positive_int(lookback, field_name="lookback")
        values: list[tuple[dt.date, Decimal]] = []
        for date in self._visible_dates:
            bar = self._data.bar(normalized_symbol, date)
            if bar is None:
                continue
            value = getattr(bar, field)
            if value is not None:
                values.append((date, value))
        if lookback is not None:
            values = values[-lookback:]
        return tuple(values)

    def _checked_symbol(self, symbol: str) -> str:
        normalized = str(symbol or "").strip().upper()
        if normalized not in self._universe:
            raise BacktestError("symbol_outside_universe", f"symbol is outside strategy universe: {normalized}")
        return normalized
