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
    """Use the historical interval universe; only an explicit limit samples companies."""
    if spec.symbols:
        return sorted(set(symbol.upper() for symbol in spec.symbols))
    sql = ("SELECT b.stk_code FROM kcrp_stock_baseinfo b WHERE b.list_date<=%s"
           " AND (b.delist_date IS NULL OR b.delist_date>=%s)")
    args = [spec.end, spec.start]
    if spec.industries:
        if "kcrp_stock_industry" not in catalog:
            raise ValueError("requested industry selection requires historical industry data")
        marks = ",".join(["%s"] * len(spec.industries))
        sql += (" AND EXISTS (SELECT 1 FROM kcrp_stock_industry i WHERE i.stk_code=b.stk_code"
                " AND i.industry_type='SW2021' AND i.level=1 AND i.begin_date<=%s"
                " AND (i.end_date IS NULL OR i.end_date>%s)"
                f" AND i.industry_name IN ({marks}))")
        args += [spec.end, spec.start, *spec.industries]
    rows = query(conn, sql + " ORDER BY b.stk_code", tuple(args))
    pool = [row["stk_code"] for row in rows if re.fullmatch(r"(?:(?:60|68)\d{4}\.SH|(?:00|30)\d{4}\.SZ|\d{6}\.BJ)", row["stk_code"])]
    if spec.max_symbols is None:
        return sorted(pool)
    return sorted(random.Random(spec.seed).sample(pool, min(spec.max_symbols, len(pool))))


def _symbol_batches(symbols, size=64):
    for start in range(0, len(symbols), size):
        yield symbols[start:start + size]


def _date_batches(start, end, days=366):
    left, last = pd.Timestamp(start), pd.Timestamp(end)
    while left <= last:
        right = min(left + pd.Timedelta(days=days - 1), last)
        yield left.date().isoformat(), right.date().isoformat()
        left = right + pd.Timedelta(days=1)


def _source_batches(conn, columns, table, symbols, start, end, *, date_column="trade_date",
                    conditions="", order_by="stk_code,trade_date", symbol_batch_size=64,
                    days=366, row_limit=50000):
    """Bound every fetch; batches cover disjoint symbol/date intervals without truncation."""
    for batch in _symbol_batches(symbols, symbol_batch_size):
        marks = ",".join(["%s"] * len(batch))
        for left, right in _date_batches(start, end, days):
            sql = (f"SELECT {columns} FROM {table} WHERE stk_code IN ({marks})"
                   f" AND {date_column}>=%s AND {date_column}<%s {conditions} ORDER BY {order_by}")
            stop = (pd.Timestamp(right) + pd.Timedelta(days=1)).date().isoformat()
            yield _bounded(conn, sql, (*batch, left, stop), limit=row_limit)


def _concat(frames):
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _market_calendar(conn, start, end):
    """Same-source market dates, independent of the selected companies' quote coverage."""
    dates = []
    for left, right in _date_batches(start, end):
        stop = (pd.Timestamp(right) + pd.Timedelta(days=1)).date().isoformat()
        frame = _bounded(conn, "SELECT DISTINCT trade_date FROM kcrp_stock_price "
                         "WHERE trade_date>=%s AND trade_date<%s ORDER BY trade_date",
                         (left, stop), limit=366)
        if not frame.empty:
            dates.extend(pd.to_datetime(frame.trade_date).dt.strftime("%Y-%m-%d").tolist())
    return sorted(set(dates))


