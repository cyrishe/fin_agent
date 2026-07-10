import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd
import pymysql

from src.market_info.market_info import StockInfo
from src.services.stock_momentum_service import StockMomentumService
from src.utils.mysql_utils import StockInfoDbUtils


SNAPSHOT_TABLE = "aiia_stock_realtime_minute_snapshot"
STRATEGY_TABLE = "aiia_stock_strategy_result_daily"
STRATEGY_NAME = "stock_momentum_ranking"
STRATEGY_VERSION = "v1"

GROUP_TO_UNIVERSE = {
    "full_market": "",
    "zz_100": "zz_100",
}
GROUP_LABELS = {
    "full_market": "全市场",
    "zz_100": "中证100",
    "custom_list": "自定义列表",
}


class StockMomentumRankingTool:
    _CACHE_DIR = Path("data/tool_cache/stock_momentum_ranking")
    _CACHE_VERSION = "v3"

    def __init__(self) -> None:
        self.stock_info = StockInfo()
        self.momentum_service = StockMomentumService()

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        try:
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _normalize_bool(value: Any, default: bool) -> bool:
        if value is None:
            return bool(default)
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
        return bool(default)

    def _normalize_trade_date(self, value: Any) -> Optional[dt.date]:
        raw = str(value or "").strip()
        if not raw:
            return None
        return dt.datetime.strptime(raw, "%Y-%m-%d").date()

    def _normalize_windows(self, value: Any) -> List[int]:
        if not value:
            return [4, 5]
        if isinstance(value, (list, tuple)):
            items = list(value)
        else:
            items = [value]
        normalized: List[int] = []
        for item in items:
            window = self.momentum_service.normalize_int(item, 5, minimum=1, maximum=250)
            if window not in (4, 5):
                continue
            if window not in normalized:
                normalized.append(window)
        return normalized or [4, 5]

    def _normalize_groups(self, value: Any) -> List[str]:
        if not value:
            return ["full_market", "zz_100"]
        if isinstance(value, (list, tuple)):
            items = list(value)
        else:
            items = [value]
        normalized: List[str] = []
        for item in items:
            group = str(item or "").strip()
            if group in GROUP_TO_UNIVERSE and group not in normalized:
                normalized.append(group)
        return normalized or ["full_market", "zz_100"]

    def _normalize_stock_list(self, value: Any) -> List[str]:
        if not value:
            return []
        items = value if isinstance(value, (list, tuple)) else [value]
        normalized: List[str] = []
        for item in items:
            raw = str(item or "").strip()
            if not raw:
                continue
            code = raw.split(".", 1)[0]
            if code.lower().startswith(("sh", "sz", "bj")):
                code = code[2:]
            if code.isdigit():
                code = code.zfill(6)
            if len(code) == 6 and code.isdigit() and code not in normalized:
                normalized.append(code)
        return normalized

    def _fetch_realtime_quotes_via_api(self, codes: Sequence[str], normalized_universe: str) -> pd.DataFrame:
        if not normalized_universe:
            df = self.stock_info.get_all_stocks_hq(ret_format="dataframe")
            if df is None or df.empty:
                return pd.DataFrame()
            normalized = df.rename(columns={"代码": "code", "名称": "name", "最新价": "realtime_price", "成交额": "realtime_amount"}).copy()
            normalized["code"] = normalized["code"].astype(str).str.strip()
            normalized["name"] = normalized["name"].astype(str).str.strip()
            normalized["realtime_price"] = pd.to_numeric(normalized["realtime_price"], errors="coerce")
            normalized["realtime_amount"] = pd.to_numeric(normalized["realtime_amount"], errors="coerce")
            return normalized[["code", "name", "realtime_price", "realtime_amount"]]

        rows: List[Dict[str, Any]] = []
        batch_size = 200
        for idx in range(0, len(codes), batch_size):
            batch = list(codes[idx : idx + batch_size])
            for item in self.stock_info.get_multi_stocks_hq(batch):
                for code, payload in item.items():
                    code6 = str(code or "").strip().split(".", 1)[0]
                    rows.append(
                        {
                            "code": code6,
                            "name": self.stock_info.get_stock_name(code6) or "",
                            "realtime_price": float(payload.get("现价", 0) or 0),
                            "realtime_amount": float(payload.get("成交额", 0) or 0),
                        }
                    )
        return pd.DataFrame(rows)

    def _load_latest_snapshot_slot(self, trade_date: Optional[dt.date] = None) -> Dict[str, Any] | None:
        db = StockInfoDbUtils()
        try:
            with db.conn.cursor() as cursor:
                if trade_date is None:
                    cursor.execute(
                        f"""
                        SELECT trade_date, minute_index, MAX(snapshot_time) AS snapshot_time
                        FROM {SNAPSHOT_TABLE}
                        GROUP BY trade_date, minute_index
                        ORDER BY trade_date DESC, minute_index DESC
                        LIMIT 1
                        """
                    )
                else:
                    cursor.execute(
                        f"""
                        SELECT trade_date, minute_index, MAX(snapshot_time) AS snapshot_time
                        FROM {SNAPSHOT_TABLE}
                        WHERE trade_date = %s
                        GROUP BY trade_date, minute_index
                        ORDER BY minute_index DESC
                        LIMIT 1
                        """,
                        (trade_date,),
                    )
                row = cursor.fetchone()
        finally:
            db.close_db()
        if not row:
            return None
        return {
            "trade_date": row[0],
            "minute_index": int(row[1]),
            "snapshot_time": row[2],
        }

    def _load_snapshot_df(self, *, trade_date: dt.date, minute_index: int, codes: Optional[Sequence[str]] = None) -> pd.DataFrame:
        db = StockInfoDbUtils()
        try:
            with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
                sql = f"""
                SELECT
                  stk_code AS code,
                  stk_name AS name,
                  latest_price AS realtime_price,
                  amount AS realtime_amount,
                  chg_ratio AS current_pct,
                  snapshot_time
                FROM {SNAPSHOT_TABLE}
                WHERE trade_date = %s
                  AND minute_index = %s
                """
                params: List[Any] = [trade_date, minute_index]
                if codes:
                    placeholders = ",".join(["%s"] * len(codes))
                    sql += f" AND stk_code IN ({placeholders})"
                    params.extend(list(codes))
                cursor.execute(sql, tuple(params))
                rows = cursor.fetchall()
        finally:
            db.close_db()
        df = pd.DataFrame(rows or [])
        if df.empty:
            return df
        df["code"] = df["code"].astype(str).str.strip().str.split(".").str[0]
        df["name"] = df["name"].astype(str).str.strip()
        df["realtime_price"] = pd.to_numeric(df["realtime_price"], errors="coerce")
        df["realtime_amount"] = pd.to_numeric(df["realtime_amount"], errors="coerce")
        df["current_pct"] = pd.to_numeric(df["current_pct"], errors="coerce")
        return df

    def _build_cache_key(
        self,
        *,
        trade_date: dt.date,
        minute_index: int,
        k: int,
        scope_key: str,
    ) -> str:
        payload = {
            "version": self._CACHE_VERSION,
            "trade_date": trade_date.isoformat(),
            "minute_index": int(minute_index),
            "k": int(k),
            "scope_key": scope_key,
        }
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(text.encode("utf-8")).hexdigest()

    def _cache_path(self, cache_key: str) -> Path:
        return self._CACHE_DIR / f"{cache_key}.json"

    def _load_cache(self, cache_key: str) -> List[Dict[str, Any]] | None:
        path = self._cache_path(cache_key)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        data = payload.get("data")
        if isinstance(data, list):
            return data
        return None

    def _save_cache(self, cache_key: str, data: List[Dict[str, Any]]) -> None:
        self._CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "cache_version": self._CACHE_VERSION,
            "saved_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "data": data,
        }
        self._cache_path(cache_key).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _build_realtime_ranked_items(
        self,
        *,
        realtime_df: pd.DataFrame,
        codes: Sequence[str],
        normalized_k: int,
    ) -> List[Dict[str, Any]]:
        realtime_df = realtime_df.dropna(subset=["realtime_price", "realtime_amount"]).copy()
        realtime_df["code"] = realtime_df["code"].astype(str).str.strip().str.split(".").str[0]
        realtime_df = realtime_df[realtime_df["code"].isin(set(codes))]
        if realtime_df.empty:
            raise ValueError("实时行情为空")

        today = pd.Timestamp.now().date()
        recent_trade_dates = self.momentum_service.load_recent_trade_dates(end_date=today, limit=normalized_k)
        if len(recent_trade_dates) < normalized_k:
            raise ValueError("没有获取到足够的历史交易日")
        history_df = self.momentum_service._load_daily_rows(trade_dates=recent_trade_dates[1:], codes=realtime_df["code"].tolist())
        if history_df.empty and normalized_k > 1:
            raise ValueError("没有获取到可用的历史参考价格和成交额")

        reference_metrics_map: Dict[str, Any] = {}
        if normalized_k <= 1:
            reference_metrics_map = {str(code): (None, 0.0) for code in realtime_df["code"].tolist()}
        else:
            for code6, group in history_df.groupby("code6", sort=False):
                ordered = group.sort_values("trade_date", ascending=False).reset_index(drop=True)
                if len(ordered) < normalized_k - 1:
                    continue
                reference_close = float(ordered.iloc[normalized_k - 2]["close"] or 0.0)
                historical_amount_total = float(ordered.iloc[: normalized_k - 1]["amount"].fillna(0.0).sum())
                if reference_close > 0:
                    reference_metrics_map[str(code6)] = (reference_close, historical_amount_total)
        if not reference_metrics_map:
            raise ValueError("没有获取到可用的历史参考价格和成交额")

        realtime_df["reference_metrics"] = realtime_df["code"].map(reference_metrics_map)
        realtime_df = realtime_df.dropna(subset=["reference_metrics"]).copy()
        realtime_df["reference_close"] = realtime_df["reference_metrics"].apply(lambda item: float(item[0]) if item else 0.0)
        realtime_df["historical_amount_total"] = realtime_df["reference_metrics"].apply(lambda item: float(item[1]) if item else 0.0)
        realtime_df["realtime_amount"] = pd.to_numeric(realtime_df["realtime_amount"], errors="coerce").fillna(0.0)
        realtime_df["window_total_amount_wan"] = (realtime_df["historical_amount_total"] + realtime_df["realtime_amount"]) / 10000
        realtime_df["avg_amount_wan"] = realtime_df["window_total_amount_wan"] / normalized_k
        realtime_df["realtime_amount_wan"] = realtime_df["realtime_amount"] / 10000
        realtime_df = realtime_df[(realtime_df["reference_close"] > 0) & (realtime_df["avg_amount_wan"] > 0)].copy()
        realtime_df["price_change_ratio"] = (realtime_df["realtime_price"] / realtime_df["reference_close"]) - 1
        realtime_df["momentum_value"] = (realtime_df["price_change_ratio"] * realtime_df["avg_amount_wan"]) / 100
        realtime_df.sort_values(by="momentum_value", ascending=False, inplace=True)
        realtime_df.reset_index(drop=True, inplace=True)

        if "current_pct" not in realtime_df.columns:
            realtime_df["current_pct"] = None

        items: List[Dict[str, Any]] = []
        for idx, row in realtime_df.iterrows():
            items.append(
                {
                    "rank": idx + 1,
                    "stock_code": self.momentum_service.code_to_full_code(str(row["code"])),
                    "stock_name": str(row.get("name") or ""),
                    "momentum_value": round(float(row["momentum_value"]), 6),
                    "current_pct": round(float(row["current_pct"]), 6) if pd.notna(row.get("current_pct")) else None,
                    "current_amount": round(float(row["realtime_amount"]), 4),
                    "reference_close": round(float(row["reference_close"]), 4),
                    "window_total_amount_wan": round(float(row["window_total_amount_wan"]), 4),
                    "avg_amount_wan": round(float(row["avg_amount_wan"]), 4),
                }
            )
        return items

    def _load_previous_trade_date(self, trade_date: dt.date) -> Optional[dt.date]:
        dates = self.momentum_service.load_recent_trade_dates(end_date=trade_date - dt.timedelta(days=1), limit=1)
        return dates[0] if dates else None

    def _load_daily_metric_map(self, *, trade_date: dt.date, codes: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        if not codes:
            return {}
        prev_trade_date = self._load_previous_trade_date(trade_date)
        trade_dates = [trade_date]
        if prev_trade_date:
            trade_dates.append(prev_trade_date)
        df = self.momentum_service._load_daily_rows(trade_dates=trade_dates, codes=codes)
        if df.empty:
            return {}

        metric_map: Dict[str, Dict[str, Any]] = {}
        for code6, group in df.groupby("code6", sort=False):
            ordered = group.sort_values("trade_date", ascending=False).reset_index(drop=True)
            current_close = float(ordered.iloc[0]["close"] or 0.0)
            current_amount = float(ordered.iloc[0]["amount"] or 0.0)
            prev_close = float(ordered.iloc[1]["close"] or 0.0) if len(ordered) > 1 else 0.0
            current_pct = ((current_close / prev_close) - 1.0) * 100.0 if prev_close > 0 else None
            metric_map[str(code6)] = {
                "current_pct": round(float(current_pct), 6) if current_pct is not None else None,
                "current_amount": round(current_amount, 4),
            }
        return metric_map

    def _load_daily_ranking_from_table(
        self,
        *,
        trade_date: dt.date,
        window: int,
        universe: str,
        top_n: int,
    ) -> List[Dict[str, Any]]:
        universe_value = self.momentum_service.normalize_universe(universe)
        db = StockInfoDbUtils()
        try:
            with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute(
                    f"""
                    SELECT
                      rank_no,
                      subject_code,
                      subject_name,
                      value_decimal,
                      strategy_detail_json
                    FROM {STRATEGY_TABLE}
                    WHERE trade_date = %s
                      AND strategy_name = %s
                      AND strategy_version = %s
                      AND strategy_instance_key = %s
                      AND universe = %s
                    ORDER BY rank_no ASC
                    LIMIT %s
                    """,
                    (trade_date, STRATEGY_NAME, STRATEGY_VERSION, f"k_{int(window)}", universe_value, top_n),
                )
                rows = cursor.fetchall()
        finally:
            db.close_db()

        items: List[Dict[str, Any]] = []
        for row in rows or []:
            detail = {}
            try:
                detail = json.loads(row.get("strategy_detail_json") or "{}")
            except Exception:
                detail = {}
            items.append(
                {
                    "rank": int(row.get("rank_no") or 0),
                    "stock_code": str(row.get("subject_code") or ""),
                    "stock_name": str(row.get("subject_name") or ""),
                    "momentum_value": round(float(row.get("value_decimal") or 0.0), 6),
                    "reference_close": self._safe_float(detail.get("reference_close")),
                    "window_total_amount_wan": self._safe_float(detail.get("window_total_amount_wan")),
                    "avg_amount_wan": self._safe_float(detail.get("avg_amount_wan")),
                }
            )
        if not items:
            raise ValueError(f"{trade_date} 没有找到 window={window}, universe={universe_value or 'full_market'} 的离线动量结果")

        codes = [str(item["stock_code"]).split(".", 1)[0] for item in items]
        metric_map = self._load_daily_metric_map(trade_date=trade_date, codes=codes)
        for item in items:
            code6 = str(item["stock_code"]).split(".", 1)[0]
            metrics = metric_map.get(code6, {})
            item["current_pct"] = metrics.get("current_pct")
            item["current_amount"] = metrics.get("current_amount")
        return items

    def _load_rank_change_map(
        self,
        *,
        trade_date: dt.date,
        prev_trade_date: Optional[dt.date],
        window: int,
        universe: str,
        codes: Sequence[str],
    ) -> Dict[str, Dict[str, Any]]:
        if not prev_trade_date or not codes:
            return {}
        normalized_codes = [self.momentum_service.code_to_full_code(str(code).split(".", 1)[0]) for code in codes]
        placeholders = ",".join(["%s"] * len(normalized_codes))
        db = StockInfoDbUtils()
        try:
            with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute(
                    f"""
                    SELECT subject_code, rank_no
                    FROM {STRATEGY_TABLE}
                    WHERE trade_date = %s
                      AND strategy_name = %s
                      AND strategy_version = %s
                      AND strategy_instance_key = %s
                      AND universe = %s
                      AND subject_code IN ({placeholders})
                    """,
                    (prev_trade_date, STRATEGY_NAME, STRATEGY_VERSION, f"k_{int(window)}", self.momentum_service.normalize_universe(universe), *normalized_codes),
                )
                rows = cursor.fetchall()
        finally:
            db.close_db()
        return {
            str(row.get("subject_code") or ""): {"prev_rank": int(row.get("rank_no") or 0)}
            for row in rows or []
        }

    def _attach_rank_change(
        self,
        *,
        items: List[Dict[str, Any]],
        trade_date: dt.date,
        prev_trade_date: Optional[dt.date],
        window: int,
        universe: str,
        include_rank_change: bool,
    ) -> None:
        if not include_rank_change:
            for item in items:
                item["prev_rank"] = None
                item["rank_change"] = None
                item["is_new"] = False
            return
        code_map = self._load_rank_change_map(
            trade_date=trade_date,
            prev_trade_date=prev_trade_date,
            window=window,
            universe=universe,
            codes=[item["stock_code"] for item in items],
        )
        for item in items:
            prev = code_map.get(str(item["stock_code"]) or "")
            prev_rank = int(prev["prev_rank"]) if prev else None
            item["prev_rank"] = prev_rank
            item["rank_change"] = None if prev_rank is None else prev_rank - int(item["rank"])
            item["is_new"] = prev_rank is None

    def _group_payload_block(self, *, group: str, window: int, items: List[Dict[str, Any]], include_metrics: bool) -> Dict[str, Any]:
        normalized_items: List[Dict[str, Any]] = []
        for item in items:
            payload = {
                "rank": int(item["rank"]),
                "stock_code": str(item["stock_code"]),
                "stock_name": str(item["stock_name"]),
                "momentum_value": round(float(item["momentum_value"]), 6),
                "prev_rank": item.get("prev_rank"),
                "rank_change": item.get("rank_change"),
                "is_new": bool(item.get("is_new", False)),
            }
            if include_metrics:
                payload["current_pct"] = item.get("current_pct")
                payload["current_amount"] = item.get("current_amount")
            normalized_items.append(payload)
        return {
            "group": group,
            "group_label": GROUP_LABELS.get(group, group),
            "window": int(window),
            "items": normalized_items,
        }

    def _build_grouped_realtime(
        self,
        *,
        trade_date: dt.date,
        top_n: int,
        windows: Sequence[int],
        groups: Sequence[str],
        include_rank_change: bool,
        include_metrics: bool,
    ) -> Dict[str, Any]:
        snapshot_slot = self._load_latest_snapshot_slot(trade_date=trade_date)
        if not snapshot_slot:
            raise ValueError(f"{trade_date} 没有可用分钟快照")
        prev_trade_date = self._load_previous_trade_date(trade_date)
        blocks: List[Dict[str, Any]] = []
        for group in groups:
            normalized_universe = GROUP_TO_UNIVERSE[group]
            codes = self.momentum_service.load_universe_codes(normalized_universe)
            for window in windows:
                scope_key = f"{group}|k_{window}"
                cache_key = self._build_cache_key(
                    trade_date=snapshot_slot["trade_date"],
                    minute_index=snapshot_slot["minute_index"],
                    k=window,
                    scope_key=scope_key,
                )
                cached = self._load_cache(cache_key)
                if cached is None:
                    realtime_df = self._load_snapshot_df(
                        trade_date=snapshot_slot["trade_date"],
                        minute_index=snapshot_slot["minute_index"],
                        codes=codes,
                    )
                    if realtime_df.empty:
                        realtime_df = self._fetch_realtime_quotes_via_api(codes, normalized_universe)
                    items = self._build_realtime_ranked_items(
                        realtime_df=realtime_df,
                        codes=codes,
                        normalized_k=window,
                    )
                    self._save_cache(cache_key, items)
                else:
                    items = list(cached)
                trimmed = [dict(item) for item in items[:top_n]]
                self._attach_rank_change(
                    items=trimmed,
                    trade_date=trade_date,
                    prev_trade_date=prev_trade_date,
                    window=window,
                    universe=normalized_universe,
                    include_rank_change=include_rank_change,
                )
                blocks.append(self._group_payload_block(group=group, window=window, items=trimmed, include_metrics=include_metrics))
        return {
            "tool": "个股动量排名",
            "ok": True,
            "meta": {
                "trade_date": trade_date.strftime("%Y-%m-%d"),
                "snapshot_time": snapshot_slot["snapshot_time"].strftime("%Y-%m-%d %H:%M:%S") if snapshot_slot.get("snapshot_time") else "",
                "calc_mode": "realtime_intraday",
                "prev_trade_date": prev_trade_date.strftime("%Y-%m-%d") if prev_trade_date else "",
                "windows": [int(window) for window in windows],
                "top_n": int(top_n),
                "groups": list(groups),
                "scope_type": "grouped",
            },
            "data": blocks,
            "error": "",
        }

    def _build_grouped_daily(
        self,
        *,
        trade_date: dt.date,
        top_n: int,
        windows: Sequence[int],
        groups: Sequence[str],
        include_rank_change: bool,
        include_metrics: bool,
    ) -> Dict[str, Any]:
        prev_trade_date = self._load_previous_trade_date(trade_date)
        blocks: List[Dict[str, Any]] = []
        for group in groups:
            normalized_universe = GROUP_TO_UNIVERSE[group]
            for window in windows:
                items = self._load_daily_ranking_from_table(
                    trade_date=trade_date,
                    window=window,
                    universe=normalized_universe,
                    top_n=top_n,
                )
                self._attach_rank_change(
                    items=items,
                    trade_date=trade_date,
                    prev_trade_date=prev_trade_date,
                    window=window,
                    universe=normalized_universe,
                    include_rank_change=include_rank_change,
                )
                blocks.append(self._group_payload_block(group=group, window=window, items=items, include_metrics=include_metrics))
        return {
            "tool": "个股动量排名",
            "ok": True,
            "meta": {
                "trade_date": trade_date.strftime("%Y-%m-%d"),
                "snapshot_time": "",
                "calc_mode": "daily_final",
                "prev_trade_date": prev_trade_date.strftime("%Y-%m-%d") if prev_trade_date else "",
                "windows": [int(window) for window in windows],
                "top_n": int(top_n),
                "groups": list(groups),
                "scope_type": "grouped",
            },
            "data": blocks,
            "error": "",
        }

    def _build_custom_list(
        self,
        *,
        trade_date: dt.date,
        top_n: int,
        windows: Sequence[int],
        stock_list: Sequence[str],
        include_rank_change: bool,
        include_metrics: bool,
    ) -> Dict[str, Any]:
        is_realtime = False
        snapshot_slot = self._load_latest_snapshot_slot(trade_date=trade_date)
        prev_trade_date = self._load_previous_trade_date(trade_date)
        if snapshot_slot and snapshot_slot["trade_date"] == trade_date:
            is_realtime = True
        blocks: List[Dict[str, Any]] = []
        if is_realtime and snapshot_slot:
            realtime_df = self._load_snapshot_df(
                trade_date=trade_date,
                minute_index=snapshot_slot["minute_index"],
                codes=stock_list,
            )
            if realtime_df.empty:
                realtime_df = self._fetch_realtime_quotes_via_api(stock_list, "custom_list")
            for window in windows:
                items = self._build_realtime_ranked_items(
                    realtime_df=realtime_df,
                    codes=stock_list,
                    normalized_k=window,
                )[:top_n]
                self._attach_rank_change(
                    items=items,
                    trade_date=trade_date,
                    prev_trade_date=prev_trade_date,
                    window=window,
                    universe="",
                    include_rank_change=include_rank_change,
                )
                blocks.append(self._group_payload_block(group="custom_list", window=window, items=items, include_metrics=include_metrics))
            calc_mode = "custom_list_recomputed"
            snapshot_text = snapshot_slot["snapshot_time"].strftime("%Y-%m-%d %H:%M:%S") if snapshot_slot.get("snapshot_time") else ""
        else:
            for window in windows:
                ranked = self.momentum_service.build_daily_ranking(
                    trade_date=trade_date,
                    k=window,
                    top_n=top_n,
                    universe="",
                )
                filtered = [item for item in ranked if str(item.get("stock_code") or "").split(".", 1)[0] in set(stock_list)]
                if len(filtered) < top_n:
                    score_df = self.momentum_service.build_daily_score_frame(trade_date=trade_date, k=window)
                    filtered_df = score_df[score_df["code6"].isin(set(stock_list))].copy()
                    filtered_df.sort_values(by="momentum_value", ascending=False, inplace=True)
                    filtered = []
                    metric_map = self._load_daily_metric_map(trade_date=trade_date, codes=stock_list)
                    for idx, row in filtered_df.head(top_n).reset_index(drop=True).iterrows():
                        code6 = str(row["code6"])
                        metrics = metric_map.get(code6, {})
                        filtered.append(
                            {
                                "rank": idx + 1,
                                "stock_code": self.momentum_service.code_to_full_code(code6),
                                "stock_name": str(row.get("stock_name") or ""),
                                "momentum_value": round(float(row.get("momentum_value") or 0.0), 6),
                                "current_pct": metrics.get("current_pct"),
                                "current_amount": metrics.get("current_amount"),
                            }
                        )
                else:
                    metric_map = self._load_daily_metric_map(trade_date=trade_date, codes=stock_list)
                    normalized_filtered: List[Dict[str, Any]] = []
                    for idx, item in enumerate(filtered[:top_n]):
                        code6 = str(item.get("stock_code") or "").split(".", 1)[0]
                        metrics = metric_map.get(code6, {})
                        normalized_filtered.append(
                            {
                                "rank": idx + 1,
                                "stock_code": str(item.get("stock_code") or ""),
                                "stock_name": str(item.get("stock_name") or ""),
                                "momentum_value": round(float(item.get("momentum_value") or 0.0), 6),
                                "current_pct": metrics.get("current_pct"),
                                "current_amount": metrics.get("current_amount"),
                            }
                        )
                    filtered = normalized_filtered
                self._attach_rank_change(
                    items=filtered,
                    trade_date=trade_date,
                    prev_trade_date=prev_trade_date,
                    window=window,
                    universe="",
                    include_rank_change=include_rank_change,
                )
                blocks.append(self._group_payload_block(group="custom_list", window=window, items=filtered, include_metrics=include_metrics))
            calc_mode = "custom_list_recomputed"
            snapshot_text = ""
        return {
            "tool": "个股动量排名",
            "ok": True,
            "meta": {
                "trade_date": trade_date.strftime("%Y-%m-%d"),
                "snapshot_time": snapshot_text,
                "calc_mode": calc_mode,
                "prev_trade_date": prev_trade_date.strftime("%Y-%m-%d") if prev_trade_date else "",
                "windows": [int(window) for window in windows],
                "top_n": int(top_n),
                "groups": [],
                "scope_type": "custom_list",
            },
            "data": blocks,
            "error": "",
        }

    def build_unified_payload(
        self,
        *,
        trade_date: Optional[dt.date],
        top_n: int,
        windows: Sequence[int],
        groups: Sequence[str],
        stock_list: Sequence[str],
        include_rank_change: bool,
        include_metrics: bool,
    ) -> Dict[str, Any]:
        latest_slot = self._load_latest_snapshot_slot()
        effective_trade_date = trade_date or (latest_slot["trade_date"] if latest_slot else dt.date.today())
        normalized_top_n = self.momentum_service.normalize_int(top_n, 10, minimum=1, maximum=100)
        normalized_windows = self._normalize_windows(list(windows))

        if stock_list:
            return self._build_custom_list(
                trade_date=effective_trade_date,
                top_n=normalized_top_n,
                windows=normalized_windows,
                stock_list=stock_list,
                include_rank_change=include_rank_change,
                include_metrics=include_metrics,
            )

        normalized_groups = self._normalize_groups(list(groups))
        if latest_slot and latest_slot["trade_date"] == effective_trade_date:
            return self._build_grouped_realtime(
                trade_date=effective_trade_date,
                top_n=normalized_top_n,
                windows=normalized_windows,
                groups=normalized_groups,
                include_rank_change=include_rank_change,
                include_metrics=include_metrics,
            )
        return self._build_grouped_daily(
            trade_date=effective_trade_date,
            top_n=normalized_top_n,
            windows=normalized_windows,
            groups=normalized_groups,
            include_rank_change=include_rank_change,
            include_metrics=include_metrics,
        )

    def build_payload(self, *, k: int = 5, t: int = 20, universe: str = "") -> List[Dict[str, Any]]:
        normalized_k = self.momentum_service.normalize_int(k, 5, minimum=1, maximum=250)
        normalized_t = self.momentum_service.normalize_int(t, 20, minimum=1, maximum=500)
        normalized_universe = self.momentum_service.normalize_universe(universe)
        codes = self.momentum_service.load_universe_codes(normalized_universe)

        snapshot_slot = self._load_latest_snapshot_slot()
        if snapshot_slot:
            scope_key = f"{normalized_universe or 'full_market'}|k_{normalized_k}"
            cache_key = self._build_cache_key(
                trade_date=snapshot_slot["trade_date"],
                minute_index=int(snapshot_slot["minute_index"]),
                k=normalized_k,
                scope_key=scope_key,
            )
            cached = self._load_cache(cache_key)
            if cached is not None:
                return cached[:normalized_t]

            realtime_df = self._load_snapshot_df(
                trade_date=snapshot_slot["trade_date"],
                minute_index=int(snapshot_slot["minute_index"]),
                codes=codes,
            )
            if realtime_df is not None and not realtime_df.empty:
                ranked = self._build_realtime_ranked_items(
                    realtime_df=realtime_df,
                    codes=codes,
                    normalized_k=normalized_k,
                )
                self._save_cache(cache_key, ranked)
                return ranked[:normalized_t]

        realtime_df = self._fetch_realtime_quotes_via_api(codes, normalized_universe)
        if realtime_df is None or realtime_df.empty:
            raise ValueError("实时行情为空")
        ranked = self._build_realtime_ranked_items(
            realtime_df=realtime_df,
            codes=codes,
            normalized_k=normalized_k,
        )
        return ranked[:normalized_t]


def run(args: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(args or {})
    tool = StockMomentumRankingTool()
    try:
        payload = tool.build_payload(
            k=params.get("k", 5),
            t=params.get("t", 20),
            universe=str(params.get("universe") or params.get("scope") or "").strip(),
        )
        return {
            "tool": "实时个股动量排名",
            "ok": True,
            "data": payload,
            "error": "",
        }
    except Exception as exc:
        return {
            "tool": "实时个股动量排名",
            "ok": False,
            "data": [],
            "error": str(exc),
        }


def run_unified(args: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(args or {})
    tool = StockMomentumRankingTool()
    try:
        payload = tool.build_unified_payload(
            trade_date=tool._normalize_trade_date(params.get("trade_date")),
            top_n=params.get("top_n", 10),
            windows=tool._normalize_windows(params.get("windows")),
            groups=tool._normalize_groups(params.get("groups")),
            stock_list=tool._normalize_stock_list(params.get("stock_list")),
            include_rank_change=tool._normalize_bool(params.get("include_rank_change"), True),
            include_metrics=tool._normalize_bool(params.get("include_metrics"), True),
        )
        return payload
    except Exception as exc:
        return {
            "tool": "个股动量排名",
            "ok": False,
            "data": [],
            "meta": {},
            "error": str(exc),
        }
