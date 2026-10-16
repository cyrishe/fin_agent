"""Evaluate an as-of Top1 stock-turnover condition on 2025 May–July replay picks."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts.append_automl_stock_amount_state import (
    archive_amounts,
    required_bars,
    stock_columns,
)
from scripts.mk_minute_archive import COMPACT_ROOT_2025


BASE = Path("docs/stock_automl_runs/20261011_mk_2025_mar_aug_backtest")
OUT = Path("docs/stock_automl_runs/20261011_stock_amount_gate_2025_may_jul")
CACHE = Path("outputs/stock_automl/stock_amount_1440_2025_may_jul.csv")


def summarize(group: pd.DataFrame) -> dict[str, float | int]:
    values = group["原模型09:40收益（%）"]
    return {
        "笔数": len(values),
        "单笔平均收益（%）": round(float(values.mean()), 6) if len(values) else 0.0,
        "收益中位数（%）": round(float(values.median()), 6) if len(values) else 0.0,
        "正收益笔数": int(values.gt(0).sum()),
        "逐笔全额复利代理（%）": round(float(((1 + values / 100).prod() - 1) * 100), 6),
    }


def main() -> None:
    trades = pd.read_csv(BASE / "trades.csv", dtype={"symbol6": str})
    trades = trades[
        trades.selection_rank.eq(1)
        & trades.signal_date.between("2025-05-01", "2025-07-31")
        & trades.policy.eq("只去掉14:40涨停ST")
    ].sort_values("signal_date").copy()
    if len(trades) != 60 or trades.signal_date.nunique() != 60:
        raise ValueError("Expected 60 distinct original Top1 picks for May–July")
    original_daily = pd.read_csv(BASE / "daily.csv", dtype={"Top1股票": str})
    original_daily = original_daily[original_daily["选股日"].between("2025-05-01", "2025-07-31")]
    check = trades.merge(original_daily[["选股日", "Top1股票", "Top1结果",
                                         "Top1固定09:40收益（%）"]],
                         left_on="signal_date", right_on="选股日", validate="one_to_one")
    class_names = {"ge3": "达标", "1to3": "可接受", "0to1": "中性", "lt0": "错误"}
    if not (check.symbol6.eq(check["Top1股票"]).all() and
            check.actual_class.map(class_names).eq(check["Top1结果"]).all() and
            ((check.actual_0940_return * 100 - check["Top1固定09:40收益（%）"])
             .abs() < 0.000001).all()):
        raise ValueError("Original Top1 or fixed 09:40 return does not match daily report")

    market = pd.read_csv(BASE / "market_amount_1440.csv")
    market = market.sort_values("trade_date")
    calendar = market.trade_date.tolist()
    needed = required_bars([
        {"选股日": row.signal_date, "Top1代码": row.symbol6}
        for row in trades.itertuples(index=False)
    ], calendar)
    if CACHE.exists():
        values = pd.read_csv(CACHE, dtype={"date": str, "symbol6": str})
    else:
        values = pd.DataFrame(archive_amounts(needed, root=COMPACT_ROOT_2025,
                                              before=None))
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        values.to_csv(CACHE, index=False)
    if values.duplicated(["date", "symbol6"]).any():
        raise ValueError("Duplicate stock amount in cache")
    amount = {(row.date, row.symbol6): float(row.amount_100m_cny)
              for row in values.itertuples(index=False)}
    market_amount = dict(zip(market.trade_date, market.amount_1440_100m_cny))
    positions = {date: index for index, date in enumerate(calendar)}
    records = []
    for trade in trades.itertuples(index=False):
        day = trade.signal_date
        previous_days = calendar[positions[day]-20:positions[day]]
        previous = [amount.get((date, trade.symbol6)) for date in previous_days]
        current = amount.get((day, trade.symbol6))
        stock = stock_columns(current, previous)
        market_previous = [market_amount[date] for date in previous_days]
        market_current = market_amount[day]
        stock_tag = stock["个股成交额低于此前20日中位数（标签）"]
        records.append({
            "选股日": day,
            "次交易日": trade.next_date,
            "Top1代码": trade.symbol6,
            "Top1股票": trade.name,
            "Top1类别": class_names[trade.actual_class],
            "原模型09:40收益（%）": round(trade.actual_0940_return * 100, 6),
            "大盘当日14:40成交额（亿元）": round(market_current, 4),
            "大盘此前20日中位数（亿元）": round(float(pd.Series(market_previous).median()), 4),
            "大盘成交额低于此前20日全部（标签）":
                "是" if market_current < min(market_previous) else "否",
            "个股当日14:40成交额（亿元）": stock["个股当日截至14:40成交额（亿元）"],
            "个股此前20日均额（亿元）": stock["个股此前20日均额（亿元）"],
            "个股此前20日中位数（亿元）": stock["个股此前20日中位数（亿元）"],
            "个股当日在前20日中的百分位": stock["个股当日在前20日中的百分位"],
            "个股此前20日有效天数": stock["个股此前20日有效天数"],
            "个股成交额低于此前20日中位数（标签）": stock_tag,
            "个股条件模拟结果": "数据不足" if not stock_tag else (
                "轮空" if stock_tag == "是" else "保留"),
        })
    daily = pd.DataFrame(records)
    OUT.mkdir(parents=True, exist_ok=True)
    daily.to_csv(OUT / "daily.csv", index=False, float_format="%.6f")
    monthly = []
    for month, group in daily.groupby(daily["选股日"].str[:7]):
        row = {"月份": month, "原模型": summarize(group),
               "个股条件保留": summarize(group[group["个股条件模拟结果"].eq("保留")]),
               "个股条件轮空": summarize(group[group["个股条件模拟结果"].eq("轮空")]),
               "个股数据不足": int(group["个股条件模拟结果"].eq("数据不足").sum()),
               "大盘非20日最低": summarize(group[
                   group["大盘成交额低于此前20日全部（标签）"].eq("否")])}
        monthly.append(row)
    (OUT / "summary.json").write_text(json.dumps(monthly, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(monthly, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
