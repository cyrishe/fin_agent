"""Reconstruct the frozen >2% next-open model's selected stock/date rows.

Read-only database access. Detailed market rows are written only to the local
ignored outputs directory at the user's request; no production asset is changed.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from scripts.experiment_automl_next_open_targets import (
    LATER_END, LATER_START, TARGET_GAP, model_specs, score,
)
from scripts.experiment_automl_next_open_weekly import (
    TEST_END, TEST_START, TRAIN_END, TRAIN_START, VALID_END, VALID_START,
    load_sources, prepare_dataset, weekly_policy,
)
from src.quant_research.automl.data import kingdom_connection, query


PERIODS = (
    ("验证期（参与选门槛）", VALID_START, VALID_END, "validation"),
    ("测试期", TEST_START, TEST_END, "test"),
    ("后续观察期", LATER_START, LATER_END, "later_check"),
)
MODEL_NAME = "direct_hist_boost_all"


def fmt_pct(value):
    return f"{value:+.2f}%" if pd.notna(value) else "—"


def summary(frame):
    gap = frame.next_open_gap_pct
    return {
        "signals": int(len(frame)), "high2_hits": int(gap.gt(2).sum()),
        "high2_rate": float(gap.gt(2).mean()),
        "high0_rate": float(gap.gt(0).mean()),
        "low0_rate": float(gap.lt(0).mean()),
        "low2_rate": float(gap.lt(-2).mean()),
        "active_days": int(frame.decision_date.nunique()),
        "active_weeks": int(pd.to_datetime(frame.decision_date).dt.strftime("%G-W%V").nunique()),
    }


def export(reference, csv_path, md_path):
    load_dotenv(".env")
    saved = json.loads(reference.read_text())
    if saved["model_selected_on_validation"] != MODEL_NAME:
        raise ValueError("saved validation model differs from the extraction model")
    saved_model = next(item for item in saved["models"] if item["model"] == MODEL_NAME)
    chosen_trial = next(item for item in saved_model["trials"]
                        if item["quantile"] == saved_model["chosen_quantile"])
    frozen_cutoff = chosen_trial["cutoff"]

    symbols, calendar, price, flow, value, industry, indices = load_sources(1000)
    data = prepare_dataset(calendar, price, flow, value, industry, indices)
    columns, mode, model = model_specs()[MODEL_NAME]
    if mode != "high2":
        raise ValueError("unexpected model target")
    train = data[data.signal_date.between(TRAIN_START, TRAIN_END)]
    model.fit(train[list(columns)].replace([np.inf, -np.inf], np.nan),
              train.gap.gt(TARGET_GAP).astype(int))
    valid = data[data.signal_date.between(VALID_START, VALID_END)]
    observed_cutoff = float(np.quantile(score(model, valid, columns),
                                        saved_model["chosen_quantile"]))
    if not np.isclose(observed_cutoff, frozen_cutoff, rtol=0, atol=1e-10):
        raise ValueError("frozen validation cutoff did not reproduce")

    daily = price[["symbol", "date", "adjclose"]].sort_values(["symbol", "date"]).copy()
    daily["adjclose"] = pd.to_numeric(daily.adjclose, errors="coerce")
    daily["signal_day_close_change_pct"] = daily.groupby("symbol").adjclose.pct_change(
        fill_method=None).mul(100)
    daily = daily.rename(columns={"date": "signal_date"})
    next_market_day = dict(zip(calendar[:-1], calendar[1:]))
    with kingdom_connection() as conn:
        marks = ",".join(["%s"] * len(symbols))
        names = pd.DataFrame(query(conn,
            f"SELECT stk_code AS symbol, stk_name AS current_name FROM kcrp_stock_baseinfo "
            f"WHERE stk_code IN ({marks})", tuple(symbols)))

    detail = []
    for period, first, last, key in PERIODS:
        frame = data[data.signal_date.between(first, last)]
        picks = weekly_policy(frame, score(model, frame, columns), frozen_cutoff)
        expected = saved_model[key]
        if len(picks) != expected["signals"] or picks.gap.gt(TARGET_GAP).sum() != expected["high2_wins"]:
            raise ValueError(f"{period} did not reproduce the published signal counts")
        picks = picks.merge(frame[["symbol", "signal_date", "price_return_1"]],
                            on=["symbol", "signal_date"], validate="one_to_one")
        picks = picks.merge(daily[["symbol", "signal_date", "signal_day_close_change_pct"]],
                            on=["symbol", "signal_date"], validate="one_to_one")
        picks = picks.merge(names, on="symbol", how="left", validate="many_to_one")
        picks["period"] = period
        picks["next_trade_date"] = picks.signal_date.map(next_market_day)
        picks["next_open_gap_pct"] = picks.gap.mul(100)
        picks["prior_day_close_change_pct"] = picks.price_return_1.mul(100)
        picks["high_open_gt_2pct"] = picks.gap.gt(TARGET_GAP)
        picks = picks.rename(columns={"signal_date": "decision_date", "score": "model_score"})
        detail.append(picks[["period", "decision_date", "symbol", "current_name",
                             "prior_day_close_change_pct", "signal_day_close_change_pct",
                             "next_trade_date", "next_open_gap_pct", "high_open_gt_2pct",
                             "model_score"]])
    rows = pd.concat(detail, ignore_index=True).sort_values(
        ["decision_date", "model_score", "symbol"], ascending=[True, False, True])
    rows["decision_date"] = pd.to_datetime(rows.decision_date).dt.strftime("%Y-%m-%d")
    rows["next_trade_date"] = pd.to_datetime(rows.next_trade_date).dt.strftime("%Y-%m-%d")
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    rows.to_csv(csv_path, index=False, encoding="utf-8-sig", float_format="%.6f")

    lines = ["# 次日高开超过 2%：全部选股明细", "",
        f"生成时间：{datetime.now().astimezone().isoformat(timespec='seconds')}。",
        f"模型：`{MODEL_NAME}`；验证期冻结分数门槛：`{frozen_cutoff:.8f}`。"
        "模型分数未经概率校准。", "",
        "**口径**：当日涨幅 = 决策日复权收盘价 / 前一交易日复权收盘价 − 1；"
        "次日开盘涨幅 = 下一市场交易日复权开盘价 / 决策日复权收盘价 − 1。"
        "两列均为事后实现值。当日收盘涨幅在 14:45 尚未知晓，未用于本次模型输入。"
        "股票名称取当前基础资料，历史上可能不同。表格显示两位小数，是否命中用未四舍五入的原值判断。", "",
        "| 区间 | 信号数 | 高开 >2% | 命中率 | 低开 | 低开 <−2% | 出手日 | 活跃周 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for period, _, _, _ in PERIODS:
        part = rows[rows.period.eq(period)]
        result = summary(part)
        lines.append(f"| {period} | {result['signals']} | {result['high2_hits']} | "
                     f"{result['high2_rate']:.2%} | {result['low0_rate']:.2%} | "
                     f"{result['low2_rate']:.2%} | {result['active_days']} | {result['active_weeks']} |")
    all_summary = summary(rows)
    lines.append(f"| **三段合计** | **{all_summary['signals']}** | "
                 f"**{all_summary['high2_hits']}** | **{all_summary['high2_rate']:.2%}** | "
                 f"**{all_summary['low0_rate']:.2%}** | **{all_summary['low2_rate']:.2%}** | "
                 f"**{all_summary['active_days']}** | **{all_summary['active_weeks']}** |")
    lines += ["", "三段合计仅方便核数；验证期参与模型与门槛选择，不能把合计率当独立测试结果。", ""]
    for period, _, _, _ in PERIODS:
        lines += [f"## {period}", "",
                  "| 决策日期 | 股票（当前名称） | 当日收盘涨幅 | 次日开盘日 | 次日开盘涨幅 | >2% |",
                  "|---|---|---:|---|---:|:---:|"]
        for row in rows[rows.period.eq(period)].itertuples(index=False):
            name = str(row.current_name) if pd.notna(row.current_name) else ""
            name = name.replace("|", "\\|")
            lines.append(f"| {row.decision_date} | {name}（`{row.symbol}`） | "
                         f"{fmt_pct(row.signal_day_close_change_pct)} | {row.next_trade_date} | "
                         f"{fmt_pct(row.next_open_gap_pct)} | {'是' if row.high_open_gt_2pct else '否'} |")
        lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print("saved", csv_path, md_path, "rows", len(rows), "summary", all_summary, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path,
        default=Path("outputs/stock_automl/next_open_targets/high2_1000.json"))
    parser.add_argument("--csv", type=Path,
        default=Path("outputs/stock_automl/next_open_targets/selected_signals.csv"))
    parser.add_argument("--markdown", type=Path,
        default=Path("outputs/stock_automl/next_open_targets/selected_signals.md"))
    args = parser.parse_args()
    export(args.reference, args.csv, args.markdown)
