"""Append Top1 stock 14:40 turnover history to the user-edited daily report."""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

import pandas as pd

from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.mk_minute_archive import COMPACT_ROOT, day_file


REPORT = Path("docs/stock_automl_runs/20261011_market_state_asof/daily_2026.csv")
MARKET_OLD = Path("docs/stock_automl_runs/20261011_mk_1m_market_amount_board/experiments/daily_market.csv")
MARKET_NEW = Path("docs/stock_automl_runs/20261011_aug_oct_independent20/market_context.csv")
CACHE = Path("outputs/stock_automl/top1_stock_amount_1440_2026.csv")
MARKET_TAG = "大盘成交额低于此前20日全部（标签）"
STOCK_COLUMNS = [
    "个股当日截至14:40成交额（亿元）",
    "个股此前20日成交额（亿元，远到近）",
    "个股此前5日成交额（亿元，远到近）",
    "个股此前20日均额（亿元）",
    "个股此前20日中位数（亿元）",
    "个股此前5日均额（亿元）",
    "个股此前5日递增次数（共4次比较）",
    "个股当日相对前20日中位数",
    "个股当日在前20日中的百分位",
    "个股此前20日有效天数",
    "个股成交额低于此前20日全部（标签）",
    "个股成交额低于此前20日中位数（标签）",
]


def normalize_date(value: str) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def required_bars(rows: list[dict[str, str]], calendar: list[str]) -> dict[str, set[str]]:
    positions = {date: index for index, date in enumerate(calendar)}
    needed: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if not row.get("Top1代码"):
            continue
        day = normalize_date(row["选股日"])
        code = row["Top1代码"].zfill(6)
        pos = positions[day]
        if pos < 20:
            raise ValueError(f"Fewer than 20 prior trading days for {day}")
        for date in calendar[pos-20:pos+1]:
            needed[date].add(code)
    return needed


def archive_amounts(
    needed: dict[str, set[str]], *, root: Path = COMPACT_ROOT,
    before: str | None = "2026-08-01",
) -> list[dict]:
    records = []
    for index, (day, codes) in enumerate(sorted(needed.items()), start=1):
        if before is not None and day >= before:
            continue
        path = day_file(root, day)
        bars = pd.read_csv(path, usecols=["time", "symbol", "turnover"],
                           dtype={"time": str, "symbol": str})
        bars = bars[bars.time.str[-8:].between("09:31:00", "14:40:00")]
        bars["symbol6"] = bars.symbol.str[:6]
        bars = bars[bars.symbol6.isin(codes)]
        groups = bars.groupby("symbol6", sort=False)
        for code, group in groups:
            valid = (len(group) == 220 and group.time.nunique() == 220 and
                     group.turnover.notna().all() and group.turnover.ge(0).all())
            if valid:
                records.append({"date": day, "symbol6": code,
                                "amount_100m_cny": float(group.turnover.sum() / 1e8),
                                "bars": 220, "source": "MK未复权1m"})
        if index % 20 == 0:
            print(f"MK archive: {index}/{len(needed)} calendar days", flush=True)
    return records


def database_amounts(needed: dict[str, set[str]], env_file: Path) -> list[dict]:
    records = []
    with db_connection(env_file) as db:
        for index, (day, codes) in enumerate(sorted(needed.items()), start=1):
            if day < "2026-08-01":
                continue
            placeholders = ",".join(["%s"] * len(codes))
            bars = query(db, f"""SELECT LEFT(stk_code,6) AS symbol6,
                COUNT(*) AS bars, COUNT(DISTINCT bar_end_time) AS distinct_bars,
                SUM(amount) / 100000000 AS amount_100m_cny,
                SUM(is_finalized=1 AND is_fallback=0 AND
                    source_snapshot_time=bar_end_time) AS exact_bars
                FROM aiia_stock_realtime_minute_snapshot_full
                WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
                  AND bar_end_time BETWEEN %s AND %s
                  AND LEFT(stk_code,6) IN ({placeholders})
                GROUP BY LEFT(stk_code,6)""",
                (day, day + " 09:31:00", day + " 14:40:00", *sorted(codes)))
            for row in bars.itertuples(index=False):
                if (int(row.bars) == 220 and int(row.distinct_bars) == 220 and
                        int(row.exact_bars) == 220 and
                        pd.notna(row.amount_100m_cny) and row.amount_100m_cny >= 0):
                    records.append({"date": day, "symbol6": str(row.symbol6).zfill(6),
                                    "amount_100m_cny": float(row.amount_100m_cny),
                                    "bars": 220, "source": "分钟快照1m"})
            if index % 10 == 0:
                print(f"DB minute snapshots: {index}/{len(needed)} calendar days",
                      flush=True)
    return records


