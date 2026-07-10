import datetime as dt
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd
import pymysql

from src.market_info.market_info import StockInfo
from src.utils.mysql_utils import StockInfoDbUtils


class StockMomentumService:
    INDEX_CONFIG_DIR = Path("data/index_config")
    STOCK_PRICE_TABLE = "kcrp_stock_price"
    DEFAULT_WINDOWS = (4, 5)
    DEFAULT_TOP_N = 30
    DEFAULT_UNIVERSES = ("", "zz_100")
    UNIVERSE_ALIASES = {
        "": "",
        "all": "",
        "full_market": "",
        "全市场": "",
        "全部": "",
        "sz_50": "sz_50",
        "上证50": "sz_50",
        "上证五十": "sz_50",
        "中证50": "sz_50",
        "中证五十": "sz_50",
        "sse_50": "sz_50",
        "zz_100": "zz_100",
        "中证100": "zz_100",
        "中证一百": "zz_100",
    }
    UNIVERSE_LABELS = {
        "": "全市场",
        "sz_50": "上证50",
        "zz_100": "中证100",
    }

    def __init__(self) -> None:
        self.stock_info = StockInfo()
        self.name_to_code = self._build_name_to_code()

    def _build_name_to_code(self) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        for full_code, name in self.stock_info.stock_code_name.items():
            code6 = str(full_code).split(".", 1)[0]
            if code6 and name:
                mapping[str(name).strip()] = code6
        return mapping

    @staticmethod
    def normalize_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
        try:
            normalized = int(value)
        except Exception:
            normalized = int(default)
        return max(minimum, min(maximum, normalized))

    def normalize_universe(self, universe: str) -> str:
        raw = str(universe or "").strip()
        return self.UNIVERSE_ALIASES.get(raw, self.UNIVERSE_ALIASES.get(raw.lower(), raw))

    def universe_label(self, universe: str) -> str:
        normalized = self.normalize_universe(universe)
        return self.UNIVERSE_LABELS.get(normalized, normalized or "全市场")

    def load_universe_codes(self, universe: str) -> List[str]:
        normalized = self.normalize_universe(universe)
        if not normalized:
            return list(self.stock_info.stock_norm_code_list)

        config_path = self.INDEX_CONFIG_DIR / normalized
        if not config_path.exists():
            supported = sorted(key for key in self.UNIVERSE_LABELS if key)
            raise ValueError(f"不支持的股池范围: {universe}；当前支持: {supported} 与空字符串(全市场)")

        codes: List[str] = []
        for line in config_path.read_text(encoding="utf-8").splitlines():
            name = str(line or "").strip()
            if not name:
                continue
            code = self.name_to_code.get(name)
            if code:
                codes.append(code)
        if not codes:
            raise ValueError(f"股池 {normalized} 没有解析出任何股票代码")
        return codes

    @staticmethod
    def code_to_full_code(code: str) -> str:
        code6 = str(code or "").strip().split(".", 1)[0]
        if not code6:
            return ""
        if code6.startswith(("60", "68", "69", "90")):
            return f"{code6}.SH"
        if code6.startswith(("43", "82", "83", "87", "88", "92")):
            return f"{code6}.BJ"
        return f"{code6}.SZ"

    def load_recent_trade_dates(self, *, end_date: dt.date, limit: int) -> List[dt.date]:
        db = StockInfoDbUtils()
        try:
            with db.conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT trade_date
                    FROM {self.STOCK_PRICE_TABLE}
                    WHERE trade_date <= %s
                    GROUP BY trade_date
                    ORDER BY trade_date DESC
                    LIMIT %s
                    """,
                    (end_date, limit),
                )
                rows = cursor.fetchall()
        finally:
            db.close_db()
        return [row[0] for row in rows or []]

    def _load_daily_rows(self, *, trade_dates: Sequence[dt.date], codes: Optional[Sequence[str]] = None) -> pd.DataFrame:
        if not trade_dates:
            return pd.DataFrame()
        trade_date_placeholders = ",".join(["%s"] * len(trade_dates))
        sql = f"""
        SELECT
          stk_code,
          trade_date,
          close,
          amount
        FROM {self.STOCK_PRICE_TABLE}
        WHERE trade_date IN ({trade_date_placeholders})
        """
        params: List[Any] = list(trade_dates)
        if codes:
            code_placeholders = ",".join(["%s"] * len(codes))
            sql += f" AND stk_code IN ({code_placeholders})"
            params.extend(self.code_to_full_code(code) for code in codes)
        sql += " ORDER BY stk_code ASC, trade_date DESC"

        db = StockInfoDbUtils()
        try:
            with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute(sql, tuple(params))
                rows = cursor.fetchall()
        finally:
            db.close_db()

        df = pd.DataFrame(rows or [])
        if df.empty:
            return df
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
        df["code6"] = df["stk_code"].astype(str).str.split(".").str[0]
        return df

    def build_daily_score_frame(
        self,
        *,
        trade_date: dt.date,
        k: int,
    ) -> pd.DataFrame:
        normalized_k = self.normalize_int(k, 5, minimum=1, maximum=250)
        recent_trade_dates = self.load_recent_trade_dates(end_date=trade_date, limit=normalized_k)
        if len(recent_trade_dates) < normalized_k:
            raise ValueError(f"交易日历史不足，无法计算 {normalized_k} 日动量: {trade_date}")
        if recent_trade_dates[0] != trade_date:
            raise ValueError(f"{trade_date} 不是最近可用交易日，无法按日终口径计算动量")

        df = self._load_daily_rows(trade_dates=recent_trade_dates, codes=None)
        if df.empty:
            raise ValueError(f"没有获取到 {trade_date} 的日线数据")

        records: List[Dict[str, Any]] = []
        for code6, group in df.groupby("code6", sort=False):
            ordered = group.sort_values("trade_date", ascending=False).reset_index(drop=True)
            if len(ordered) < normalized_k:
                continue
            latest_trade_date = ordered.iloc[0]["trade_date"].date()
            if latest_trade_date != trade_date:
                continue
            today_close = float(ordered.iloc[0]["close"] or 0.0)
            reference_close = float(ordered.iloc[normalized_k - 1]["close"] or 0.0)
            window_amount = float(ordered.iloc[:normalized_k]["amount"].fillna(0.0).sum())
            if today_close <= 0 or reference_close <= 0 or window_amount <= 0:
                continue
            price_change_ratio = (today_close / reference_close) - 1.0
            window_total_amount_wan = window_amount / 10000.0
            avg_amount_wan = window_total_amount_wan / normalized_k
            if avg_amount_wan <= 0:
                continue
            records.append(
                {
                    "code6": code6,
                    "stock_code": self.code_to_full_code(code6),
                    "stock_name": self.stock_info.get_stock_name(code6) or "",
                    "close_price": round(today_close, 4),
                    "reference_close": round(reference_close, 4),
                    "price_change_ratio": round(price_change_ratio, 6),
                    "window_total_amount_wan": round(window_total_amount_wan, 4),
                    "avg_amount_wan": round(avg_amount_wan, 4),
                    "momentum_value": round((price_change_ratio * avg_amount_wan) / 100.0, 6),
                    "k": normalized_k,
                }
            )

        if not records:
            raise ValueError(f"{trade_date} 没有计算出任何可用的 {normalized_k} 日动量结果")

        ranking_df = pd.DataFrame(records)
        ranking_df.sort_values(by="momentum_value", ascending=False, inplace=True)
        ranking_df.reset_index(drop=True, inplace=True)
        ranking_df["global_rank"] = ranking_df.index + 1
        return ranking_df

    def build_daily_ranking(
        self,
        *,
        trade_date: dt.date,
        k: int,
        top_n: int = DEFAULT_TOP_N,
        universe: str = "",
        score_df: Optional[pd.DataFrame] = None,
    ) -> List[Dict[str, Any]]:
        normalized_top_n = self.normalize_int(top_n, self.DEFAULT_TOP_N, minimum=1, maximum=500)
        normalized_universe = self.normalize_universe(universe)
        codes = self.load_universe_codes(normalized_universe)
        ranking_df = score_df.copy() if score_df is not None else self.build_daily_score_frame(trade_date=trade_date, k=k)
        if normalized_universe:
            ranking_df = ranking_df[ranking_df["code6"].isin(set(codes))].copy()
        if ranking_df.empty:
            raise ValueError(f"{trade_date} 在股池 {self.universe_label(normalized_universe)} 中没有可用日线数据")

        ranking_df.sort_values(by="momentum_value", ascending=False, inplace=True)
        eligible_count = len(ranking_df)
        ranking_df = ranking_df.head(normalized_top_n).reset_index(drop=True)

        items: List[Dict[str, Any]] = []
        for idx, row in ranking_df.iterrows():
            payload = row.to_dict()
            payload["rank"] = idx + 1
            payload["eligible_count"] = eligible_count
            payload["universe"] = normalized_universe
            payload["universe_label"] = self.universe_label(normalized_universe)
            items.append(payload)
        return items
