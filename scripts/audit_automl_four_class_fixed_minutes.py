"""Audit fixed morning exits for the saved unweighted four-class picks."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.audit_automl_sell_minute_mode import (
    OCT, OUT as OPENING_RUN, SLOTS, WEIGHTED, load_cohorts, load_picks,
)


OUT = Path("docs/stock_automl_runs/20261010_second_high_four_class_fixed_minutes")


def check_0940_control(picks: pd.DataFrame) -> None:
    """The 09:40 result must match the previously reported exit ledger."""
    historical = pd.read_csv(WEIGHTED / "exit_trades.csv", dtype={"symbol6": str})
    october = pd.read_csv(OCT / "exit_trades.csv", dtype={"symbol6": str})
    controls = pd.concat([historical, october], ignore_index=True)
    controls = controls[
        controls.model.eq("unweighted")
        & controls.selection_rule.eq("class_priority_fallback")
        & controls.exit_strategy.eq("always_0940")
    ]
    keys = ["signal_date", "next_date", "symbol6", "selection_rank"]
    joined = picks.merge(
        controls[keys + ["entry_1440", "exit_price", "exit_return"]],
        on=keys, validate="one_to_one", suffixes=("", "_control"),
    )
    if len(joined) != 42 or not (
        np.allclose(joined.entry_1440, joined.entry_1440_control)
        and np.allclose(joined.close_40, joined.exit_price)
        and np.allclose(joined.close_40 / joined.entry_1440 - 1,
                        joined.exit_return)
    ):
        raise ValueError("09:40 prices or returns disagree with the frozen exit ledger")


def calculate(picks: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    trades = picks[["signal_date", "next_date", "symbol6", "name",
                    "selection_rank", "entry_1440", *SLOTS]].copy()
    for slot in SLOTS:
        trades[f"return_{slot}_pct"] = (trades[slot] / trades.entry_1440 - 1) * 100

    daily_rows = []
    for (signal_date, next_date), pair in trades.groupby(
            ["signal_date", "next_date"], sort=True):
        for top_k in (1, 2):
            chosen = pair[pair.selection_rank.le(top_k)].sort_values("selection_rank")
            if len(chosen) != top_k:
                raise ValueError(f"Missing rank on {signal_date}")
            values = np.array([
                chosen[f"return_{slot}_pct"].mean() for slot in SLOTS
            ], dtype=float)
            best, worst = values.max(), values.min()
            best_ties = [SLOTS[i] for i in np.flatnonzero(
                np.isclose(values, best, rtol=0, atol=1e-10))]
            worst_ties = [SLOTS[i] for i in np.flatnonzero(
                np.isclose(values, worst, rtol=0, atol=1e-10))]
            daily_rows.append({
                "signal_date": signal_date, "next_date": next_date,
                "top_k": top_k,
                "symbols": ",".join(chosen.symbol6),
                "names": ",".join(chosen["name"]),
                "best_slot": best_ties[0], "best_return_pct": best,
                "best_tied_slots": ",".join(best_ties),
                "worst_slot": worst_ties[0], "worst_return_pct": worst,
                "worst_tied_slots": ",".join(worst_ties),
                "close_40_return_pct": values[-1],
            })
    daily = pd.DataFrame(daily_rows)

    average_rows = []
    for sample, frame in (
        ("historical20", trades[trades.signal_date.ne("2026-10-08")]),
        ("oct9_single", trades[trades.signal_date.eq("2026-10-08")]),
        ("extended21", trades),
    ):
        for top_k in (1, 2):
            selected = frame[frame.selection_rank.le(top_k)]
            for slot in SLOTS:
                average_rows.append({
                    "sample": sample, "top_k": top_k, "slot": slot,
                    "signal_days": selected.signal_date.nunique(),
                    "trades": len(selected),
                    "mean_trade_return_pct": selected[f"return_{slot}_pct"].mean(),
                })
    averages = pd.DataFrame(average_rows)
    return trades, daily, averages


def render_report(daily: pd.DataFrame, averages: pd.DataFrame) -> str:
    def percent(value: float) -> str:
        return f"{value:+.3f}%"

    def slot_name(slot: str) -> str:
        return "开盘" if slot == "open_0931" else "09:" + slot[-2:]

    def slots_name(slots: str) -> str:
        return "/".join(slot_name(slot) for slot in slots.split(","))

    lines = [
        "# 未加权四分类：固定开盘或分钟末卖出的完整回放",
        "",
        "模型为此前七因子滚动训练的**未加权四分类小型提升树**；选股沿用已保存的"
        " `class_priority_fallback` 名单，优先预测 `≥3%` 类，不足时顺延预测 `1–3%` 类。"
        "没有根据本次分钟收益重新训练或调整名单。Top 1 是每天第 1 名；Top 2 是每天"
        "前两名各买一份的**等权平均**。买价为信号日 14:40 已完成分钟的收盘价。",
        "",
        "开盘指次交易日 09:31 第一根 1 分钟 K 的开盘价；09:31 至 09:40 则依次指"
        "十根已完成 1 分钟 K 的**收盘价**。下列均为单笔收益率的算术平均，"
        "没有复利、手续费或滑点。09:40 的结果逐笔与[此前四分类卖出明细]"
        "(../20261010_second_high_four_class_weighted/exit_trades.csv)及"
        "[10 月 9 日补测明细](../20261010_second_high_four_class_oct9/exit_trades.csv)核对一致。",
        "",
        "## 各统一卖出时点的平均收益",
        "",
        "**主口径为原历史 20 个信号日（09-02 至 09-30）**，即此前比较卖出策略时的"
        "同一批折外选股。右侧另外列出加入 10-08 信号、10-09 卖出后 21 天的均值，"
        "两组不能混称为同一回测。",
        "",
        "| 统一卖出时点 | 原 20 天 Top 1 | 原 20 天 Top 2 | 加入 10-09 后 21 天 Top 1 | 加入 10-09 后 21 天 Top 2 |",
        "|---|---:|---:|---:|---:|",
    ]
    for slot in SLOTS:
        values = []
        for sample in ("historical20", "extended21"):
            for top_k in (1, 2):
                row = averages[
                    averages["sample"].eq(sample) & averages.top_k.eq(top_k)
                    & averages.slot.eq(slot)].iloc[0]
                values.append(percent(row.mean_trade_return_pct))
        lines.append("| " + " | ".join([slot_name(slot), *values]) + " |")
    lines += [
        "",
        "在原 20 天，09:40 的 Top 1 **+1.311%**、Top 2 **+0.484%** 均为这 11 种"
        "固定时点中的最高均值，复现了[原卖出策略报告]"
        "(../20261010_second_high_four_class_weighted/README.md)中的 09:40 口径。"
        "Top 1 的 09:37 均值为 +1.278%，与 09:40 只差 0.033 个百分点。"
        "逐日看，09:40 仅在 Top 1 的 6/20 天、Top 2 的 4/20 天属于事后最高"
        "（含并列）。20 天内的最高均值不能当成分钟选择已具有稳定优势的证据。",
        "",
        "## 每日固定时点的最高、最低和 09:40 收益",
        "",
        "每行的“最高/最低”只在开盘和十个分钟**收盘价**之间比较，"
        "不使用 K 线盘中最高/最低价。它们是**事后才知道的极值**，"
        "不是当天可事前执行的卖出规则。并列极值列出全部时点。"
        "Top 2 的极值先在**同一个卖出时点**计算两只股票的等权收益，"
        "再比较 11 个时点；不会逐只股票各自挑最佳分钟。",
    ]
    for top_k in (1, 2):
        lines += [
            "", f"### Top {top_k}", "",
            "| 信号日 → 卖出日 | 股票 | 事后最高时点／收益 | 事后最低时点／收益 | 09:40 收益 |",
            "|---|---|---:|---:|---:|",
        ]
        for row in daily[daily.top_k.eq(top_k)].itertuples(index=False):
            date = row.signal_date[5:] + " → " + row.next_date[5:]
            best = slots_name(row.best_tied_slots) + " " + percent(row.best_return_pct)
            worst = slots_name(row.worst_tied_slots) + " " + percent(row.worst_return_pct)
            names = row.names.replace(",", "＋")
            lines.append("| " + " | ".join([
                date, names, best, worst, percent(row.close_40_return_pct)]) + " |")
    lines += [
        "",
        "最后一行 10-08 → 10-09 是**另加的一天**，不属于原 20 天均值。"
        "例如国轩高科买价 30.96 元，10-09 09:39 收盘价 30.95 元、09:40 收盘价"
        " 30.81 元，故最高固定时点仍为 −0.032%，09:40 为 −0.484%。",
        "",
        "完整逐股 11 时点卖价及收益在[逐笔底稿](selected_stock_minute_returns.csv)，"
        "逐日极值在[逐日表](daily_extrema.csv)，各时点均值在[均值表]"
        "(fixed_minute_averages.csv)。开盘价来自已核验的[开盘价底稿]"
        "(../20261010_second_high_sell_minute_mode/opening_prices.csv)；其余分钟收盘价"
        "来自此前的精确分钟数据。",
        "",
    ]
    return "\n".join(lines)


def run() -> dict:
    cohorts = load_cohorts()
    opening = pd.read_csv(OPENING_RUN / "opening_prices.csv",
                          dtype={"symbol6": str})
    cohorts = cohorts.merge(opening, on=["next_date", "symbol6"],
                            validate="one_to_one")
    picks = load_picks(cohorts)
    picks = picks[picks.model.eq("unweighted")].copy()
    if (len(cohorts) != 5996 or len(picks) != 42
            or picks.signal_date.nunique() != 21
            or picks[["entry_1440", *SLOTS]].isna().any().any()):
        raise ValueError("Incomplete saved unweighted four-class price paths")
    check_0940_control(picks)
    trades, daily, averages = calculate(picks)
    hist = averages[
        averages["sample"].eq("historical20") & averages.slot.eq("close_40")]
    controls = dict(zip(hist.top_k, hist.mean_trade_return_pct))
    if not (np.isclose(controls[1], 1.311, atol=.001)
            and np.isclose(controls[2], .484, atol=.001)):
        raise ValueError("Historical 09:40 means disagree with the earlier report")
    OUT.mkdir(parents=True, exist_ok=True)
    trades.to_csv(OUT / "selected_stock_minute_returns.csv", index=False)
    daily.to_csv(OUT / "daily_extrema.csv", index=False)
    averages.to_csv(OUT / "fixed_minute_averages.csv", index=False)
    (OUT / "README.md").write_text(render_report(daily, averages))
    summary = {
        "model": "unweighted four-class small_boosted_tree",
        "selection": "class_priority_fallback, saved pre-outcome picks",
        "entry": "signal-date 14:40 finalized minute close",
        "exits": "next-morning 09:31 bar open or 09:31-09:40 finalized minute closes",
        "historical_dates": "2026-09-02 through 2026-09-30, 20 signal days",
        "additional_date": "2026-10-08 signal, 2026-10-09 morning",
        "daily_extrema": "Among 11 fixed exit prices; ties list all slots and display the earliest",
        "historical_0940_control": controls,
        "rows": {"trades": len(trades), "daily": len(daily),
                 "averages": len(averages)},
    }
    (OUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
