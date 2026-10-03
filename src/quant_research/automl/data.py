from __future__ import annotations

from contextlib import contextmanager
import os
import random
import re
from urllib.parse import unquote, urlparse

import pandas as pd
import pymysql


@contextmanager
def kingdom_connection(env_name=None):
    """Explicit configured kingdomai connection, SELECT-only transaction, bounded queries."""
    names = [env_name] if env_name else ["KINGDOMAI_DB_URL", "BI_DB_URL", "BUSINESS_DB_URL"]
    url = next((os.environ[n] for n in names if os.environ.get(n)), "")
    p = urlparse(url.replace("mysql+pymysql://", "mysql://", 1))
    if p.scheme != "mysql" or p.path.strip("/") != "kingdomai":
        raise ValueError("configure a kingdomai URL in KINGDOMAI_DB_URL or BI_DB_URL")
    conn = pymysql.connect(host=p.hostname, port=p.port or 3306, user=unquote(p.username or ""),
                           password=unquote(p.password or ""), database="kingdomai", charset="utf8mb4",
                           cursorclass=pymysql.cursors.DictCursor, connect_timeout=8,
                           read_timeout=60, write_timeout=15, autocommit=False)
    try:
        with conn.cursor() as c:
            c.execute("SET SESSION MAX_EXECUTION_TIME=45000")
            c.execute("SET SESSION TRANSACTION READ ONLY")
            c.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
        yield conn
    finally:
        conn.rollback()
        conn.close()


def query(conn, sql, args=()):
    with conn.cursor() as c:
        c.execute(sql, args)
        return list(c.fetchall())


def inventory(conn):
    rows = query(conn, "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE FROM information_schema.COLUMNS "
                       "WHERE TABLE_SCHEMA=DATABASE() ORDER BY TABLE_NAME, ORDINAL_POSITION")
    tables = {}
    for r in rows:
        tables.setdefault(r["TABLE_NAME"], {})[r["COLUMN_NAME"]] = r["DATA_TYPE"]
    return tables


def _bounded(conn, sql, args, limit=500000):
    rows = query(conn, sql + " LIMIT %s", (*args, limit + 1))
    if len(rows) > limit:
        raise ValueError("source row budget exceeded; reduce dates or symbols (no silent truncation)")
    return pd.DataFrame(rows)


def select_symbols(conn, catalog, spec):
    """Sample within the requested historical industry universe, before applying per-day filters."""
    if spec.symbols:
        symbols = sorted(set(symbol.upper() for symbol in spec.symbols))
        if len(symbols) > spec.max_symbols:
            raise ValueError("explicit symbols exceed max_symbols")
        return symbols
    sql = "SELECT b.stk_code FROM kcrp_stock_baseinfo b WHERE b.list_date<=%s AND b.delist_date>=%s"
    args = [spec.start, spec.start]
    if spec.industries:
        if "kcrp_stock_industry" not in catalog:
            raise ValueError("requested industry selection requires historical industry data")
        marks = ",".join(["%s"] * len(spec.industries))
        sql += (" AND EXISTS (SELECT 1 FROM kcrp_stock_industry i WHERE i.stk_code=b.stk_code"
                " AND i.industry_type='SW2021' AND i.level=1 AND i.begin_date<=%s AND i.end_date>%s"
                f" AND i.industry_name IN ({marks}))")
        args += [spec.start, spec.start, *spec.industries]
    rows = query(conn, sql + " ORDER BY b.stk_code", tuple(args))
    pool = [row["stk_code"] for row in rows if re.fullmatch(r"(?:(?:60|68)\d{4}\.SH|(?:00|30)\d{4}\.SZ|\d{6}\.BJ)", row["stk_code"])]
    return sorted(random.Random(spec.seed).sample(pool, min(spec.max_symbols, len(pool))))


