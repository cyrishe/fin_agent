"""Publish the no-ST rerank of the already trained ST-balanced rolling model."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import pandas as pd

from scripts.experiment_automl_mk_1m_st_policies import OUT, POLICIES, compact_tables
from scripts.experiment_automl_mk_1m_st_rerank_nonst import RERANK_OUT
from scripts.report_automl_mk_1m_st_policies import REPORT, markdown_table, monthly_display


def render(output: Path, report: Path) -> None:
    summary = json.loads((output / "summary.json").read_text())
    picks = pd.read_csv(output / "top2_verified.csv", dtype={"symbol6": str})
    folds = pd.read_csv(output / "folds.csv")
    original = pd.read_csv(OUT / "top2_verified.csv", dtype={"symbol6": str})
    original = original[original.policy.eq(POLICIES[2])]
    dates = sorted(folds.day.tolist())
    if (summary["days"] != 118 or len(dates) != 118 or
            len(set(dates)) != 118 or picks.historical_st.any() or
            picks.actual_0940_return.isna().any()):
        raise ValueError("Incomplete or ST-contaminated reranked result")
    if picks.selection_rank.eq(1).sum() != summary["top1_days"]:
        raise ValueError("Top1 count does not match the rerank summary")

    monthly, daily = compact_tables(picks.assign(policy=POLICIES[2]), dates)
    monthly = monthly[monthly.方案.eq(POLICIES[2])].drop(columns="方案").reset_index(drop=True)
    daily = daily[daily.方案.eq(POLICIES[2])].drop(columns="方案").reset_index(drop=True)
    if len(monthly) != 6 or len(daily) != 118:
        raise ValueError("Incomplete six-month rerank report")
    report.mkdir(parents=True, exist_ok=True)
    for name in ("summary.json", "folds.csv", "top2_verified.csv"):
        shutil.copy2(output / name, report / name)
    monthly.to_csv(report / "monthly.csv", index=False)
    daily.to_csv(report / "daily.csv", index=False)

    original_top1 = original[original.selection_rank.eq(1)]
    reranked_top1 = picks[picks.selection_rank.eq(1)]
    no_st_top1 = pd.read_csv(OUT / "top2_verified.csv")
    no_st_top1 = no_st_top1[no_st_top1.policy.eq(POLICIES[0]) &
                              no_st_top1.selection_rank.eq(1)]
    def counts(frame: pd.DataFrame) -> str:
        labels = frame.actual_class.mask(frame.suspension_zero, "0to1")
        return "/".join(
            f"{name}{int(labels.eq(value).sum())}"
            for name, value in (("达标", "ge3"), ("可接受", "1to3"),
                                ("中性", "0to1"), ("错误", "lt0")))

    top1_extreme_up = int(reranked_top1.actual_0940_return.gt(.05).sum())
    top1_extreme_down = int(reranked_top1.actual_0940_return.lt(-.05).sum())
    original_limit = int((original_top1.historical_st &
                          original_top1.entry_at_limit).sum())
    display = monthly_display(monthly.assign(方案="ST参与训练，推理时去ST顺延"))
    display = display.drop(columns="方案")
    text = f"""# ST 参与训练后，推理时去掉 ST 并顺延选股

本实验沿用 [三种 ST 训练口径](../README.md) 中第③组的 **118 个前 20 日滚动模型**：训练阶段保留全部 14:40 已涨停 ST，用固定哈希保留约 22% 未涨停 ST，使全期候选 ST 占 3.52%。每天对同一候选池的**全部股票先评分**，再删掉所有 ST，从剩余的预测达标、可接受股票中，按原有优先级和概率选 Top1、Top2。不是只检查原 Top2；两只都为 ST 时也继续往后找。每个交易日重新拟合模型并核对原 Top2 股票和四类概率与已保存记录一致后，才进行这一步。所有结果在选定股票后才连接 T+1 行情。

| 训练与选股口径 | Top1 有选股日 | Top1 达标／可接受／中性／错误 | 固定次日 09:40 平均收益 |
|---|---:|---|---:|
| 第③组原表：ST 参与训练、也允许选 ST | {len(original_top1)}/118 | {counts(original_top1)} | {original_top1.actual_0940_return.mean()*100:+.3f}%/笔 |
| 本实验：ST 参与训练、推理时去 ST 顺延 | {len(reranked_top1)}/118 | {counts(reranked_top1)} | {reranked_top1.actual_0940_return.mean()*100:+.3f}%/笔 |
| 第①组：训练和推理都去 ST | {len(no_st_top1)}/118 | {counts(no_st_top1)} | {no_st_top1.actual_0940_return.mean()*100:+.3f}%/笔 |

第③组原 Top1 中有 **{original_limit}/{len(original_top1)}** 笔是 14:40 已涨停 ST；推理时去 ST 顺延后，**116 日**有非 ST Top1，另 **2 日**在原筛选规则下没有非 ST 入选。顺延 Top2 为 **{summary['top2_days']} 日**，固定次日 09:40 平均 **{summary['top2_mean_return_pct']:+.3f}%/笔**。顺延 Top1 有 **{top1_extreme_up} 笔 >+5%**、**{top1_extreme_down} 笔 <-5%**。这些是价格路径统计，未计买入成交、费用或滑点；从原表的 +2.36%/笔降到本实验的 -0.035%/笔，说明原结果主要依赖信号时已涨停 ST，不能直接当成可买策略。与“从训练起完全去 ST”的 +0.004%/笔相比，当前样本也没有显示保留 ST 训练会改善非 ST Top1。差距很小，不宜据此断言任何稳定劣势或优势。

顺延后的 **6 月 Top1 表面均值 +1.52%/笔**，但当月 **5 笔 >+5%、1 笔 <-5%**；剔除这些单笔后是 **-0.40%/笔**。所以即使改为非 ST 顺延，单看月均值也容易高估稳定性。该剔除只用于敏感性分析，不是实际交易规则。

月表的“均”是当月单笔简单平均，不是月总收益；“月”是假设每笔全部资金再投入的价格复利代理；“去极”剔除严格超过 +5% 或低于 -5% 的单笔后再平均。`>+5%/＜-5%` 为对应笔数。按日表中的结果取 T+1 09:31–09:40 第二高价相对 T 14:40 的涨幅，收益取 T+1 09:40 分钟收盘价；两列目标不同。

## 按月结果

{markdown_table(display)}

## 按日明细

{markdown_table(daily)}

逐笔股票、四类概率、ST 状态和次晨价格见 [逐笔记录](top2_verified.csv)；逐日训练样本数及候选数量见 [训练折记录](folds.csv)。
"""
    (report / "README.md").write_text(text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=RERANK_OUT)
    parser.add_argument("--report", type=Path, default=REPORT / "rerank_nonst")
    args = parser.parse_args()
    render(args.output, args.report)


if __name__ == "__main__":
    main()
