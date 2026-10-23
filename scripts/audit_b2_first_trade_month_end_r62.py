"""Audit B2's first trade by D0 model month and month-end R57 inputs.

This reads frozen R60/R61 assets. It does not retrain models or change trades.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.experiment_b2_recent_loss_resume_r55 import prior_return
from scripts.replay_limit_rebound_cash_r23 import sha


ROOT = Path("outputs/stock_automl/b2_2021_minute_r60_20261020")
R41 = Path("outputs/stock_automl/b2_2022_minute_r41_20261019")
R35 = Path("outputs/stock_automl/b2_two_month_2024_r35_20261019")
R60 = Path("docs/stock_automl_runs/20261020_b2_2021_minutes_r60")
R61 = Path("docs/stock_automl_runs/20261020_b2_model_month_r61")
OUT = Path("docs/stock_automl_runs/20261020_b2_first_trade_month_end_r62")
FEE = 0.001


def rule_ids(paths: list[Path]) -> dict[str, str]:
    rules = {}
    for path in paths:
        for item in json.loads(path.read_text()):
            month = item["month"]
            assert month not in rules, month
            rules[month] = item["selected"]["id"]
    return rules


def md_pct(value: float | None) -> str:
    return "—" if value is None or pd.isna(value) else f"{value:+.2%}"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    signal_path = ROOT / "signals.pkl"
    calendar_path = ROOT / "calendar.json"
    monthly_path = R60 / "逐月.csv"
    nav_path = R60 / "逐日净值.csv"
    r61_path = R61 / "模型归属.json"
    rule_paths = [ROOT / "rules_frozen.json", R41 / "rules_frozen.json",
                  R35 / "rules_frozen.json"]
    source_paths = [signal_path, calendar_path, monthly_path, nav_path,
                    r61_path, *rule_paths]

    months = pd.read_csv(monthly_path).set_index("月份")
    assert len(months) == 53 and months.index[0] == "2022-05" and months.index[-1] == "2026-09"
    calendar = json.loads(calendar_path.read_text())
    cal_index = {date: i for i, date in enumerate(calendar)}
    month_end = {date[:7]: date for date in calendar}
    signals = pd.read_pickle(signal_path)
    signals = signals[signals.d0_date.str[:7].isin(months.index)].copy()
    assert len(signals) == 709 and signals.d0_date.is_unique
    assert signals.model_month.eq(signals.d0_date.str[:7]).all()
    rules = rule_ids(rule_paths)
    assert len(rules) >= len(months)
    assert signals.apply(lambda row: row.rule_id == rules[row.model_month], axis=1).all()
    signals = signals.set_index("d0_date", drop=False)

    r61 = json.loads(r61_path.read_text())
    trades = pd.DataFrame(r61["逐笔"])
    assert len(trades) == 306 and trades.D0.is_unique
    assert trades.模型月.eq(trades.D0.str[:7]).all()
    actual = trades.set_index("D0", drop=False)
    for trade in trades.itertuples(index=False):
        source = signals.loc[trade.D0]
        assert trade.股票 == source.symbol and trade.买入日 == source.d1_date
        assert trade.卖出日 == calendar[cal_index[source.d1_date] + 1]
        expected = source.d2_close / source.entry * (1 - FEE) / (1 + FEE) - 1
        np.testing.assert_allclose(trade.费后收益, expected, rtol=0, atol=1e-12)

    first_model = {month: group.sort_values("D0").iloc[0]
                   for month, group in trades.groupby("模型月")}
    first_buy = {month: group.sort_values(["买入日", "D0"]).iloc[0]
                 for month, group in trades.groupby(trades.买入日.str[:7])}
    first_sell = {month: group.sort_values(["卖出日", "D0"]).iloc[0]
                  for month, group in trades.groupby(trades.卖出日.str[:7])}
    assert len(first_model) == 24 and int(months.月初暂停.sum()) == 29

    first_rows = []
    for month, gate in months.iterrows():
        own = first_model.get(month)
        buy = first_buy.get(month)
        sell = first_sell.get(month)
        assert (own is None) == bool(gate.月初暂停)
        shadow = signals[signals.model_month.eq(month)].sort_index().iloc[0]
        shadow_return = shadow.d2_close / shadow.entry * (1 - FEE) / (1 + FEE) - 1
        if own is not None:
            assert own.D0 == shadow.d0_date
            np.testing.assert_allclose(own.费后收益, shadow_return, rtol=0, atol=1e-12)
        source = signals.loc[own.D0] if own is not None else None
        first_rows.append(dict(
            模型月=month, 当月停摆=bool(gate.月初暂停),
            本模型首个影子D0=shadow.d0_date,
            本模型影子首笔费后收益=shadow_return,
            本模型首笔D0=own.D0 if own is not None else None,
            本模型首笔D1=own.买入日 if own is not None else None,
            本模型首笔D2=own.卖出日 if own is not None else None,
            本模型首笔股票=own.股票 if own is not None else None,
            本模型首笔四K规则=source.rule_id if source is not None else None,
            本模型首笔费后收益=own.费后收益 if own is not None else None,
            自然月首笔买入D0=buy.D0 if buy is not None else None,
            自然月首笔买入所属模型=buy.模型月 if buy is not None else None,
            自然月首笔卖出D0=sell.D0 if sell is not None else None,
            自然月首笔卖出所属模型=sell.模型月 if sell is not None else None,
            自然月首笔卖出费后收益=sell.费后收益 if sell is not None else None,
        ))
    first = pd.DataFrame(first_rows)
    assert first.本模型首笔D0.notna().sum() == 24
    assert first.自然月首笔买入D0.notna().sum() == 29
    assert (first.loc[first.自然月首笔买入D0.notna(), "自然月首笔买入所属模型"]
            != first.loc[first.自然月首笔买入D0.notna(), "模型月"]).sum() == 14
    assert first.自然月首笔卖出D0.notna().sum() == 33
    assert (first.loc[first.自然月首笔卖出D0.notna(), "自然月首笔卖出所属模型"]
            != first.loc[first.自然月首笔卖出D0.notna(), "模型月"]).sum() == 20

    nav = pd.read_csv(nav_path).set_index("date").sort_index()
    shadow_nav = nav[["原B2净值"]].rename(columns={"原B2净值": "nav"})
    end_rows = []
    for month in months.index:
        end = month_end[month]
        cohort = signals[signals.model_month.eq(month)]
        last = cohort.sort_index().iloc[-1]
        exit_day = calendar[cal_index[last.d1_date] + 1]
        next_month = str(pd.Period(month, freq="M") + 1)
        r5 = prior_return(shadow_nav, month, 5)
        r10 = prior_return(shadow_nav, month, 10)
        next_pause = None
        if next_month in months.index:
            gate = months.loc[next_month]
            assert int(gate.前月候选) == len(cohort)
            np.testing.assert_allclose([r5, r10],
                [gate.前月末5日, gate.前月末10日], rtol=0, atol=1e-12)
            next_pause = bool(gate.月初暂停)
        net = last.d2_close / last.entry * (1 - FEE) / (1 + FEE) - 1
        if last.d0_date in actual.index:
            np.testing.assert_allclose(net, actual.loc[last.d0_date].费后收益,
                                       rtol=0, atol=1e-12)
        end_rows.append(dict(
            模型月=month, 月末最后交易日=end, 本月最后D0=last.d0_date,
            本月完整D0候选数=len(cohort), R60记载D0候选数=int(months.loc[month].原B2候选),
            D0恰在月末=last.d0_date == end, D1模拟可买日=last.d1_date,
            D2影子卖出日=exit_day, 股票=last.symbol, 月初停摆=bool(months.loc[month].月初暂停),
            日线模型月=last.model_month, 四K规则=last.rule_id,
            D1仍沿用D0月规则=last.rule_id == rules[month],
            月末状态=("尚未买入" if last.d1_date > end else
                     "已买未卖，按D1收盘估值" if exit_day > end else "D2已结清"),
            最后D0影子费后收益=net, 月末影子净值=float(shadow_nav.loc[end, "nav"]),
            下月=next_month, 用于下月末5日收益=r5, 用于下月末10日收益=r10,
            下月门槛已回放=next_month in months.index,
            下月暂停=next_pause,
        ))
    month_end_frame = pd.DataFrame(end_rows)
    final_day = month_end_frame[month_end_frame.D0恰在月末]
    assert len(final_day) == 37
    assert int((~final_day.月初停摆).sum()) == 15
    assert final_day.月末状态.eq("尚未买入").all()
    assert month_end_frame.D1仍沿用D0月规则.all()
    assert month_end_frame.loc[month_end_frame.模型月.eq("2026-09"), "本月最后D0"].item() == "2026-09-30"
    assert month_end_frame.loc[month_end_frame.模型月.eq("2026-09"), "本月完整D0候选数"].item() == 16
    assert month_end_frame.loc[month_end_frame.模型月.eq("2026-09"), "R60记载D0候选数"].item() == 15
    assert month_end_frame.loc[month_end_frame.模型月.ne("2026-09"), "本月完整D0候选数"].equals(
        month_end_frame.loc[month_end_frame.模型月.ne("2026-09"), "R60记载D0候选数"])
    np.testing.assert_allclose(
        [actual.loc["2026-09-28"].费后收益, actual.loc["2026-09-30"].费后收益],
        [0.05328490751231296, -0.09260607813239374], rtol=0, atol=1e-12)

    first.to_csv(OUT / "真实模型首笔.csv", index=False, encoding="utf-8-sig")
    month_end_frame.to_csv(OUT / "月末D0与门槛.csv", index=False, encoding="utf-8-sig")
    audit = dict(
        模型月=53, 放行模型月=24, 停摆模型月=29,
        自然月有首笔买入=29, 其中属于上月模型=14,
        自然月有首笔卖出=33, 其中属于上月模型_与R61一致=20,
        月末最后交易日存在D0信号=37, 其中当月放行=15,
        信号模型月与D0一致=709, 信号四K规则与D0月冻结规则一致=709,
        已保存末5末10逐月复算一致=52, 已保存前月候选数逐月复算一致=52,
        九月完整D0候选=16, R60九月截止D1记载候选=15,
        源哈希={str(path): sha(path) for path in source_paths},
    )
    (OUT / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")

    lines = [
        "# R62：真实模型首笔与月末 D0 信号时点核对", "",
        "本轮只读 R60／R61 冻结信号和账户，不重训、不改停摆或买卖规则。",
        "“本模型首笔”按 D0 所在月排序，展示 R57 **实际放行**的第一笔完整 D2 费后收益；停摆月没有实际首笔，另存原 B2 影子首笔供核对。",
        "自然月第一笔买入、第一笔卖出可能由上月 D0 选出，不能用它评价新月模型。", "",
        "## 核对结论", "",
        "- 53 个模型月中 24 月放行、29 月停摆。29 个有买入的自然月中，14 月首笔买入属于上月模型；33 个有卖出的自然月中，20 月首笔卖出属于上月模型。",
        "- 37 个月在最后交易日出现 D0 最终信号，其中 15 月放行、22 月仅在影子账户模拟。其 D1 均在下月，故该信号在 D0 月末尚未买入，未来完整 D2 收益不进入当月末 5／10 日净值。",
        "- 月末 5／10 日收益取原 B2 影子账户截至本月最后一个交易日的**收盘净值**，分别除以窗口前一交易日净值。较早 D0 已买未卖的持仓按当日收盘估值纳入；月末新出现的 D0 信号尚未买入，不纳入收益。它仍计入本月 D0 候选数，52 组既有候选数和门槛值逐月复算一致。",
        "- 709 条 D0 信号的日线模型月与 D0 月一致，四 K 规则也与该 D0 月冻结规则逐条一致。即使 D1 落在下月，仍使用旧月 D0 选出的规则；下月新模型只处理下月 D0 信号。",
        "- 2026-09-28 D0 的 600802 在 09-29 买入、09-30 D2 卖出，费后 **+5.33%**，九月末净值已反映这笔交易。09-30 D0 的 605388 使用九月四 K 规则 `r001`，10-08 D1 模拟买入、10-09 D2 卖出，费后 **−9.26%**，不在九月末净值或末 5／10 日指标中。九月末影子净值计算的末 5／10 日收益分别为 **−3.55%／+11.54%**。R60 以 D1≤09-30 截止，仅记九月 15 条候选；R61 补齐末日 D0 后为 16 条。R60 未回放十月门槛，若后续计算十月前月候选数，应使用完整的 16 条。", "",
        "## 每个放行模型真正的第一笔", "",
        "| D0 模型月 | D0 | D1 买入 | D2 卖出 | 股票 | 四 K 规则 | D2 费后收益 |",
        "| --- | --- | --- | --- | --- | --- | ---: |",
    ]
    for row in first.itertuples(index=False):
        if pd.isna(row.本模型首笔D0):
            continue
        lines.append(f"| {row.模型月} | {row.本模型首笔D0} | {row.本模型首笔D1} | "
                     f"{row.本模型首笔D2} | {row.本模型首笔股票} | "
                     f"{row.本模型首笔四K规则} | {md_pct(row.本模型首笔费后收益)} |")
    lines += [
        "", "## 月末最后交易日确有 D0 的月份", "",
        "下表只列 D0 恰在月末的 37 月；其余月份最后 D0 更早，详见 `月末D0与门槛.csv`。",
        "“停摆”按该 D0 月决定，次月 D1 不重新套用新月开关。", "",
        "| D0 模型月 | 月末 D0 | 下月 D1 候选 | D2 影子结束 | 四 K 规则 | 当月停摆 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in final_day.itertuples(index=False):
        lines.append(f"| {row.模型月} | {row.本月最后D0} | {row.D1模拟可买日} | "
                     f"{row.D2影子卖出日} | {row.四K规则} | {'是' if row.月初停摆 else '否'} |")
    lines += [
        "", "## 复查入口", "",
        "- `真实模型首笔.csv`：53 月逐月列出原 B2 影子首笔和本模型真实首笔，另列自然月首笔买入／卖出的 D0 归属和收益；停摆月真实首笔留空。",
        "- `月末D0与门槛.csv`：53 月最后 D0、影子 D1／D2、规则、月末状态、影子净值，以及可在月末算出的 5／10 日收益。2026-09 的数值已复算，R60 没有十月门槛回放，十月暂停留空。",
        "- `audit.json`：计数、源哈希与逐月复算回执；复算入口为 `scripts.audit_b2_first_trade_month_end_r62`。",
        "", "本报告的历史模拟收益不等于真实委托成交；D1 分钟 open 仍是原买价代理。", "",
    ]
    (OUT / "README.md").write_text("\n".join(lines))
    print(json.dumps({k: v for k, v in audit.items() if k != "源哈希"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
