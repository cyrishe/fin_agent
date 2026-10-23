from pathlib import Path
import glob
import hashlib
import json

import numpy as np
import pandas as pd

ROOT = Path("/data/cyris/work")
OUT = ROOT / "b2_d2_open_volume_opportunity_r82_20261022"
R81 = ROOT / "b2_d2_open_volume_ranks_r81_20261022" / "09_31仍封死19笔_核心变量与结局.csv"
R71 = ROOT / "b2_board_volume_r71_20261022" / "B2_V2放行连板量能特征_306笔.csv"
R79 = ROOT / "b2_d2_floor_volume_r79_20261022" / "100笔D2触跌停_成交量逐笔.csv"
LEDGERS = sorted(glob.glob(str(ROOT / "b2_news_flow_years_r76_20261022" / "20*_morning7_baseline_ledger.csv")))
assert len(LEDGERS) == 5
ledger = pd.concat([pd.read_csv(p) for p in LEDGERS], ignore_index=True)
ledger[["股票", "D0选中日"]] = ledger["id"].str.split("|", expand=True)
assert len(ledger) == 306 and ledger.id.nunique() == 306
assert np.allclose(ledger.pnl / ledger.buy_cost, ledger["return"], rtol=1e-5, atol=1e-5)

case = pd.read_csv(R81)
floor = pd.read_csv(R79)
case = case.merge(
    ledger[["股票", "D0选中日", "return"]],
    on=["股票", "D0选中日"], validate="one_to_one"
).merge(
    floor[["股票", "D0选中日", "D2官方前收", "D2收盘价", "D3收盘涨跌幅_pct"]],
    on=["股票", "D0选中日"], validate="one_to_one"
)
assert len(case) == 19
case["原卖法费后收益_pct"] = case["return"] * 100
case["D2收盘涨跌幅_pct"] = (case["D2收盘价"] / case["D2官方前收"] - 1) * 100
case["原卖法费后盈利"] = (case["return"] > 0).astype(int)
case["D3收盘上涨"] = (case["D3收盘涨跌幅_pct"] > 0).astype(int)
case["每笔1万元旧账盈亏_元"] = case["return"] * 10000
case = case.sort_values("首分钟量除板前20日同刻最大")
case.to_csv(OUT / "19笔09_31仍封死_费后收益与D3.csv", index=False, encoding="utf-8-sig")

board = pd.read_csv(R71)
full = board.merge(
    ledger[["股票", "D0选中日", "return"]],
    on=["股票", "D0选中日"], validate="one_to_one"
)
assert len(full) == 306
feature = "D0量除首板前20日均量"
valid = full.loc[full[feature].notna()].sort_values([feature, "股票", "D0选中日"]).copy()
assert len(valid) == 297
valid["D0量比全期排序"] = range(1, len(valid) + 1)
labels = ["低20%", "次低20%", "中20%", "次高20%", "高20%"]
valid["D0量比分位"] = pd.qcut(valid["D0量比全期排序"], 5, labels=labels).astype(str)
full = full.merge(
    valid[["股票", "D0选中日", "D0量比全期排序", "D0量比分位"]],
    on=["股票", "D0选中日"], how="left", validate="one_to_one"
)
full["D0量比分位"] = full["D0量比分位"].fillna("基线缺失")
full["原卖法费后收益_pct"] = full["return"] * 100
full["若排除则少赚_每笔1万元"] = full["return"].clip(lower=0) * 10000
full["若排除则避亏_每笔1万元"] = -full["return"].clip(upper=0) * 10000
full["D0年份"] = full["D0选中日"].str[:4]
full[[
    "股票", "D0选中日", "D1买入日", "D2原卖出日", "D0年份",
    feature, "D0量比全期排序", "D0量比分位", "原卖法费后收益_pct",
    "若排除则少赚_每笔1万元", "若排除则避亏_每笔1万元",
    "D2开盘跌停", "D2D3连续收盘跌停"
]].sort_values(["D0选中日", "股票"]).to_csv(
    OUT / "306笔D0已知量比_原交易收益.csv", index=False, encoding="utf-8-sig"
)

def aggregate(group):
    wins = group.loc[group["return"] > 0]
    losses = group.loc[group["return"] < 0]
    miss = wins["return"].sum() * 10000
    avoid = -losses["return"].sum() * 10000
    return pd.Series({
        "笔数": len(group),
        "最小D0量比": group[feature].min(),
        "最大D0量比": group[feature].max(),
        "盈利笔数": len(wins),
        "收益超过5%笔数": (group["return"] > .05).sum(),
        "收益超过10%笔数": (group["return"] > .10).sum(),
        "亏损笔数": len(losses),
        "若排除则少赚_每笔1万元合计": round(miss, 2),
        "若排除则避亏_每笔1万元合计": round(avoid, 2),
        "若排除净增_每笔1万元合计": round(avoid-miss, 2),
        "D2开盘跌停笔数": int(group["D2开盘跌停"].sum()),
        "D2D3连续收盘跌停笔数": int(group["D2D3连续收盘跌停"].sum()),
    })

summary = full.groupby("D0量比分位", sort=False).apply(aggregate, include_groups=False).reset_index()
summary["D0量比分位"] = pd.Categorical(summary["D0量比分位"], categories=labels + ["基线缺失"], ordered=True)
summary = summary.sort_values("D0量比分位")
summary["D0量比分位"] = summary["D0量比分位"].astype(str)
summary.to_csv(OUT / "D0量比五档误拦得失.csv", index=False, encoding="utf-8-sig")
years = full[full["D0量比分位"] == "低20%"].groupby("D0年份").apply(aggregate, include_groups=False).reset_index()
years.to_csv(OUT / "低20%各年误拦得失.csv", index=False, encoding="utf-8-sig")

