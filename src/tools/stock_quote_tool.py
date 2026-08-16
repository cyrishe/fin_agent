import datetime
import os
import re
from typing import Any, Dict, List, Tuple

import requests

from src.utils.mysql_utils import StockInfoDbUtils


class StockQuoteTool:
    _STOCK_NAME_TSV = "stock_name.tsv"
    _NAME_TO_CODE: Dict[str, str] = {}
    _CODE_TOKEN_TO_CODE: Dict[str, str] = {}
    _CODE_TO_NAME: Dict[str, str] = {}
    _RESOLVER_LOADED = False

    _PREFIX2MKT: List[Tuple[str, str]] = [
        ("920", "BJ"), ("43", "BJ"), ("83", "BJ"),
        ("87", "BJ"), ("88", "BJ"), ("82", "BJ"),
        ("900", "SH"),
        ("688", "SH"), ("689", "SH"),
        ("60", "SH"), ("61", "SH"), ("603", "SH"),
        ("605", "SH"),
        ("000", "SZ"), ("001", "SZ"), ("002", "SZ"),
        ("003", "SZ"), ("200", "SZ"), ("300", "SZ"),
        ("301", "SZ"), ("302", "SZ"),
    ]

    _DIGIT2MARKET = {
        6: "sh", 9: "bj", 4: "bj", 0: "sz",
        3: "sz", 1: "sz", 2: "sz", 8: "bj",
    }
    _MARKET2SETCODE = {
        "sh": 1,
        "sz": 0,
        "bj": 7,
    }

    @classmethod
    def _load_resolver(cls) -> None:
        if cls._RESOLVER_LOADED:
            return
        cls._RESOLVER_LOADED = True
        if not os.path.exists(cls._STOCK_NAME_TSV):
            return
        with open(cls._STOCK_NAME_TSV, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or "\t" not in line:
                    continue
                full_code, name = line.split("\t", 1)
                full_code = str(full_code or "").strip().upper()
                name = str(name or "").strip()
                match = re.fullmatch(r"(\d{6})\.(SZ|SH|BJ)", full_code)
                if not match:
                    continue
                code6 = match.group(1)
                exchange = match.group(2)
                cls._NAME_TO_CODE[name] = code6
                cls._CODE_TO_NAME[code6] = name
                cls._CODE_TO_NAME[f"{code6}.{exchange}"] = name
                cls._CODE_TOKEN_TO_CODE[code6] = code6
                cls._CODE_TOKEN_TO_CODE[f"{code6}.{exchange}"] = code6
                cls._CODE_TOKEN_TO_CODE[f"{exchange}{code6}"] = code6
                cls._CODE_TOKEN_TO_CODE[f"{code6}{exchange}"] = code6

    def _resolve_input_to_code6(self, value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            raise ValueError("证券代码或名称不能为空")

        self._load_resolver()
        if raw in self._NAME_TO_CODE:
            return self._NAME_TO_CODE[raw]

        upper = raw.upper().replace("-", "").replace("_", "").replace(" ", "")
        upper = upper.replace("SH.", "SH").replace("SZ.", "SZ").replace("BJ.", "BJ")
        upper = upper.replace(".SH", "SH").replace(".SZ", "SZ").replace(".BJ", "BJ")
        if upper in self._CODE_TOKEN_TO_CODE:
            return self._CODE_TOKEN_TO_CODE[upper]

        match = re.search(r"(\d{6})", upper)
        if match:
            return match.group(1)

        raise ValueError(f"无法识别证券代码或名称: {raw}")

    def get_stock_name(self, code_or_name: str) -> str:
        code6 = self._resolve_input_to_code6(code_or_name)
        self._load_resolver()
        return self._CODE_TO_NAME.get(code6, "")

    def normalize_cn_a_code(self, code: str) -> str:
        raw = self._resolve_input_to_code6(code)
        for prefix, mkt in self._PREFIX2MKT:
            if raw.startswith(prefix):
                return f"{raw}.{mkt}"
        raise ValueError(f"无法识别的证券代码前缀: {raw}")

    def _to_upchina_code(self, code: str) -> str:
        return self.normalize_cn_a_code(code).split(".", 1)[0]

    def _get_setcode(self, code: str) -> int:
        digits = self._to_upchina_code(code)
        market = self._DIGIT2MARKET.get(int(digits[0]), "sh")
        return self._MARKET2SETCODE.get(market, 1)

    @staticmethod
    def _compute_tech_indicators(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not rows:
            return []
        enriched = [dict(item) for item in rows]
        closes = [float(item.get("close", 0) or 0) for item in enriched]

        def ema(values: List[float], span: int) -> List[float]:
            alpha = 2 / (span + 1)
            out = []
            prev = None
            for value in values:
                prev = value if prev is None else alpha * value + (1 - alpha) * prev
                out.append(prev)
            return out

        def rolling_mean(values: List[float], window: int) -> List[float]:
            out = []
            for i in range(len(values)):
                start = max(0, i - window + 1)
                chunk = values[start : i + 1]
                out.append(sum(chunk) / len(chunk))
            return out

        pct_changes = [0.0]
        for i in range(1, len(closes)):
            prev = closes[i - 1]
            pct_changes.append(0.0 if abs(prev) < 1e-9 else (closes[i] - prev) / prev * 100)

        ema12 = ema(closes, 12)
        ema26 = ema(closes, 26)
        macd = [a - b for a, b in zip(ema12, ema26)]
        dea = ema(macd, 9)
        hist = [a - b for a, b in zip(macd, dea)]
        ma5 = rolling_mean(closes, 5)
        ma10 = rolling_mean(closes, 10)
        ma20 = rolling_mean(closes, 20)

        gains = []
        losses = []
        for i in range(len(closes)):
            if i == 0:
                gains.append(0.0)
                losses.append(0.0)
                continue
            delta = closes[i] - closes[i - 1]
            gains.append(max(delta, 0.0))
            losses.append(max(-delta, 0.0))
        avg_gain = rolling_mean(gains, 14)
        avg_loss = rolling_mean(losses, 14)
        rsi = []
        for g, l in zip(avg_gain, avg_loss):
            rs = g / (l + 1e-9)
            rsi.append(100 - 100 / (1 + rs))

        for idx, item in enumerate(enriched):
            item["pct_chg"] = round(pct_changes[idx], 4)
            item["MACD"] = round(macd[idx], 4)
            item["DEA"] = round(dea[idx], 4)
            item["MACD_HIST"] = round(hist[idx], 4)
            item["MA5"] = round(ma5[idx], 4)
            item["MA10"] = round(ma10[idx], 4)
            item["MA20"] = round(ma20[idx], 4)
            item["RSI"] = round(rsi[idx], 4)
        return enriched

    @staticmethod
    def _rows_to_echarts(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        indicators = {key: [] for key in ["MACD", "DEA", "MACD_HIST", "MA5", "MA10", "MA20", "RSI"]}
        kline = []
        for row in rows:
            tstr = str(row.get("Datetime") or row.get("time") or "")
            kline.append([
                tstr,
                float(row.get("open", 0) or 0),
                float(row.get("close", 0) or 0),
                float(row.get("low", 0) or 0),
                float(row.get("high", 0) or 0),
                float(row.get("volume", 0) or 0),
                float(row.get("pct_chg", 0) or 0),
            ])
            for key in indicators:
                indicators[key].append([tstr, float(row.get(key, 0) or 0)])
        return {"kline": kline, "indicators": indicators}

    def get_daily_history(self, code: str, history_days: int = 60) -> Dict[str, Any]:
        db = StockInfoDbUtils()
        try:
            stk_code = self.normalize_cn_a_code(code)
            start_date = datetime.datetime.now() - datetime.timedelta(days=max(7, int(history_days) * 2))
            end_date = datetime.datetime.now()
            df = db.get_stock_history_hq(stk_code, start_date=start_date, end_date=end_date)
        finally:
            db.close_db()

        if df is None or df.empty:
            return {"kline": [], "indicators": {}}

        df = df.copy()
        df.reset_index(inplace=True)
        df.rename(columns={"trade_date": "Datetime"}, inplace=True)
        df.sort_values("Datetime", inplace=True)
        df.reset_index(drop=True, inplace=True)
        if len(df) > history_days:
            df = df.tail(history_days)
        rows = df.to_dict("records")
        rows = self._compute_tech_indicators(rows)
        return self._rows_to_echarts(rows)

    def get_realtime_quote(self, code: str) -> Dict[str, Any]:
        payload = {
            "stReq": {
                "vStock": [
                    {
                        "shtSetcode": self._get_setcode(code),
                        "sCode": self._to_upchina_code(code),
                    }
                ],
                "eHqData": 1,
            }
        }
        resp = requests.post(
            "http://jzyzwup.upoem1.com/json/hq_basichq/stockHq",
            json=payload,
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("stRsp", {}).get("vStockHq", [])
        if not items:
            return {}
        item = items[0]
        sim = item.get("stSimHq", {}) or {}
        return {
            "code": str(item.get("sCode") or self._to_upchina_code(code)),
            "现价": sim.get("fNowPrice", 0),
            "开盘": sim.get("fOpen", 0),
            "最高": sim.get("fHigh", 0),
            "最低": sim.get("fLow", 0),
            "涨跌额": sim.get("fChgValue", 0),
            "涨跌幅": sim.get("fChgRatio", 0),
            "成交额": sim.get("fAmount", 0),
            "成交量": sim.get("lVolume", 0),
            "昨收": sim.get("fClose", 0),
        }

    def get_realtime_quote_from_db(self, code_or_name: str) -> Dict[str, Any]:
        db = StockInfoDbUtils()
        try:
            identity = db.resolve_stock_identity(code_or_name)
            if not identity:
                return {}
            stk_code = str(identity.get("stk_code") or "").strip()
            code6 = stk_code[:6] if stk_code else ""
            if not code6:
                return {}
            sql = """
                SELECT snapshot_time, snapshot_slot, stk_code, stk_name,
                       latest_price, open_price, high_price, low_price,
                       preclose_price, chg_value, chg_ratio, volume, amount, source
                FROM aiia_stock_realtime_minute_snapshot
                WHERE stk_code = %s
                  AND kline_type = '1d'
                  AND period_minutes = 1440
                ORDER BY trade_date DESC, snapshot_time DESC, bar_end_time DESC
                LIMIT 1
            """
            with db.conn.cursor() as cursor:
                cursor.execute(sql, (code6,))
                row = cursor.fetchone()
            if not row:
                return {}
            snapshot_time = row[0]
            return {
                "code": code6,
                "name": str(row[3] or identity.get("stk_name") or "").strip(),
                "snapshot_time": snapshot_time.strftime("%Y-%m-%d %H:%M:%S") if hasattr(snapshot_time, "strftime") else str(snapshot_time or ""),
                "source": str(row[13] or "aiia_stock_realtime_minute_snapshot").strip(),
                "现价": float(row[4] or 0),
                "开盘": float(row[5] or 0),
                "最高": float(row[6] or 0),
                "最低": float(row[7] or 0),
                "昨收": float(row[8] or 0),
                "涨跌额": float(row[9] or 0),
                "涨跌幅": float(row[10] or 0),
                "成交量": float(row[11] or 0),
                "成交额": float(row[12] or 0),
            }
        finally:
            db.close_db()

    def get_minute_kline(self, code: str, minute_count: int = 100, line_type: int = 2) -> Dict[str, Any]:
        normalized_line_type = int(line_type or 2)
        if normalized_line_type not in (1, 2, 3):
            normalized_line_type = 2
        payload = {
            "stReq": {
                "stHeader": {"shtMarket": self._get_setcode(code)},
                "sCode": self._to_upchina_code(code),
                "eLineType": normalized_line_type,
                "shtStartxh": 0,
                "shtWantNum": max(1, int(minute_count)),
            }
        }
        resp = requests.post(
            "http://jzyzwup.upoem1.com/json/hq_marketdata/kLineData",
            json=payload,
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("stRsp", {}).get("vAnalyData", [])
        if not rows:
            return {"kline": [], "indicators": {}}

        kline_rows = []
        prev_close = None
        for item in rows:
            sht_time = int(((item.get("sttDateTime") or {}).get("shtTime") or 0))
            hh = sht_time // 60
            mm = sht_time % 60
            close_price = float(item.get("fClose", 0) or 0)
            if prev_close in (None, 0):
                pct_chg = 0.0
            else:
                pct_chg = (close_price - prev_close) / prev_close * 100
            kline_rows.append(
                {
                    "time": f"{hh:02d}:{mm:02d}",
                    "open": float(item.get("fOpen", 0) or 0),
                    "close": close_price,
                    "low": float(item.get("fLow", 0) or 0),
                    "high": float(item.get("fHigh", 0) or 0),
                    "volume": float(item.get("lVolume", 0) or 0),
                    "pct_chg": round(pct_chg, 4),
                }
            )
            prev_close = close_price

        kline_rows.sort(key=lambda x: x["time"])
        return {
            "kline": [
                [
                    row["time"],
                    row["open"],
                    row["close"],
                    row["low"],
                    row["high"],
                    row["volume"],
                    row["pct_chg"],
                ]
                for row in kline_rows
            ],
            "indicators": {},
        }

    def get_minute_kline_from_db(
        self,
        code_or_name: str,
        minute_count: int = 240,
        period_minutes: int = 1,
    ) -> Dict[str, Any]:
        db = StockInfoDbUtils()
        try:
            identity = db.resolve_stock_identity(code_or_name)
            if not identity:
                return {"kline": [], "indicators": {}}
            stk_code = str(identity.get("stk_code") or "").strip()
            code6 = stk_code[:6] if stk_code else ""
            if not code6:
                return {"kline": [], "indicators": {}}
            period = int(period_minutes or 1)
            if period not in {1, 3, 5, 10, 15, 30, 60}:
                period = 1
            sql = """
                SELECT bar_end_time, open_price, latest_price, low_price, high_price, volume
                FROM aiia_stock_realtime_minute_snapshot
                WHERE stk_code = %s
                  AND kline_type = %s
                  AND period_minutes = %s
                ORDER BY trade_date DESC, bar_end_time DESC, snapshot_time DESC
                LIMIT %s
            """
            with db.conn.cursor() as cursor:
                cursor.execute(sql, (code6, f"{period}m", period, max(1, int(minute_count))))
                rows = cursor.fetchall()
            if not rows:
                return {"kline": [], "indicators": {}}
            kline_list = []
            previous_close = None
            for row in reversed(rows):
                close = float(row[2] or 0)
                pct = 0.0 if previous_close in (None, 0) else (close - previous_close) / previous_close * 100
                kline_list.append([
                    str(row[0] or ""),
                    float(row[1] or 0),
                    close,
                    float(row[3] or 0),
                    float(row[4] or 0),
                    float(row[5] or 0),
                    round(pct, 4),
                ])
                previous_close = close
            return {
                "kline": kline_list,
                "indicators": {},
            }
        finally:
            db.close_db()


def _kline_payload_to_candles(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = payload.get("kline") if isinstance(payload, dict) else []
    candles: List[Dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, list) or len(row) < 7:
            continue
        candles.append(
            {
                "time": str(row[0] or ""),
                "open": float(row[1] or 0),
                "close": float(row[2] or 0),
                "low": float(row[3] or 0),
                "high": float(row[4] or 0),
                "volume": float(row[5] or 0),
                "pct": float(row[6] or 0),
            }
        )
    return candles


def _kline_payload_to_indicators(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    indicators = payload.get("indicators") if isinstance(payload, dict) else {}
    out: List[Dict[str, Any]] = []
    for name, rows in (indicators.items() if isinstance(indicators, dict) else []):
        points = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, list) or len(row) < 2:
                continue
            points.append({"time": str(row[0] or ""), "value": float(row[1] or 0)})
        if points:
            out.append({"name": str(name or "").strip(), "points": points})
    return out


def _build_quote_render_blocks(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    daily = payload.get("daily_kline") if isinstance(payload.get("daily_kline"), dict) else {}
    intraday = payload.get("intraday_kline") if isinstance(payload.get("intraday_kline"), dict) else {}
    realtime = payload.get("realtime_quote") if isinstance(payload.get("realtime_quote"), dict) else {}

    render_blocks: List[Dict[str, Any]] = []

    metric_items = [
        {"label": "现价", "value": realtime.get("现价")},
        {"label": "涨跌幅", "value": realtime.get("涨跌幅")},
        {"label": "成交额", "value": realtime.get("成交额")},
        {"label": "成交量", "value": realtime.get("成交量")},
        {"label": "最高", "value": realtime.get("最高")},
        {"label": "最低", "value": realtime.get("最低")},
    ]
    metric_items = [item for item in metric_items if item.get("value") not in (None, "")]
    if metric_items:
        render_blocks.append(
            {
                "type": "metric_strip",
                "title": "行情概览",
                "data": {"items": metric_items},
                "meta": {"source": str(source.get("realtime") or ""), "url": "", "unit": ""},
            }
        )

    daily_candles = _kline_payload_to_candles(daily)
    if daily_candles:
        render_blocks.append(
            {
                "type": "kline",
                "title": "日线K线",
                "data": {
                    "candles": daily_candles,
                    "indicators": _kline_payload_to_indicators(daily),
                },
                "meta": {"source": str(source.get("daily") or ""), "url": "", "unit": ""},
            }
        )

    intraday_candles = _kline_payload_to_candles(intraday)
    if intraday_candles:
        render_blocks.append(
            {
                "type": "line",
                "title": "分时走势",
                "data": {
                    "x_axis": [item["time"] for item in intraday_candles],
                    "series": [
                        {"name": "价格", "data": [item["close"] for item in intraday_candles]},
                    ],
                },
                "meta": {"source": str(source.get("intraday") or ""), "url": "", "unit": ""},
            }
        )

    return render_blocks


def get_stock_quote_snapshot(
    code: str,
    history_days: int = 60,
    minute_count: int = 100,
    minute_line_type: int = 2,
) -> Dict[str, Any]:
    tool = StockQuoteTool()
    realtime_quote = tool.get_realtime_quote(code)
    intraday_kline = tool.get_minute_kline(code, minute_count=minute_count, line_type=minute_line_type)
    daily_kline = tool.get_daily_history(code, history_days=history_days)
    normalized_code = tool.normalize_cn_a_code(code)
    return {
        "source": {
            "realtime": "upchina",
            "intraday": "upchina",
            "daily": "kcrp_stock_price",
        },
        "code": normalized_code.split(".", 1)[0],
        "stk_code": normalized_code,
        "name": tool.get_stock_name(code),
        "realtime_quote": realtime_quote,
        "intraday_kline": intraday_kline,
        "daily_kline": daily_kline,
        "render_blocks": _build_quote_render_blocks(
            {
                "source": {
                    "realtime": "upchina",
                    "intraday": "upchina",
                    "daily": "kcrp_stock_price",
                },
                "realtime_quote": realtime_quote,
                "intraday_kline": intraday_kline,
                "daily_kline": daily_kline,
            }
        ),
    }


def _build_realtime_render_blocks(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    quote = payload.get("quote") if isinstance(payload.get("quote"), dict) else {}
    source = str(payload.get("source") or "").strip()
    items = [
        {"label": "现价", "value": quote.get("current")},
        {"label": "开盘", "value": quote.get("open")},
        {"label": "最高", "value": quote.get("high")},
        {"label": "最低", "value": quote.get("low")},
        {"label": "成交量", "value": quote.get("volume")},
        {"label": "成交额", "value": quote.get("amount")},
    ]
    items = [item for item in items if item.get("value") not in (None, "")]
    if not items:
        return []
    return [
        {
            "type": "metric_strip",
            "title": "实时行情",
            "data": {"items": items},
            "meta": {"source": source, "url": "", "unit": ""},
        }
    ]


def _build_history_render_blocks(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    daily = payload.get("daily_kline") if isinstance(payload.get("daily_kline"), dict) else {}
    candles = _kline_payload_to_candles(daily)
    if not candles:
        return []
    return [
        {
            "type": "kline",
            "title": "历史日K",
            "data": {
                "candles": candles,
                "indicators": _kline_payload_to_indicators(daily),
            },
            "meta": {"source": str(payload.get("source") or "").strip(), "url": "", "unit": ""},
        }
    ]


def _build_intraday_render_blocks(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    intraday = payload.get("intraday_kline") if isinstance(payload.get("intraday_kline"), dict) else {}
    candles = _kline_payload_to_candles(intraday)
    if not candles:
        return []
    return [
        {
            "type": "kline",
            "title": "日内分钟K线",
            "data": {
                "candles": candles,
                "indicators": [],
            },
            "meta": {"source": str(payload.get("source") or "").strip(), "url": "", "unit": ""},
        }
    ]


def get_stock_realtime_snapshot(name: str) -> Dict[str, Any]:
    tool = StockQuoteTool()
    realtime_quote = tool.get_realtime_quote_from_db(name) or tool.get_realtime_quote(name)
    normalized_code = tool.normalize_cn_a_code(name)
    payload = {
        "name": tool.get_stock_name(name),
        "code": normalized_code.split(".", 1)[0],
        "stk_code": normalized_code,
        "source": str(realtime_quote.get("source") or "upchina"),
        "quote": {
            "open": realtime_quote.get("开盘", 0),
            "current": realtime_quote.get("现价", 0),
            "high": realtime_quote.get("最高", 0),
            "low": realtime_quote.get("最低", 0),
            "volume": realtime_quote.get("成交量", 0),
            "amount": realtime_quote.get("成交额", 0),
        },
    }
    payload["render_blocks"] = _build_realtime_render_blocks(payload)
    return payload


def get_stock_history_kline_snapshot(name: str, days: int = 60) -> Dict[str, Any]:
    tool = StockQuoteTool()
    daily_kline = tool.get_daily_history(name, history_days=days)
    normalized_code = tool.normalize_cn_a_code(name)
    candles = _kline_payload_to_candles(daily_kline)
    payload = {
        "name": tool.get_stock_name(name),
        "code": normalized_code.split(".", 1)[0],
        "stk_code": normalized_code,
        "source": "kcrp_stock_price",
        "days": int(days),
        "daily_kline": {
            "candles": candles,
            "indicators": _kline_payload_to_indicators(daily_kline),
        },
        "latest_candle": candles[-1] if candles else {},
    }
    payload["render_blocks"] = _build_history_render_blocks({"daily_kline": daily_kline, "source": payload["source"]})
    return payload


def get_stock_intraday_kline_snapshot(name: str, minute_count: int = 240, line_type: int = 2) -> Dict[str, Any]:
    tool = StockQuoteTool()
    intraday_kline_db = tool.get_minute_kline_from_db(name, minute_count=minute_count)
    intraday_kline = intraday_kline_db or tool.get_minute_kline(name, minute_count=minute_count, line_type=line_type)
    normalized_code = tool.normalize_cn_a_code(name)
    candles = _kline_payload_to_candles(intraday_kline)
    payload = {
        "name": tool.get_stock_name(name),
        "code": normalized_code.split(".", 1)[0],
        "stk_code": normalized_code,
        "source": "aiia_stock_realtime_minute_snapshot" if intraday_kline_db else "upchina",
        "minute_count": int(minute_count),
        "intraday_kline": {
            "candles": candles,
            "indicators": [],
        },
    }
    payload["render_blocks"] = _build_intraday_render_blocks({"intraday_kline": intraday_kline, "source": payload["source"]})
    return payload


def run_realtime(args: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(args or {})
    try:
        payload = get_stock_realtime_snapshot(
            name=str(params.get("name") or params.get("code") or params.get("query") or "").strip(),
        )
        return {
            "tool": "stock_realtime_quote",
            "ok": True,
            "data": payload,
            "error": "",
        }
    except Exception as exc:
        return {
            "tool": "stock_realtime_quote",
            "ok": False,
            "data": {},
            "error": str(exc),
        }


def run_history_kline(args: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(args or {})
    try:
        payload = get_stock_history_kline_snapshot(
            name=str(params.get("name") or params.get("code") or params.get("query") or "").strip(),
            days=int(params.get("days", 60) or 60),
        )
        return {
            "tool": "stock_history_kline",
            "ok": True,
            "data": payload,
            "error": "",
        }
    except Exception as exc:
        return {
            "tool": "stock_history_kline",
            "ok": False,
            "data": {},
            "error": str(exc),
        }


def run_intraday_kline(args: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(args or {})
    try:
        payload = get_stock_intraday_kline_snapshot(
            name=str(params.get("name") or params.get("code") or params.get("query") or "").strip(),
            minute_count=int(params.get("minute_count", 240) or 240),
            line_type=int(params.get("minute_line_type", 2) or 2),
        )
        return {
            "tool": "stock_intraday_kline",
            "ok": True,
            "data": payload,
            "error": "",
        }
    except Exception as exc:
        return {
            "tool": "stock_intraday_kline",
            "ok": False,
            "data": {},
            "error": str(exc),
        }


def run(args: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(args or {})
    try:
        payload = get_stock_quote_snapshot(
            code=str(params.get("code") or params.get("query") or params.get("name") or params.get("company") or "").strip(),
            history_days=int(params.get("history_days", 60) or 60),
            minute_count=int(params.get("minute_count", 100) or 100),
            minute_line_type=params.get("minute_line_type", 2),
        )
        return {
            "tool": "stock_quote",
            "ok": True,
            "data": payload,
            "error": "",
        }
    except Exception as exc:
        return {
            "tool": "stock_quote",
            "ok": False,
            "data": {},
            "error": str(exc),
        }
