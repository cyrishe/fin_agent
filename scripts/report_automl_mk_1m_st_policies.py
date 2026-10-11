"""Publish the three ST-policy comparison as two compact Chinese tables."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import pandas as pd

from scripts.experiment_automl_mk_1m_st_policies import OUT, POLICIES


REPORT = Path("docs/stock_automl_runs/20261011_mk_1m_three_st_policies")


def markdown_table(frame: pd.DataFrame) -> str:
    columns = list(frame.columns)
    lines = ["| " + " | ".join(columns) + " |",
             "|" + "|".join(["---"] * len(columns)) + "|"]
    for row in frame.itertuples(index=False, name=None):
        lines.append("| " + " | ".join(str(value).replace("|", "\\|")
                                        for value in row) + " |")
    return "\n".join(lines)


def render(output: Path, report: Path) -> None:
    summary = json.loads((output / "summary.json").read_text())
    monthly = pd.read_csv(output / "monthly.csv")
    daily = pd.read_csv(output / "daily.csv")
    picks = pd.read_csv(output / "top2_verified.csv")
    if summary["test_days"] != 118 or len(monthly) != 18 or len(daily) != 354:
        raise ValueError("Incomplete six-month, three-policy report")
    if set(daily.方案) != set(POLICIES):
        raise ValueError("A policy is absent from the daily results")
    report.mkdir(parents=True, exist_ok=True)
    for name in ("monthly.csv", "daily.csv", "top2_verified.csv", "folds.csv",
                 "summary.json"):
        shutil.copy2(output / name, report / name)

    total_rows = summary["policy_counts"][POLICIES[2]]["rows"]
    st_rows = summary["policy_counts"][POLICIES[2]]["st_rows"]
    limit_st_rows = summary["policy_counts"][POLICIES[2]]["st_at_limit_rows"]
    reference = summary["aug_sep_reference_counts"]
    rate = 100 * st_rows / total_rows
    selected_stats = []
    for policy in POLICIES:
        detail = summary["per_policy"][policy]
        day_rows = daily[daily.方案.eq(policy)]
        result_counts = "/".join(
            f"{label}{int(day_rows['Top1结果'].eq(label).sum())}"
            for label in ("达标", "可接受", "中性", "错误"))
        ret = picks.loc[picks.policy.eq(policy) & picks.selection_rank.eq(1),
                        "actual_0940_return"] * 100
        selected_stats.append(
            f"- **{policy}**：Top1 选股 {detail['top1_days']}/118 日；"
            f"{result_counts}；固定 09:40 平均 {ret.mean():+.2f}%/笔；"
            f"Top1 ST {detail['st_top1_picks']} 笔，"
            f"其中信号时涨停 ST {detail['limit_st_top1_picks']} 笔；"
            f"次日停牌按 0% 记账 {detail['suspension_zero_picks']} 笔，"
            f"结果未知 {detail['unknown_picks']} 笔。")
    unknown = picks[picks.actual_0940_return.isna()]
    policy3_top1 = picks[picks.policy.eq(POLICIES[2]) &
                         picks.selection_rank.eq(1)]
    policy3_top2 = picks[picks.policy.eq(POLICIES[2]) &
                         picks.selection_rank.eq(2)]
    policy3_buyable = policy3_top1[~policy3_top1.entry_at_limit]
    policy3_limited = policy3_top1[policy3_top1.entry_at_limit]
    unknown_note = ("所有已选股票都有可观察的次晨收益或确认停牌。"
                    if unknown.empty else
                    f"仍有 {len(unknown)} 笔已选股票缺少可靠次晨收益，表中显示“缺数据”。")
    text = f"""# 2026 年 1–7 月：三种 ST 口径的 20 日滚动模型对照