def line(v):
    return "／".join(str(x) for x in v)
low = case[case["板前20日同刻最大量比分段"] == "低端5"]
high = case[case["板前20日同刻最大量比分段"] == "高端5"]
low_buy = full[full["D0量比分位"] == "低20%"]
low_summary = summary[summary["D0量比分位"] == "低20%"].iloc[0]
text = f"""# R82：开盘低量预警会不会误伤上涨交易

## 结论

F05＝D2 09:31 完成的首分钟量／首板前20日同刻最大量。此时 D1 已买入。若只在“D2 开盘跌停且 09:31 仍封死”时提示卖出难度，并继续执行既定的触跌停持续挂卖单，它**不会取消买入，新增的误拦盈利笔数是零**。把 F05 解释成 D1 买前禁买条件，是时间穿越。

受限的 19 笔中，按 F05 排序的低端 5 笔 D2 全部一价封死、D2 收盘全为跌停附近、D3 收盘全为负涨幅（其中 4 笔仍跌停）。但原“上午>7%否则收盘全额成交”价格账本有 {int(low['原卖法费后盈利'].sum())} 笔费后正收益：603137.SH 的 D0=2026-07-14，费后 +{low['原卖法费后收益_pct'].max():.2f}%；该 D2 全天一价封死、未证实卖得出，不能将账面盈利视为已实现。高端5笔也不是“好交易”：原费后盈利 {int(high['原卖法费后盈利'].sum())} 笔，但 {int(high['D3收盘上涨'].sum())} 笔 D3 收盘上涨，说明“是否开板”“旧账交易盈亏”“D3 反弹”是不同目标。

## 在买前可用变量上做误伤压力测试

另取 **D0 收盘已知**的“D0 成交量／首板前20日平均量”，对全部 306 笔 V2 放行买入评估。它与 F05 是不同变量，不把两者当同一模型。297 笔有完整板前20日基线，9 笔缺失；按 297 笔历史排序分五档，低20%为60笔，量比 {low_summary['最小D0量比']:.3f}～{low_summary['最大D0量比']:.3f}。

| 若禁买 D0 量比低20% | 结果 |
| --- | ---: |
| 被取消 | {len(low_buy)} 笔 |
| 原本费后盈利／其中 >5%／其中 >10% | {int(low_summary['盈利笔数'])}／{int(low_summary['收益超过5%笔数'])}／{int(low_summary['收益超过10%笔数'])} 笔 |
| 每笔固定投1万元，少赚 | {low_summary['若排除则少赚_每笔1万元合计']:,.2f} 元 |
| 同口径避亏 | {low_summary['若排除则避亏_每笔1万元合计']:,.2f} 元 |
| 避亏减少赚 | {low_summary['若排除净增_每笔1万元合计']:+,.2f} 元 |

2026 年同一低端档包含20笔，误拦9笔盈利、3笔费后收益超过10%；少赚7,415.58元，避亏6,432.37元，净 **−983.21元**。因此作为买前硬过滤会确实误伤好票，且效果分年不稳定。全期五档及每年明细见 CSV。

这个五档阈值是**事后用完整历史排序**，属于说明机会成本的探索，不是事前固定阈值的交易回测；每笔1万元只是等本金归因，未重放组合现金、月度影子停摆和因卖不出而延期的反馈。2022—24 亦主要处于原模型开发样本内。旧账本中跌停卖出按收盘价全额成交，实际成交不能由分钟价验证。任何买前过滤升级需冻结 D0/D1 可用特征和门槛，随后在未用过的月份检验盈利误拦、尾部风险与连续账户。

## 复算输入

- R81 主样本19笔及原始分钟独立审计。
- R79 D2/D3 跌停逐笔价格与风险标签。
- R71 V2 放行306笔 D0 连板量能表。
- R76 2022—2026 五份同卖法逐笔账本：买入与卖出各0.1%费用，D2上午完成分钟最高价严格高于 D1 收盘7%时卖，否则盘末价卖；50笔退出未核实。
- 所有 19/306 笔按股票＋D0 一对一合并，收益与 pnl/buy_cost 核对。
- 关联 KingdomAI 既有不可变策略 `imac/B2_V2`，定义 SHA-256 `54ebe0150dfefa6ce9837d403fd83b331f2720ad835271525e3106aae7529f00`；R82 是诊断研究，没有训练新模型。
- 本次仅服务器计算，不修改正式 B2_V2、生产库或定时任务。
"""
(OUT / "README.md").write_text(text, encoding="utf-8")
inputs = [str(R81), str(R71), str(R79), *LEDGERS]
outputs = [str(OUT/p) for p in ["analyze.py", "README.md", "19笔09_31仍封死_费后收益与D3.csv", "306笔D0已知量比_原交易收益.csv", "D0量比五档误拦得失.csv", "低20%各年误拦得失.csv"]]
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
(OUT / "manifest.json").write_text(json.dumps({"inputs": {p: sha(p) for p in inputs}, "outputs": {p: sha(p) for p in outputs}}, indent=2, ensure_ascii=False), encoding="utf-8")
print(summary.to_string(index=False))
print(years[["D0年份","笔数","盈利笔数","收益超过10%笔数","若排除则少赚_每笔1万元合计","若排除则避亏_每笔1万元合计","若排除净增_每笔1万元合计"]].to_string(index=False))
print("r81 low",len(low),"winners",int(low["原卖法费后盈利"].sum()),"D3 positive",int(low["D3收盘上涨"].sum()))
print("output",OUT)