def load_kingdom(spec, *, env_name=None):
    """Return daily prices plus only locally derived, timestamped auxiliary facts."""
    warnings = []
    with kingdom_connection(env_name) as conn:
        catalog = inventory(conn)
        symbols = select_symbols(conn, catalog, spec)
        if len(symbols) < 5:
            raise ValueError("at least five companies are required for company holdout")
        marks = ",".join(["%s"] * len(symbols))
        args = (*symbols, spec.start, spec.end)
        where = f"stk_code IN ({marks}) AND trade_date BETWEEN %s AND %s"
        daily = _bounded(conn, "SELECT stk_code AS symbol, trade_date AS date, open, high, low, close, "
                         "preclose, adjopen, adjhigh, adjlow, adjclose, volume, amount, turn_ratio "
                         f"FROM kcrp_stock_price WHERE {where} ORDER BY stk_code,trade_date", args)
        if daily.empty:
            raise ValueError("no daily bars in requested interval")
        daily["date"] = pd.to_datetime(daily.date)
        for col in daily.columns.difference(["symbol", "date"]):
            daily[col] = pd.to_numeric(daily[col], errors="coerce")
        events = []
        if "kcrp_stock_pricevaluate" in catalog:
            val = _bounded(conn, "SELECT stk_code AS symbol, trade_date, total_mv, pe_ttm, pb_mrq "
                           f"FROM kcrp_stock_pricevaluate WHERE {where} ORDER BY stk_code,trade_date", args)
            if not val.empty:
                val["available_at"] = pd.to_datetime(val.pop("trade_date")) + pd.Timedelta(days=1)
                events.append(val)
            warnings.append("估值按下一自然日可用处理；原库未提供历史修订版本，不能证明原始发布时点。")
        if "kcrp_stock_industry" in catalog:
            ind = _bounded(conn, "SELECT stk_code AS symbol, begin_date, end_date, industry_name "
                           f"FROM kcrp_stock_industry WHERE stk_code IN ({marks}) "
                           "AND industry_type='SW2021' AND level=1 AND begin_date<=%s ORDER BY stk_code,begin_date",
                           (*symbols, spec.end))
            if not ind.empty:
                # Effective intervals may be revised; evidence explicitly records that limitation.
                daily["industry"] = "unknown"
                for row in ind.itertuples():
                    mask = (daily.symbol == row.symbol) & (daily.date >= str(row.begin_date)) & (daily.date < str(row.end_date))
                    daily.loc[mask, "industry"] = row.industry_name
            warnings.append("行业按 SW2021 生效区间对齐，未取得分类历史版本；用于研究特征和分组，非严格历史成分认证。")
        if "kcrp_news_info" in catalog:
            news = _bounded(conn, "SELECT stk_code AS symbol, ann_date, create_time, news_id "
                            f"FROM kcrp_news_info WHERE stk_code IN ({marks}) AND ann_date BETWEEN %s AND %s "
                            "ORDER BY stk_code,ann_date", args)
            if not news.empty:
                news = news.drop_duplicates(["symbol", "news_id"])
                # A date-only publication is first usable next day; late ingestion cannot backfill history.
                news["available_at"] = pd.concat([
                    pd.to_datetime(news.ann_date) + pd.Timedelta(days=1),
                    pd.to_datetime(news.create_time)], axis=1).max(axis=1)
                news["available_at"] = news.available_at.dt.ceil("D")
                counts = news.groupby(["symbol", "available_at"]).size().rename("news_count").reset_index()
                # Counts are flows, not an as-of state: explicitly insert zero-count calendar days.
                complete = pd.MultiIndex.from_product([symbols, pd.date_range(spec.start, spec.end)],
                                                       names=["symbol", "available_at"])
                counts = counts.set_index(["symbol", "available_at"]).reindex(complete, fill_value=0).reset_index()
                counts["news_count_7d"] = counts.groupby("symbol").news_count.transform(lambda s: s.rolling(7, min_periods=1).sum())
                events.append(counts.drop(columns="news_count"))
            warnings.append("新闻热度为已入库新闻的7日数量代理，不代表全网热度；不读取或上传新闻正文。")
        if spec.include_minute:
            table = "aiia_stock_realtime_minute_snapshot"
            if table not in catalog:
                warnings.append("当前连接没有分钟快照表，本轮未使用分钟特征。")
            else:
                minute = _bounded(conn, "SELECT stk_code AS symbol, bar_end_time, latest_price AS close, "
                                  f"volume FROM {table} WHERE stk_code IN ({marks}) AND trade_date BETWEEN %s AND %s "
                                  "AND kline_type='1m' AND period_minutes=1 AND is_finalized=1 ORDER BY stk_code,bar_end_time",
                                  (*[s[:6] for s in symbols], spec.start, spec.end))
                events.append(minute_features(minute, symbols))
        daily["industry"] = daily.get("industry", "unknown")
    return daily, events, {"source": "kingdomai", "symbols": symbols, "tables": catalog,
                           "warnings": warnings, "price_basis": "vendor_adjusted",
                           "universe": "listed at start, including companies subsequently delisted; requested industries use membership at start then per-date filtering"}


def minute_features(frame, symbols):
    if frame.empty:
        return pd.DataFrame()
    frame = frame.copy()
    frame["symbol"] = frame.symbol.map({s[:6]: s for s in symbols})
    frame["bar_end_time"] = pd.to_datetime(frame.bar_end_time)
    frame = frame[frame.bar_end_time.dt.time <= pd.Timestamp("15:00").time()]
    frame["close"] = pd.to_numeric(frame.close)
    frame["day"] = frame.bar_end_time.dt.normalize()
    frame["r"] = frame.groupby(["symbol", "day"]).close.pct_change(fill_method=None)
    out = frame.groupby(["symbol", "day"]).agg(minute_volatility=("r", "std"), minute_bars=("close", "count")).reset_index()
    out["available_at"] = out.pop("day") + pd.Timedelta(hours=15, minutes=5)
    return out


def asof_features(panel, events):
    """Extension contract: symbol, available_at, numeric/categorical features; no future fill."""
    out = panel.copy()
    for event in events:
        if event.empty:
            continue
        event = event.copy()
        event["available_at"] = pd.to_datetime(event.available_at)
        if event[["symbol", "available_at"]].duplicated().any():
            raise ValueError("ambiguous auxiliary revisions at the same availability timestamp")
        columns = event.columns.difference(["symbol", "available_at"])
        if set(columns) & (set(out.columns) | {"forward_return", "entry_date", "label_end", "prediction", "history_ready"}):
            raise ValueError("auxiliary feature names must not overwrite prices or labels")
        for col in columns:
            if col != "industry":
                event[col] = pd.to_numeric(event[col], errors="raise")
        out = pd.merge_asof(out.sort_values("decision_at"), event.sort_values("available_at"),
                            left_on="decision_at", right_on="available_at", by="symbol", direction="backward")
        out = out.drop(columns="available_at")
    return out.sort_values(["symbol", "date"]).reset_index(drop=True)
