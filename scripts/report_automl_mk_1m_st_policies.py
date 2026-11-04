"""Publish the two usable ST-policy comparisons as compact Chinese tables."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import pandas as pd

from scripts.experiment_automl_mk_1m_st_policies import OUT, POLICIES


REPORT = Path("docs/stock_automl_runs/20261011_mk_1m_three_st_policies")
DISPLAY_POLICIES = POLICIES[:2]


def markdown_table(frame: pd.DataFrame) -> str:
    columns = list(frame.columns)
    lines = ["| " + " | ".join(columns) + " |",
             "|" + "|".join(["---"] * len(columns)) + "|"]
    for row in frame.itertuples(index=False, name=None):
        lines.append("| " + " | ".join(str(value).replace("|", "\\|")
                                        for value in row) + " |")
    return "\n".join(lines)


def monthly_display(monthly: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for item in monthly.to_dict("records"):
        row = {"月份": item["月份"], "方案": item["方案"]}
        for rank in (1, 2):
            key = f"Top{rank}"
            row[f"{key}结果"] = item[f"{key}结果"]
            row[f"{key}收益（均/月/去极）"] = (
                f"{item[f'{key} 09:40收益']} / "
                f"{item[f'{key} 月复利代理']} / "
                f"{item[f'{key} 去极端平均']}")
            row[f"{key} >+5%/＜-5%"] = (
                f"{item[f'{key} 超过+5%笔数']}/"
                f"{item[f'{key} 低于-5%笔数']}")
        rows.append(row)
    return pd.DataFrame(rows)


def render(output: Path, report: Path) -> None:
    summary = json.loads((output / "summary.json").read_text())
    all_monthly = pd.read_csv(output / "monthly.csv")
    all_daily = pd.read_csv(output / "daily.csv")
    picks = pd.read_csv(output / "top2_verified.csv")
    if summary["test_days"] != 118 or len(all_monthly) != 18 or len(all_daily) != 354:
        raise ValueError("Incomplete six-month source experiment")
    if set(all_daily.方案) != set(POLICIES):
        raise ValueError("A source policy is absent from the daily results")
    monthly = all_monthly[all_monthly.方案.isin(DISPLAY_POLICIES)].copy()
    daily = all_daily[all_daily.方案.isin(DISPLAY_POLICIES)].copy()
    if len(monthly) != 12 or len(daily) != 236:
        raise ValueError("Incomplete two-policy display tables")
    monthly = monthly.drop(columns=[f"Top{rank} 14:40涨停ST笔数"
                                    for rank in (1, 2)])
    report.mkdir(parents=True, exist_ok=True)
    monthly.to_csv(report / "monthly.csv", index=False)
    daily.to_csv(report / "daily.csv", index=False)
    # Keep the original three-policy evidence available for audit, outside the
    # user-facing monthly/daily strategy tables.
    for name in ("top2_verified.csv", "folds.csv", "summary.json"):
        shutil.copy2(output / name, report / name)

    selected_stats = []
    for policy in DISPLAY_POLICIES:
        detail = summary["per_policy"][policy]
        day_rows = daily[daily.方案.eq(policy)]
        result_counts = "/".join(
            f"{label}{int(day_rows['Top1结果'].eq(label).sum())}"
            for label in ("达标", "可接受", "中性", "错误"))
        ret = picks.loc[picks.policy.eq(policy) & picks.selection_rank.eq(1),
                        "actual_0940_return"] * 100
        selected_stats.append(
            f"- **{policy}**：Top1 选股 {detail['top1_days']}/118 日；"
            f"{result_counts}；固定 09:40 平均 {ret.mean():+.2f}%/笔。")

    text = f"""# 2026 年 2–7 月：两种 ST 处理口径的 20 日滚动回测

同一份 T 日 14:40 涨幅 3%–6% 候选、相同七因子与四分类提升树，逐日以前 20 个交易日的可监督样本训练，在 **2026-02-02 至 07-30 共 118 个折外交易日**选 Top1、Top2。下表只保留两种可比较的口径：① 从训练和选股中去掉全部 ST；② 仅去掉 **14:40 已涨停的 ST**，保留当时未涨停的 ST。

此前「控制 ST 总比例」方案保留了全部已涨停 ST，却抽掉多数未涨停 ST；比例虽接近 8–9 月，却改变了 ST 内部构成，**不再列为策略效果方案**。原因和日 K 核对见 [ST 涨停核对](st_limit_regime_audit.md)。原始三方案逐笔记录仍在 [审计文件](top2_verified.csv)，用于追溯旧结果；[ST 参与训练后只选非 ST 的顺延实验](rerank_nonst/README.md)也保留为诊断。

结果栏按 T+1 09:31–09:40 的**第二高价相对 T 日 14:40 价**分类：达标 ≥3%、可接受 [1%,3%)、中性 [0%,1%)、错误 <0%。09:40 收益使用该分钟**收盘价**，因此目标类别与固定卖出收益不一定同向。次日确认停牌的已选股票按约定记 0% 和中性。

月表收益依次为：已选股票**单笔平均**／假设每笔全额再投入的月复利价格代理／剔除单笔严格高于 +5% 或低于 −5% 后的平均；极端笔数按 `>+5% / <-5%` 排列。这些都未计实际买入成交、费用和滑点。按日表在无选股时显示「—」。

{chr(10).join(selected_stats)}

## 按月结果

{markdown_table(monthly_display(monthly))}

## 按日明细

{markdown_table(daily)}
"""
    (report / "README.md").write_text(text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--report", type=Path, default=REPORT)
    args = parser.parse_args()
    render(args.output, args.report)


if __name__ == "__main__":
    main()
