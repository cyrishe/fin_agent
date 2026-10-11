"""Summarize 2026 Top1 stock and market turnover tags by month."""
from __future__ import annotations

from pathlib import Path

import pandas as pd


SOURCE = Path("docs/stock_automl_runs/20261011_market_state_asof/daily_2026.csv")
OUTPUT = Path("docs/stock_automl_runs/20261011_stock_amount_gate_2026_monthly/monthly.csv")
RETURN = "Top1收益（%）"
STOCK_TAG = "个股成交额低于此前20日中位数（标签）"
MARKET_TAG = "大盘成交额低于此前20日全部（标签）"


def stats(rows: pd.DataFrame) -> tuple[int, float | None, float | None, int]:
    values = rows[RETURN]
    if values.empty:
        return 0, None, None, 0
    return (len(values), round(float(values.mean()), 6),
            round(float(((1 + values / 100).prod() - 1) * 100), 6),
            int(values.gt(0).sum()))


def main() -> None:
    daily = pd.read_csv(SOURCE, dtype={"Top1代码": str})
    daily = daily[daily["Top1代码"].notna() & daily[RETURN].notna()].copy()
    daily["月份"] = pd.to_datetime(daily["选股日"]).dt.strftime("%Y-%m")
    records = []
    for month, group in daily.groupby("月份", sort=True):
        stock_keep = group[group[STOCK_TAG].eq("否")]
        stock_skip = group[group[STOCK_TAG].eq("是")]
        market_keep = group[group[MARKET_TAG].eq("否")]
        market_skip = group[group[MARKET_TAG].eq("是")]
        both_keep = group[group[STOCK_TAG].eq("否") & group[MARKET_TAG].eq("否")]
        original = stats(group)
        stock_kept = stats(stock_keep)
        stock_skipped = stats(stock_skip)
        market_kept = stats(market_keep)
        market_skipped = stats(market_skip)
        both = stats(both_keep)
        missing_stock = int(group[STOCK_TAG].isna().sum())
        if stock_kept[0] + stock_skipped[0] + missing_stock != original[0]:
            raise ValueError(f"Stock-tag groups do not partition {month}")
        if market_kept[0] + market_skipped[0] != original[0]:
            raise ValueError(f"Market-tag groups do not partition {month}")
        records.append({
            "月份": month,
            "原模型笔数": original[0],
            "原模型单笔平均收益（%）": original[1],
            "原模型复利代理（%）": original[2],
            "个股条件保留笔数": stock_kept[0],
            "个股条件保留单笔平均收益（%）": stock_kept[1],
            "个股条件保留复利代理（%）": stock_kept[2],
            "个股低于20日中位数笔数": stock_skipped[0],
            "个股低于20日中位数组单笔平均收益（%）": stock_skipped[1],
            "个股历史不足笔数": missing_stock,
            "大盘20日最低笔数": market_skipped[0],
            "大盘20日最低组单笔平均收益（%）": market_skipped[1],
            "大盘非20日最低笔数": market_kept[0],
            "大盘非20日最低组单笔平均收益（%）": market_kept[1],
            "两个条件同时保留笔数": both[0],
            "两个条件同时保留单笔平均收益（%）": both[1],
            "原模型超过+5%笔数": int(group[RETURN].gt(5).sum()),
            "原模型低于-5%笔数": int(group[RETURN].lt(-5).sum()),
        })
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    result = pd.DataFrame(records)
    result.to_csv(OUTPUT, index=False, float_format="%.6f")
    print(result.to_string(index=False))


if __name__ == "__main__":
    main()