def stock_columns(current: float | None, prior: list[float | None]) -> dict[str, str]:
    if len(prior) != 20:
        raise ValueError("A stock row must have 20 prior trading-day slots")
    valid = [value for value in prior if value is not None]
    result = dict.fromkeys(STOCK_COLUMNS, "")
    result["个股此前20日有效天数"] = str(len(valid))
    result["个股此前20日成交额（亿元，远到近）"] = ";".join(
        "" if value is None else f"{value:.4f}" for value in prior)
    result["个股此前5日成交额（亿元，远到近）"] = ";".join(
        "" if value is None else f"{value:.4f}" for value in prior[-5:])
    if current is not None:
        result["个股当日截至14:40成交额（亿元）"] = f"{current:.4f}"
    if len(valid) != 20:
        return result
    values = pd.Series(prior, dtype=float)
    median = float(values.median())
    result["个股此前20日均额（亿元）"] = f"{values.mean():.4f}"
    result["个股此前20日中位数（亿元）"] = f"{median:.4f}"
    result["个股此前5日均额（亿元）"] = f"{values.iloc[-5:].mean():.4f}"
    result["个股此前5日递增次数（共4次比较）"] = str(int(
        values.iloc[-5:].diff().iloc[1:].gt(0).sum()))
    if current is not None and median > 0:
        result["个股当日相对前20日中位数"] = f"{current / median:.6f}"
        percentile = (int(values.lt(current).sum()) +
                      int(values.eq(current).sum()) / 2) / 20
        result["个股当日在前20日中的百分位"] = f"{percentile:.6f}"
        result["个股成交额低于此前20日全部（标签）"] = (
            "是" if current < values.min() else "否")
        result["个股成交额低于此前20日中位数（标签）"] = (
            "是" if current < median else "否")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--cache", type=Path, default=CACHE)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    with args.report.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    if not {"选股日", "Top1代码", "当日在前20日中的百分位"}.issubset(fields):
        raise ValueError("The user-edited Top1 report is missing its core columns")
    market = pd.concat([
        pd.read_csv(MARKET_OLD, usecols=["trade_date"]),
        pd.read_csv(MARKET_NEW, usecols=["trade_date"]),
    ], ignore_index=True).drop_duplicates("trade_date").sort_values("trade_date")
    calendar = market.trade_date.tolist()
    needed = required_bars(rows, calendar)
    if args.cache.exists():
        values = pd.read_csv(args.cache, dtype={"date": str, "symbol6": str})
    else:
        values = pd.DataFrame(archive_amounts(needed) +
                              database_amounts(needed, args.env_file))
        args.cache.parent.mkdir(parents=True, exist_ok=True)
        values.to_csv(args.cache, index=False)
    if values.duplicated(["date", "symbol6"]).any():
        raise ValueError("Duplicate stock/date turnover in the cache")
    amount = {(row.date, row.symbol6): row.amount_100m_cny
              for row in values.itertuples(index=False)}
    positions = {date: index for index, date in enumerate(calendar)}
    missing_current = 0
    complete_history = 0
    for row in rows:
        for column in STOCK_COLUMNS:
            row.pop(column, None)
        row.pop(MARKET_TAG, None)
        row.pop("大盘当日成交额为前20日最低时", None)
        market_percentile = row["当日在前20日中的百分位"]
        row[MARKET_TAG] = (
            "是" if float(market_percentile) == 0 else "否") if market_percentile else ""
        if not row.get("Top1代码"):
            row.update(dict.fromkeys(STOCK_COLUMNS, ""))
            continue
        day = normalize_date(row["选股日"])
        code = row["Top1代码"].zfill(6)
        pos = positions[day]
        previous = [amount.get((date, code)) for date in calendar[pos-20:pos]]
        current = amount.get((day, code))
        result = stock_columns(current, previous)
        row.update(result)
        missing_current += current is None
        complete_history += len([value for value in previous if value is not None]) == 20
    if missing_current:
        raise ValueError(f"{missing_current} selected stocks lack T-day 14:40 turnover")
    output_fields = [field for field in fields if field not in STOCK_COLUMNS + [MARKET_TAG, "大盘当日成交额为前20日最低时"]]
    output_fields += [MARKET_TAG] + STOCK_COLUMNS
    with args.report.open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=output_fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"rows={len(rows)}, selected={sum(bool(row.get('Top1代码')) for row in rows)}, "
          f"complete_stock_history={complete_history}, cache_rows={len(values)}")


if __name__ == "__main__":
    main()