def load_kingdom(spec, *, env_name=None):
    """Read bounded batches from one connection; return an in-memory daily research panel."""
    warnings = []
    with kingdom_connection(env_name) as conn:
        catalog = inventory(conn)
        symbols = select_symbols(conn, catalog, spec)
        if not symbols:
            raise ValueError("no companies in requested historical universe")
        daily_batches = []
        for frame in _source_batches(conn, "stk_code AS symbol, trade_date AS date, open, high, low, close, "
                                     "preclose, adjopen, adjhigh, adjlow, adjclose, volume, amount, turn_ratio",
                                     "kcrp_stock_price", symbols, spec.start, spec.end):
            if frame.empty:
                continue
            frame["date"] = pd.to_datetime(frame.date)
            for col in frame.columns.difference(["symbol", "date"]):
                frame[col] = pd.to_numeric(frame[col], errors="coerce")
            daily_batches.append(frame)
        daily = _concat(daily_batches)
        del daily_batches
        if daily.empty:
            raise ValueError("no daily bars in requested interval")
        market_calendar = _market_calendar(conn, spec.start, spec.end)
        events = []
        if "kcrp_stock_pricevaluate" in catalog:
            values = []
            for val in _source_batches(conn, "stk_code AS symbol, trade_date, total_mv, pe_ttm, pb_mrq",
                                       "kcrp_stock_pricevaluate", symbols, spec.start, spec.end):
                if not val.empty:
                    val["available_at"] = pd.to_datetime(val.pop("trade_date")) + pd.Timedelta(days=1)
                    for column in ("total_mv", "pe_ttm", "pb_mrq"):
                        val[column] = pd.to_numeric(val[column], errors="coerce")
                    values.append(val)
            if values:
                events.append(_concat(values))
            del values
            warnings.append("估值按下一自然日可用处理；原库未提供历史修订版本，不能证明原始发布时点。")
        if "kcrp_stock_industry" in catalog:
            daily["industry"] = "unknown"
            company_rows = daily.groupby("symbol", sort=False).indices
            for batch in _symbol_batches(symbols):
                marks = ",".join(["%s"] * len(batch))
                ind = _bounded(conn, "SELECT stk_code AS symbol, begin_date, end_date, industry_name "
                               f"FROM kcrp_stock_industry WHERE stk_code IN ({marks}) "
                               "AND industry_type='SW2021' AND level=1 AND begin_date<=%s "
                               "AND (end_date IS NULL OR end_date>%s) ORDER BY stk_code,begin_date",
                               (*batch, spec.end, spec.start), limit=50000)
                for row in ind.itertuples():
                    positions = company_rows.get(row.symbol)
                    if positions is None:
                        continue
                    # Only scan this company's rows, avoiding an all-market scan per membership.
                    dates = daily.loc[positions, "date"].dt.strftime("%Y-%m-%d")
                    mask = dates >= str(row.begin_date)[:10]
                    if pd.notna(row.end_date):
                        mask &= dates < str(row.end_date)[:10]
                    daily.loc[dates.index[mask], "industry"] = row.industry_name
            warnings.append("行业按 SW2021 生效区间对齐，未取得分类历史版本；用于研究特征和分组，非严格历史成分认证。")
        if "kcrp_news_info" in catalog:
            news_counts = []
            seen_news = set()
            for news in _source_batches(conn, "stk_code AS symbol, ann_date, create_time, news_id",
                                        "kcrp_news_info", symbols, spec.start, spec.end,
                                        date_column="ann_date", order_by="stk_code,ann_date"):
                if news.empty:
                    continue
                news = news.drop_duplicates(["symbol", "news_id"])
                keys = list(zip(news.symbol, news.news_id))
                news = news.loc[[key not in seen_news for key in keys]]
                seen_news.update(keys)
                if news.empty:
                    continue
                # A date-only publication is first usable next day; late ingestion cannot backfill history.
                news["available_at"] = pd.concat([
                    pd.to_datetime(news.ann_date) + pd.Timedelta(days=1),
                    pd.to_datetime(news.create_time)], axis=1).max(axis=1).dt.ceil("D")
                news_counts.append(news.groupby(["symbol", "available_at"]).size().rename("news_count").reset_index())
            if news_counts:
                counts = _concat(news_counts).groupby(["symbol", "available_at"]).news_count.sum()
                # Counts are flows: emit zero-count days for each symbol, then roll calendar days.
                complete = pd.MultiIndex.from_product([symbols, pd.date_range(spec.start, spec.end)],
                                                       names=["symbol", "available_at"])
                counts = counts.reindex(complete, fill_value=0).reset_index()
                counts["news_count_7d"] = counts.groupby("symbol").news_count.transform(lambda s: s.rolling(7, min_periods=1).sum())
                events.append(counts.drop(columns="news_count"))
            warnings.append("新闻热度为已入库新闻的7日数量代理，不代表全网热度；不读取或上传新闻正文。")
        if spec.include_minute:
            table = "aiia_stock_realtime_minute_snapshot"
            if table not in catalog:
                warnings.append("当前连接没有分钟快照表，本轮未使用分钟特征。")
            else:
                minute_days = []
                for minute in _source_batches(conn, "stk_code AS symbol, bar_end_time, latest_price AS close, volume",
                                              table, [s[:6] for s in symbols], spec.start, spec.end,
                                              conditions="AND kline_type='1m' AND period_minutes=1 AND is_finalized=1",
                                              order_by="stk_code,bar_end_time", symbol_batch_size=8,
                                              days=31, row_limit=100000):
                    if not minute.empty:
                        minute_days.append(minute_features(minute, symbols))
                if minute_days:
                    events.append(_concat(minute_days))
        daily["industry"] = daily.get("industry", "unknown")
    daily.attrs["market_calendar"] = market_calendar
    explicit_limit = spec.max_symbols is not None and not spec.symbols
    if explicit_limit:
        warnings.append(f"本轮显式设置公司试跑上限 {spec.max_symbols}，实际选择 {len(symbols)} 家；不是全市场事件样本。")
    return daily, events, {"source": "kingdomai", "symbols": symbols, "tables": catalog,
                           "warnings": warnings, "price_basis": "vendor_adjusted",
                           "market_calendar": {"source_table": "kcrp_stock_price",
                               "scope": "distinct dates across all companies in the same configured connection",
                               "sessions": len(market_calendar),
                               "first_session": market_calendar[0] if market_calendar else None,
                               "last_session": market_calendar[-1] if market_calendar else None},
                           "universe": "companies listed during any part of the research interval, including later IPOs and delistings; requested industries use overlapping membership then per-date filtering",
                           "universe_selection": {
                               "method": "explicit_symbols" if spec.symbols else "seeded_company_sample" if explicit_limit else "historical_interval_universe",
                               "requested_limit": spec.max_symbols, "selected_count": len(symbols),
                               "seed": spec.seed if explicit_limit else None,
                               "daily_batch_symbols": 64, "daily_batch_days": 366,
                               "daily_query_row_limit": 50000,
                           }}


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