同一份 **59,959 条** T 日 14:40 候选、相同七因子、相同四分类提升树和选股规则，逐日用前 **20 个交易日**的可监督历史样本训练，再对当日候选评分。折外选股日为 **2026-02-02 至 2026-07-30，共 118 日**；2026-07-31 没有下一交易日分钟档案，所以不计入。这里只调整 ST 的样本与推理候选口径：① 全部去 ST；② 只去信号时已涨停的 ST；③ 保留全部涨停 ST、用固定的股票－日期哈希保留约 22% 未涨停 ST，使**全期候选池** ST 约占 3.5%。第③组实际为 **{st_rows}/{total_rows} = {rate:.2f}%**。固定哈希不读取次日结果，也不按回测收益挑样本；这不是逐日或每个训练窗都严格 3.5%。保留所有涨停 ST 时，部分交易日仅这些股票就超过 3.5%，无法同时满足逐日硬上限。

第①、②组复用已经完整训练并保存的 [完全去 ST](../20261010_mk_1m_jan_jul_nonst_rolling/README.md) 和 [仅去涨停 ST](../20261011_mk_1m_tradable_st_rolling20/README.md) 结果。复算时逐项核对了候选键、七因子、次晨标签和 118 个训练窗行数；已重新拟合的前段交易日，选股与四类概率也一致。第③组是本次新训练的 118 个滚动模型。

8–9 月参照候选池的历史 ST 比例为 **{reference['st_share'] * 100:.2f}%**，所以 3.5% **仅接近总占比，不等于相同分布**。第③组的 ST 中有 **{limit_st_rows} 条在 14:40 已涨停**；8–9 月参照池 {reference['st_rows']} 条 ST 按同一涨停价算法为 **{reference['st_at_limit_rows']} 条**。一个关键背景是 2026-07-06 起沪深主板风险警示股票涨跌幅限制由 5% 调整为 10%（[上交所发布说明](https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20260424_10816474.shtml)、[深交所实施通知](https://www.szse.cn/lawrules/service/member/t20260630_621404.html)）；8–9 月 3%–6% 涨幅的主板 ST 因此通常不会触及涨停。训练使用已成熟的历史次晨标签；T 日 14:40 候选与 ST 取舍**不查看 T 日 14:40 之后或 T+1 行情**。没有可定义次晨目标的历史样本不能参与监督拟合，但在其 T 日仍具有推理资格。已选股票次日若日线成交量为零，固定卖出回测按用户约定记 0%；表内结果栏按回测口径记“中性”，但监督训练标签仍保持未知。

“达标／可接受／中性／错误”按 **T+1 09:31–09:40 第二高价相对 T 14:40 信号价**划分：≥3%、[1%,3%)、[0%,1%)、<0%。09:40 收益另按**该分钟收盘价**相对信号价计算，目标达标仍可能在固定卖出时亏损。选股沿用原四类预测规则，优先预测达标类，其次预测可接受类；Top1、Top2 每天各至多一只，无选股日记“—”。月表的收益是当月**已选笔数的简单平均**，四类数是当月对应排名的笔数；不计费用、滑点或实际买入成交。

{chr(10).join(selected_stats)}

{unknown_note}第③组 Top1 的 **{len(policy3_limited)}/{len(policy3_top1)} 笔**、Top2 的 **{int(policy3_top2.entry_at_limit.sum())}/{len(policy3_top2)} 笔**在信号时已涨停。Top1 表内整体 **{policy3_top1.actual_0940_return.mean() * 100:+.2f}%/笔**主要受这部分价格路径影响；仅观察其余 **{len(policy3_buyable)} 笔**，09:40 平均为 **{policy3_buyable.actual_0940_return.mean() * 100:+.2f}%/笔**。这只是对既有 Top1 名单做可买入性拆分，**没有在涨停日重新补选**，因此不能视为可执行策略收益。逐笔身份及 ST／涨停标记见 [Top2 原始记录](top2_verified.csv)，训练窗行数见 [折记录](folds.csv)，来源摘要见 [summary.json](summary.json)。

## 按月结果

{markdown_table(monthly)}

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
