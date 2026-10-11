"""Compare a minute-triggered exit with 09:40 and add prior market context."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.experiment_automl_mk_1m_st_policies import POLICIES, SOURCE
from scripts.mk_minute_archive import COMPACT_ROOT, day_files, trading_day
from scripts.report_automl_daily_market_context import MARKET_SQL


BASE = Path("docs/stock_automl_runs/20261011_mk_1m_three_st_policies")
OUTPUT = BASE / "tradable_st_new_exit_market"
PICKS = BASE / "top2_verified.csv"
ARCHIVES = (COMPACT_ROOT,)
NEXT_CLOSES = [f"next_close_{minute:02d}" for minute in range(31, 41)]
LABELS = {"ge3": "达标", "1to3": "可接受", "0to1": "中性", "lt0": "错误"}


def sell(entry: float, closes: list[float]) -> tuple[str, str, float]:
    """First >3% vs entry, then <0% with >=1% gain vs previous close."""
    if not np.isfinite(entry) or entry <= 0 or len(closes) != 10 or not np.isfinite(closes).all():
        raise ValueError("A selected stock lacks a complete 09:31–09:40 price path")
    for index, price in enumerate(closes):
        minute = f"09:{31 + index:02d}"
        if price > entry * 1.03 + 1e-8:
            return minute, "涨幅超过3%", price / entry - 1
        if (index > 0 and price < entry - 1e-8 and
                price >= closes[index - 1] * 1.01 - 1e-8):
            return minute, "亏损中较前一分钟上涨至少1%", price / entry - 1
    return "09:40", "到时卖出", closes[-1] / entry - 1


def market_amounts(archives: tuple[Path, ...], cache: Path,
                   expected_days: int = 139) -> pd.DataFrame:
    paths = day_files(archives)
    if len(paths) != expected_days:
        raise ValueError(f"Expected {expected_days} distinct minute files")
    if cache.exists():
        result = pd.read_csv(cache, dtype={"trade_date": str})
    else:
        records = []
        for index, path in enumerate(paths, start=1):
            bars = pd.read_csv(path, usecols=["time", "symbol", "turnover"],
                               dtype={"symbol": str})
            day = trading_day(path)
            if not bars.time.str[:10].eq(day).all():
                raise ValueError(f"Unexpected date in {path}")
            a_share = bars.symbol.str.endswith((".SH", ".SZ")) & bars.symbol.str[0].isin(
                ("0", "3", "6"))
            visible = bars.loc[a_share & bars.time.str[-8:].le("14:40:00")]
            at_1440 = visible[visible.time.str.endswith("14:40:00")]
            if (visible.empty or visible.turnover.isna().any() or
                    visible.turnover.lt(0).any() or
                    at_1440.symbol.nunique() < 4000 or
                    visible[["time", "symbol"]].duplicated().any()):
                raise ValueError(f"Incomplete or duplicate A-share turnover in {path}")
            records.append({"trade_date": day,
                            "amount_1440_100m_cny": visible.turnover.sum() / 1e8,
                            "symbols_at_1440": at_1440.symbol.nunique()})
            if index % 10 == 0 or index == len(paths):
                print(f"14:40 market amounts: {index}/{len(paths)} days", flush=True)
        result = pd.DataFrame(records).sort_values("trade_date").reset_index(drop=True)
        cache.parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(cache, index=False, float_format="%.6f")
    if len(result) != expected_days or result.trade_date.duplicated().any():
        raise ValueError("Incomplete or duplicate cached 14:40 market amounts")
    return result.sort_values("trade_date").reset_index(drop=True)


def attach_market(daily: pd.DataFrame, amount: pd.DataFrame,
                  breadth: pd.DataFrame) -> pd.DataFrame:
    breadth = breadth.copy()
    for column in ("up_count", "down_count", "amount_100m_cny"):
        breadth[column] = pd.to_numeric(breadth[column], errors="raise")
    market = amount.merge(breadth, on="trade_date", validate="one_to_one")
    if len(market) != len(amount):
        raise ValueError("A minute market day has no daily K breadth")
    # Raw minute turnover and daily K amount come from separate feeds; flag
    # obvious scale/date mismatches without requiring exact equality.
    ratio = market.amount_1440_100m_cny / market.amount_100m_cny
    if not ratio.between(.4, 1.02).all():
        raise ValueError(f"Implausible 14:40/full-day amount ratio: {ratio.min()}, {ratio.max()}")
    positions = {date: index for index, date in enumerate(market.trade_date)}
    result = daily.copy()
    for row_index, day in enumerate(result["选股日"]):
        index = positions.get(day)
        if index is None or index < 5:
            raise ValueError(f"Five prior market dates unavailable for {day}")
        previous = market.iloc[index - 1]
        result.loc[row_index, "前一交易日上涨家数"] = int(previous.up_count)
        result.loc[row_index, "前一交易日下跌家数"] = int(previous.down_count)
        for lag in range(1, 6):
            result.loc[row_index, f"前{lag}交易日截至14:40成交额（亿元）"] = (
                market.iloc[index - lag].amount_1440_100m_cny)
    return result


def make_daily(picks: pd.DataFrame, candidates: pd.DataFrame,
               dates: list[str], expected_picks: int | None = 224) -> tuple[pd.DataFrame, pd.DataFrame]:
    policy = POLICIES[1]
    selected = picks[picks.policy.eq(policy)].copy()
    if (expected_picks is not None and len(selected) != expected_picks) or selected.entry_at_limit.any():
        raise ValueError("Expected non-limit Top1/Top2 picks")
    observations = candidates[["signal_date", "symbol6", *NEXT_CLOSES]]
    selected = selected.merge(observations, on=["signal_date", "symbol6"],
                              how="left", validate="one_to_one")
    if selected[NEXT_CLOSES].isna().any().any():
        raise ValueError("A selected stock has a missing morning minute close")
    baseline = selected.next_close_40 / selected.signal_price - 1
    if not np.allclose(baseline, selected.actual_0940_return, atol=1e-10):
        raise ValueError("Saved 09:40 returns disagree with minute prices")
    exits = [sell(row.signal_price, [getattr(row, col) for col in NEXT_CLOSES])
             for row in selected.itertuples(index=False)]
    selected["sell_minute"] = [item[0] for item in exits]
    selected["sell_rule"] = [item[1] for item in exits]
    selected["sell_return"] = [item[2] for item in exits]
    selected["baseline_return"] = baseline

    records = []
    for day in dates:
        group = selected[selected.signal_date.eq(day)].set_index("selection_rank")
        row = {"选股日": day}
        for rank in (1, 2):
            prefix = f"Top{rank}"
            if rank not in group.index:
                row.update({f"{prefix}股票": "", f"{prefix}结果": "—",
                            f"{prefix}固定09:40收益（%）": np.nan,
                            f"{prefix}新规则收益（%）": np.nan,
                            f"{prefix}卖出时刻": "", f"{prefix}卖出原因": ""})
                continue
            item = group.loc[rank]
            row.update({f"{prefix}股票": item.symbol6,
                        f"{prefix}结果": LABELS.get(item.actual_class, "未知"),
                        f"{prefix}固定09:40收益（%）": item.baseline_return * 100,
                        f"{prefix}新规则收益（%）": item.sell_return * 100,
                        f"{prefix}卖出时刻": item.sell_minute,
                        f"{prefix}卖出原因": item.sell_rule})
        records.append(row)
    return pd.DataFrame(records), selected


def summarize(trades: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    monthly_rows = []
    for (month, rank), group in trades.groupby(
            [trades.signal_date.str[:7], "selection_rank"], sort=True):
        fixed = group.baseline_return
        adaptive = group.sell_return
        monthly_rows.append({
            "月份": month, "排名": f"Top{rank}", "交易笔数": len(group),
            **{f"{label}数": int(group.actual_class.eq(code).sum())
               for code, label in LABELS.items()},
            "固定09:40平均收益（%）": fixed.mean() * 100,
            "新规则平均收益（%）": adaptive.mean() * 100,
            "平均差额（百分点）": (adaptive - fixed).mean() * 100,
            "固定09:40复利代理（%）": ((1 + fixed).prod() - 1) * 100,
            "新规则复利代理（%）": ((1 + adaptive).prod() - 1) * 100,
            "超过3%卖出笔数": group.sell_rule.eq("涨幅超过3%").sum(),
            "亏损反弹卖出笔数": group.sell_rule.eq("亏损中较前一分钟上涨至少1%").sum(),
            "09:40到时卖出笔数": group.sell_rule.eq("到时卖出").sum(),
        })
    monthly = pd.DataFrame(monthly_rows)
    summary = {}
    for rank, group in trades.groupby("selection_rank"):
        summary[f"Top{rank}"] = {
            "trades": len(group),
            "fixed_mean_pct": float(group.baseline_return.mean() * 100),
            "new_mean_pct": float(group.sell_return.mean() * 100),
            "difference_mean_percentage_points": float((group.sell_return -
                                                        group.baseline_return).mean() * 100),
            "fixed_compound_pct": float(((1 + group.baseline_return).prod() - 1) * 100),
            "new_compound_pct": float(((1 + group.sell_return).prod() - 1) * 100),
            "new_better": int(group.sell_return.gt(group.baseline_return).sum()),
            "new_worse": int(group.sell_return.lt(group.baseline_return).sum()),
            "new_equal": int(group.sell_return.eq(group.baseline_return).sum()),
            "sell_rule_counts": {str(k): int(v) for k, v in group.sell_rule.value_counts().items()},
        }
    return monthly, summary


def report(summary: dict) -> str:
    lines = [
        "# 只剔除 14:40 涨停 ST：新卖出规则回测",
        "",
        "逐日全量表见 [daily.csv](daily.csv)，按月全量表见 [monthly.csv](monthly.csv)。"
        "逐笔价格路径及卖出依据见 [trades.csv](trades.csv)。",
        "",
        "训练、选股和 Top1/Top2 排名沿用原 20 日滚动模型；只对该方案的"
        "次晨卖出规则作成对比较。样本为 2026 年 2—7 月 118 个选股日，"
        "Top1 共 115 笔、Top2 共 109 笔；空缺排名在逐日表中留空。",
        "",
        "卖出规则：以 T 日 14:40 价格为买价，依次检查 T+1 日 09:31—09:40"
        "每分钟收盘价。首次相对买价严格超过 3% 时卖出；否则，若当前仍低于"
        "买价、但较前一分钟收盘价上涨至少 1%，立即卖出；均未触发则 09:40 卖出。"
        "09:31 没有前一分钟可用于反弹判断。无成交滑点、手续费和冲击成本。",
        "",
        "| 排名 | 笔数 | 固定 09:40 平均收益 | 新规则平均收益 | 差额 | 固定复利代理 | 新规则复利代理 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for rank in ("Top1", "Top2"):
        item = summary[rank]
        lines.append(
            f"| {rank} | {item['trades']} | {item['fixed_mean_pct']:.3f}% | "
            f"{item['new_mean_pct']:.3f}% | "
            f"{item['difference_mean_percentage_points']:+.3f} 个百分点 | "
            f"{item['fixed_compound_pct']:.2f}% | {item['new_compound_pct']:.2f}% |"
        )
    lines.extend([
        "",
        "按月表中的达标／可接受／中性／错误是原模型预测目标的事后分类，"
        "根据次晨前 10 分钟最高价判断；它不是卖出收益的分类。"
        "新卖出规则只改变卖出收益，不改变该分类。月复利代理假设每笔等额串行复投，"
        "仅供同口径比较，不是实际资金曲线。",
        "",
        "逐日表的前一交易日涨跌家数来自沪深 A 股日 K 收盘价与昨收比较；"
        "前 1—5 交易日成交额分别来自原始一分钟 K 的沪深 A 股 turnover 累加至"
        "当日 14:40（含），单位亿元，按近到远排列。成交额源文件 139 个交易日"
        "均有超过 5,000 只股票的 14:40 分钟记录。",
        "",
        "本次观测中，新规则的 Top1 与 Top2 平均收益都低于固定 09:40；"
        "逐笔差额及其触发原因可在全量表核对。分钟收盘价仅是可观测成交代理，"
        "不保证实盘可在该价成交。",
        "",
    ])
    return "\n".join(lines)


def run(output: Path, env_file: Path) -> dict:
    picks = pd.read_csv(PICKS, dtype={"symbol6": str, "signal_date": str})
    candidates = pd.read_csv(SOURCE, usecols=["signal_date", "symbol6", *NEXT_CLOSES],
                             dtype={"symbol6": str, "signal_date": str}, low_memory=False)
    dates = sorted(picks.signal_date.unique())
    daily, trades = make_daily(picks, candidates, dates)
    amount = market_amounts(ARCHIVES, output / "market_amount_1440.csv")
    with db_connection(env_file) as db:
        breadth = query(db, MARKET_SQL, (amount.trade_date.min(), amount.trade_date.max()))
    breadth["trade_date"] = pd.to_datetime(breadth.trade_date).dt.strftime("%Y-%m-%d")
    daily = attach_market(daily, amount, breadth)
    monthly, summary = summarize(trades)
    for column in ("前一交易日上涨家数", "前一交易日下跌家数"):
        daily[column] = daily[column].astype(int)
    output.mkdir(parents=True, exist_ok=True)
    daily.to_csv(output / "daily.csv", index=False, float_format="%.8f")
    monthly.to_csv(output / "monthly.csv", index=False, float_format="%.8f")
    trades.to_csv(output / "trades.csv", index=False, float_format="%.8f")
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    (output / "README.md").write_text(report(summary), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    print(json.dumps(run(args.output, args.env_file), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
