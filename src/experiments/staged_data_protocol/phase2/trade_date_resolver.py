from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Mapping, Sequence, Tuple

from src.experiments.staged_data_protocol.phase2.models import ApiCall
from src.utils.mysql_utils import StockInfoDbUtils


TRADE_CALENDAR_TABLE = "aiia_trade_calendar"
DEFAULT_MARKET_CODE = "CN_A"

_DATE_FIELDS = {"date", "tradedate", "trade_date", "start", "end", "start_date", "end_date", "as_of", "asof"}
_DATE_VALUE_RE = re.compile(r"^(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{8}|-?\d+|latest|current|today)$", re.IGNORECASE)
_FILTER_DATE_RE = re.compile(
    r"(?P<field>\btradedate\b|\btrade_date\b)\s*"
    r"(?P<op>=|==|<=|>=|<|>)\s*"
    r"(?P<value>'[^']+'|\"[^\"]+\"|-?\d+|\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{8})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TradeDateAdjustment:
    field: str
    requested: str
    resolved: str

    def warning(self) -> str:
        return (
            f"请求日期 {self.requested} 不是交易日，已使用前一个交易日 "
            f"{self.resolved} 的数据。"
        )


@dataclass(frozen=True)
class TradeDateResolution:
    call: ApiCall
    adjustments: Tuple[TradeDateAdjustment, ...] = ()

    @property
    def warnings(self) -> List[str]:
        return [item.warning() for item in self.adjustments]


class TradeDateResolutionError(RuntimeError):
    """The authoritative trade calendar could not resolve a requested date."""


class TradeDateResolver:
    """Normalize market-data dates against ``aiia_trade_calendar``.

    This is intentionally a narrow execution boundary.  It handles market-data
    APIs only; report periods, announcement dates, and event dates remain owned
    by their respective providers.
    """

    _MARKET_VIEWS = {"quote", "moneyflow", "margin", "pricevalue", "constitution"}

    def __init__(
        self,
        *,
        database: str = "kingdomai",
        db_factory: Callable[..., Any] = StockInfoDbUtils,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.database = database
        self.db_factory = db_factory
        self.today = today

    def should_resolve(self, api: str) -> bool:
        parts = str(api or "").split(".")
        return bool(set(parts) & self._MARKET_VIEWS) or "dynamic_cal" in parts

    def resolve(self, call: ApiCall) -> TradeDateResolution:
        if not self.should_resolve(call.api):
            return TradeDateResolution(call=call)

        raw_args = dict(call.args)
        candidates = self._collect_candidates(raw_args)
        if not candidates:
            return TradeDateResolution(call=call)

        market_code = str(raw_args.get("market_code") or raw_args.get("market") or DEFAULT_MARKET_CODE).strip()
        anchor = self._anchor_date(raw_args)
        trade_days = self._load_trade_days(market_code=market_code, through=max([anchor, *self._exact_dates(candidates)]))
        if not trade_days:
            raise TradeDateResolutionError(
                f"{TRADE_CALENDAR_TABLE} has no trade days for market_code={market_code}"
            )

        adjustments: List[TradeDateAdjustment] = []
        normalized = dict(raw_args)
        for key in _DATE_FIELDS & normalized.keys():
            value = normalized.get(key)
            if value in (None, "") or not self._is_date_value(value):
                continue
            resolved, adjustment = self._resolve_value(
                value,
                field=key,
                anchor=anchor,
                trade_days=trade_days,
            )
            normalized[key] = resolved
            if adjustment is not None:
                adjustments.append(adjustment)

        filter_text = str(normalized.get("filter") or "")
        if filter_text:
            normalized_filter = self._rewrite_filter(
                filter_text,
                anchor=anchor,
                trade_days=trade_days,
                adjustments=adjustments,
            )
            normalized["filter"] = normalized_filter

        return TradeDateResolution(
            call=ApiCall(
                result_id=call.result_id,
                api=call.api,
                args=normalized,
                outputs=list(call.outputs),
                raw=call.raw,
            ),
            adjustments=tuple(adjustments),
        )

    def _collect_candidates(self, args: Mapping[str, Any]) -> List[str]:
        values = [str(args[key]).strip() for key in _DATE_FIELDS if key in args and args[key] not in (None, "")]
        filter_text = str(args.get("filter") or "")
        values.extend(match.group("value").strip("'\"") for match in _FILTER_DATE_RE.finditer(filter_text))
        return [item for item in values if self._is_date_value(item)]

    def _anchor_date(self, args: Mapping[str, Any]) -> date:
        raw = str(args.get("as_of") or args.get("asof") or "").strip()
        if raw and self._is_exact_date(raw):
            return self._parse_date(raw)
        return self.today()

    def _exact_dates(self, values: Sequence[str]) -> List[date]:
        result: List[date] = []
        for value in values:
            if self._is_exact_date(value):
                result.append(self._parse_date(value))
        return result

    def _load_trade_days(self, *, market_code: str, through: date) -> List[date]:
        db = self.db_factory(database=self.database)
        try:
            with db.conn.cursor() as cursor:
                cursor.execute(
                    f"SELECT calendar_date FROM {TRADE_CALENDAR_TABLE} "
                    "WHERE market_code = %s AND is_trade_day = 1 AND calendar_date <= %s "
                    "ORDER BY calendar_date ASC",
                    (market_code, through),
                )
                rows = cursor.fetchall()
            dates: List[date] = []
            for row in rows:
                value = row[0] if not isinstance(row, Mapping) else row.get("calendar_date")
                if isinstance(value, datetime):
                    value = value.date()
                if isinstance(value, date):
                    dates.append(value)
                elif value:
                    dates.append(self._parse_date(str(value)))
            return dates
        finally:
            db.close_db()

    def _resolve_value(
        self,
        value: Any,
        *,
        field: str,
        anchor: date,
        trade_days: Sequence[date],
    ) -> Tuple[str, TradeDateAdjustment | None]:
        raw = str(value).strip().strip("'\"")
        if raw.lower() in {"latest", "current", "today"}:
            requested = raw
            resolved = self._floor(anchor, trade_days)
            return resolved.isoformat(), None
        if re.fullmatch(r"-\d+", raw):
            offset = int(raw)
            before = [item for item in trade_days if item < anchor]
            index = abs(offset) - 1
            if index >= len(before):
                raise TradeDateResolutionError(f"trade date offset {raw} is outside available calendar history")
            return before[-1 - index].isoformat(), None
        requested_date = self._parse_date(raw)
        resolved = self._floor(requested_date, trade_days)
        adjustment = None
        if resolved != requested_date:
            adjustment = TradeDateAdjustment(field=field, requested=requested_date.isoformat(), resolved=resolved.isoformat())
        return resolved.isoformat(), adjustment

    @staticmethod
    def _floor(target: date, trade_days: Sequence[date]) -> date:
        eligible = [item for item in trade_days if item <= target]
        if not eligible:
            raise TradeDateResolutionError(f"no trade day on or before {target.isoformat()}")
        return eligible[-1]

    def _rewrite_filter(
        self,
        text: str,
        *,
        anchor: date,
        trade_days: Sequence[date],
        adjustments: List[TradeDateAdjustment],
    ) -> str:
        def replace(match: re.Match[str]) -> str:
            raw_value = match.group("value")
            value = raw_value.strip("'\"")
            resolved, adjustment = self._resolve_value(
                value,
                field=f"filter.{match.group('field')}",
                anchor=anchor,
                trade_days=trade_days,
            )
            if adjustment is not None:
                adjustments.append(adjustment)
            return f"{match.group('field')} {match.group('op')} '{resolved}'"

        return _FILTER_DATE_RE.sub(replace, text)

    @staticmethod
    def _is_exact_date(value: Any) -> bool:
        return bool(re.fullmatch(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{8}", str(value or "").strip()))

    @classmethod
    def _is_date_value(cls, value: Any) -> bool:
        return bool(_DATE_VALUE_RE.fullmatch(str(value or "").strip()))

    @staticmethod
    def _parse_date(value: str) -> date:
        text = str(value).strip().strip("'\"")
        if re.fullmatch(r"\d{8}", text):
            return datetime.strptime(text, "%Y%m%d").date()
        return datetime.strptime(text.replace("/", "-"), "%Y-%m-%d").date()
