"""Attribute frozen B2/R57 outcomes to the D0 model month, including late exits.

This is a read-only research report. The R57 pause decision stays on the
prior calendar month's shadow-account closing NAV; this file does not change
the strategy or pretend late D2 outcomes were known at the month boundary.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.replay_limit_rebound_cash_r23 import replay, sha


ROOT = Path("outputs/stock_automl/b2_2021_minute_r60_20261020")
R60 = Path("docs/stock_automl_runs/20261020_b2_2021_minutes_r60")
R59 = Path("docs/stock_automl_runs/20261020_b2_first5_losses_r59")
OUT = Path("docs/stock_automl_runs/20261020_b2_model_month_r61")
FIRST, LAST = "2022-05", "2026-09"
INITIAL, FEE = 1_000_000., .001


def loss_runs(returns: pd.Series) -> list[int]:
    result, count = [], 0
    for value in returns:
        if value < 0:
            count += 1
        elif count:
            result.append(count)
            count = 0
    if count:
        result.append(count)
    return result


def first_five(frame: pd.DataFrame) -> dict:
    first = frame.sort_values(["d0_date", "entry_date", "symbol"]).head(5)
    values = first.return_.to_numpy(dtype=float)
    if len(values) < 5:
        return dict(前五盈亏=None, 前四亏数=None, 前五亏数=None,
                    前五三亏笔数=None, 前五三亏确认日=None)
    four, five = int((values[:4] < 0).sum()), int((values < 0).sum())
    trigger = 4 if four >= 3 else (5 if five >= 3 else None)
    return dict(前五盈亏="".join("亏" if x < 0 else "盈" for x in values),
                前四亏数=four, 前五亏数=five, 前五三亏笔数=trigger,
                前五三亏确认日=str(first.iloc[trigger - 1].exit_date) if trigger else None)


def cohort_replay(signals: pd.DataFrame, calendar: list[str], index: dict[str, int]) -> dict:
    first = str(signals.d1_date.min())
    last = str(signals.d1_date.max())
    end = calendar[index[last] + 1]
    summary, _, _, ledger, excluded = replay(
        signals, calendar, start=first, end=end, initial=INITIAL, fee_rate=FEE)
    assert not excluded and summary["entries"] == summary["closed"] == len(signals)
    assert summary["pending"] == 0 and len(ledger) == len(signals)
    return dict(收益=float(summary["total_return"]), 回撤=float(summary["max_drawdown"]),
                结清日=end)


def to_records(frame: pd.DataFrame) -> list[dict]:
    return json.loads(frame.replace({np.nan: None}).to_json(
        orient="records", force_ascii=False, date_format="iso"))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    source = ROOT / "signals.pkl"
    calendar_path = ROOT / "calendar.json"
    month_path = R60 / "逐月.csv"
    nav_path = R60 / "逐日净值.csv"
    old_path = R59 / "逐月前五笔.csv"
    all_signals = pd.read_pickle(source)
    calendar = json.loads(calendar_path.read_text())
    index = {day: i for i, day in enumerate(calendar)}
    months = pd.read_csv(month_path)
    nav_old = pd.read_csv(nav_path)
    old_first5 = pd.read_csv(old_path).set_index("月份")
    assert len(months) == 53 and months.月份.iloc[0] == FIRST and months.月份.iloc[-1] == LAST
    signals = all_signals[all_signals.d0_date.str[:7].between(FIRST, LAST)].copy()
    assert signals.d0_date.is_unique and signals.model_month.eq(signals.d0_date.str[:7]).all()
    assert len(signals) == 709  # R60's D1<=Sep30 endpoint omitted Sep30 D0 / Oct8 D1.
    extra = signals[signals.d1_date.gt("2026-09-30")]
    assert len(extra) == 1 and extra.iloc[0].d0_date == "2026-09-30"
    assert extra.iloc[0].d1_date == "2026-10-08" and calendar[index["2026-10-08"]+1] == "2026-10-09"

    gate = months.set_index("月份").月初暂停.to_dict()
    active = signals[~signals.d0_date.str[:7].map(gate)].copy()
    assert len(active) == 306
    original, _, _, shadow_ledger, excluded = replay(
        signals, calendar, start="2022-05-01", end="2026-10-09",
        initial=INITIAL, fee_rate=FEE)
    assert not excluded and original["entries"] == original["closed"] == 709
    actual, _, actual_nav, actual_ledger, excluded = replay(
        active, calendar, start="2022-05-01", end="2026-10-09",
        initial=INITIAL, fee_rate=FEE)
    assert not excluded and actual["entries"] == actual["closed"] == 306
    for ledger in (shadow_ledger, actual_ledger):
        ledger["模型月"] = ledger.d0_date.str[:7]
        ledger["卖出月"] = ledger.exit_date.str[:7]
    saved = pd.read_csv(R60 / "实买账本.csv", dtype={"股票": str})
    left = actual_ledger[actual_ledger.d0_date.isin(saved.D0)].set_index("d0_date")
    right = saved.set_index("D0")
    assert len(left) == len(right) == 305 and left.index.equals(right.index)
    np.testing.assert_allclose(left.return_, right.D2费后收益, rtol=0, atol=1e-12)
    np.testing.assert_allclose(left.pnl, right.盈亏额, rtol=0, atol=1e-6)
    assert actual_ledger.d0_date.eq("2026-09-30").sum() == 1

    monthly, before_after, first_day, cross_month, trades = [], [], [], [], []
    nav_old["月"] = nav_old.date.str[:7]
    first_dates = nav_old.groupby("月", sort=True).head(1).set_index("月")
    for month in months.月份:
        cohort = signals[signals.d0_date.str[:7].eq(month)]
        original_lots = shadow_ledger[shadow_ledger.模型月.eq(month)].sort_values("d0_date")
        actual_lots = actual_ledger[actual_ledger.模型月.eq(month)].sort_values("d0_date")
        assert len(cohort) == len(original_lots)
        assert len(actual_lots) == (0 if gate[month] else len(cohort))
        independent = cohort_replay(cohort, calendar, index)
        np.testing.assert_allclose(
            original_lots.return_.to_numpy(),
            (original_lots.sell / original_lots.buy * (1-FEE) / (1+FEE)-1).to_numpy(),
            rtol=0, atol=1e-12)
        count = len(actual_lots)
        first5 = first_five(actual_lots)
        runs = loss_runs(actual_lots.return_) if count else []
        gate_row = months.set_index("月份").loc[month]
        monthly.append(dict(
            模型月=month, 停摆="是" if gate[month] else "否",
            上月末5加10=float(gate_row.前月末5日 + gate_row.前月末10日)
                if pd.notna(gate_row.前月末5日) else None,
            原候选=len(cohort), 原模型胜率=float(original_lots.return_.gt(0).mean()),
            原模型笔均=float(original_lots.return_.mean()),
            原模型独立收益=independent["收益"],
            实买=count, 跨月卖=int(actual_lots.卖出月.ne(month).sum()),
            实买胜率=float(actual_lots.return_.gt(0).mean()) if count else None,
            实买笔均=float(actual_lots.return_.mean()) if count else None,
            实买笔收益和=float(actual_lots.return_.sum()) if count else 0.,
            实买盈亏元=float(actual_lots.pnl.sum()) if count else 0.,
            实买模型独立收益=independent["收益"] if count else 0.,
            实买模型独立回撤=independent["回撤"] if count else 0.,
            前五盈亏=first5["前五盈亏"], 前五三亏笔数=first5["前五三亏笔数"],
            三连亏=int(any(x >= 3 for x in runs)) if count else None,
            首次三连亏确认日=(
                next((str(actual_lots.iloc[i].exit_date) for i in range(2, count)
                      if all(actual_lots.iloc[j].return_ < 0 for j in (i-2, i-1, i))), None)
                if count else None),
            最后D0=str(cohort.d0_date.max()), 最晚卖出=str(original_lots.exit_date.max())))

        day = first_dates.loc[month]
        first_date = str(day.date)
        shadow_exposed = shadow_ledger[
            shadow_ledger.entry_date.le(first_date) & shadow_ledger.exit_date.ge(first_date)]
        actual_exposed = actual_ledger[
            actual_ledger.entry_date.le(first_date) & actual_ledger.exit_date.ge(first_date)]
        assert shadow_exposed.模型月.ne(month).all() and actual_exposed.模型月.ne(month).all()
        if day.原B2日收益 != 0:
            assert len(shadow_exposed) > 0
        if day.停摆日收益 != 0:
            assert len(actual_exposed) > 0
        first_day.append(dict(月份=month, 首个交易日=str(day.date),
            原B2首日日收益=float(day.原B2日收益), 停摆首日日收益=float(day.停摆日收益),
            归属="上月模型持仓或交易" if month != FIRST else "起始月，无上月持仓"))

    for lot in actual_ledger.itertuples():
        trades.append(dict(模型月=lot.模型月, D0=lot.d0_date, 买入日=lot.entry_date,
            卖出日=lot.exit_date, 股票=lot.symbol, D0排名=int(lot.d0_rank),
            买价=float(lot.buy), 卖价=float(lot.sell), 费后收益=float(lot.return_),
            投入元=float(lot.cost), 盈亏元=float(lot.pnl), 跨月卖=int(lot.模型月 != lot.卖出月)))
        if lot.模型月 != lot.卖出月:
            cross_month.append(trades[-1].copy())

    # Compare the historical sell-month first-five calculation with the D0
    # model's own first five for the same 14 months and same fee-net trades.
    study = months.loc[months.月份.ge("2024-03") & ~months.月初暂停, "月份"].tolist()
    assert len(study) == 14
    first_exits = actual_ledger.sort_values(["exit_date", "entry_date", "symbol"]).groupby(
        "卖出月", sort=True).head(1).set_index("卖出月")
    for month in study:
        new = next(row for row in monthly if row["模型月"] == month)
        old = old_first5.loc[month]
        exit_first = first_exits.loc[month]
        old_trigger = int(old.判断笔数) if pd.notna(old.判断笔数) else None
        before_after.append(dict(模型月=month, 实买=new["实买"],
            卖出月首笔D0=str(exit_first.d0_date),
            卖出月首笔属上月=int(str(exit_first.d0_date)[:7] != month),
            旧前五盈亏=str(old.前五盈亏), 旧三亏笔数=old_trigger,
            模型前五盈亏=new["前五盈亏"], 模型三亏笔数=new["前五三亏笔数"],
            模型三连亏=new["三连亏"], 首次三连亏确认日=new["首次三连亏确认日"] or None))

    summary = pd.DataFrame(monthly)
    before_after_frame = pd.DataFrame(before_after)
    assert summary.原候选.sum() == 709 and summary.实买.sum() == 306
    assert summary.跨月卖.sum() == len(cross_month) == 31
    assert sum(x["卖出月首笔属上月"] for x in before_after) == 7
    assert before_after_frame.旧三亏笔数.notna().sum() == 6
    assert before_after_frame.模型三亏笔数.notna().sum() == 8
    assert before_after_frame.模型三连亏.sum() == 10
    assert sum(x["模型月"] == "2026-09" for x in cross_month) == 1
    first_exits_in_scope = first_exits[first_exits.index.isin(months.月份)]
    assert len(first_exits_in_scope) == 33
    assert first_exits_in_scope.模型月.ne(first_exits_in_scope.index).sum() == 20
    np.testing.assert_allclose(summary.实买盈亏元.sum(), actual["final_nav"]-INITIAL,
                               rtol=0, atol=1e-6)
    source_paths = [source, calendar_path, month_path, nav_path, old_path,
                    R60 / "实买账本.csv"]
    payload = dict(
        口径=dict(模型月="D0 日期所在月；D1/D2 跨月仍归该模型。",
                  模型独立收益="每月仅放本模型 D0 信号，从 100 万起账并结清全部 D2；不等于连续账户自然月收益。",
                  实买笔收益和="按模型月直接相加各笔 D2 买卖各 0.1% 后的收益率，不复利。",
                  停摆="沿用 R57 由上月影子账户自然日净值决定的门槛；本报告不改策略。",
                  前五与连亏="按 D0 模型月取实买笔，并按该模型 D0 顺序；亏损在 D2 收盘后才确认。",
                  截止="模型 D0 截至 2026-09-30；最后一笔 D2 结清于 2026-10-09。"),
        月模型=to_records(summary), 旧新前五=to_records(before_after_frame),
        月首日=first_day, 跨月卖=cross_month, 逐笔=trades,
        审计=dict(月模型=len(months), 原候选=len(signals), 实买=len(active),
                  原R60截至九月D1候选=708, 新增九月末D0=1,
                  跨月卖=len(cross_month), 研究14月旧前五触发=6,
                  研究14月模型前五触发=8, 研究14月模型三连亏=10,
                  研究14月卖出月首笔属上月=7,
                  有实卖月份=33, 其中首笔来自上月模型=20,
                  影子首日非零=int(pd.DataFrame(first_day).原B2首日日收益.ne(0).sum()),
                  停摆账户首日非零=int(pd.DataFrame(first_day).停摆首日日收益.ne(0).sum()),
                  账户月自然日收益不可用于模型归属=True,
                  源哈希={str(p):sha(p) for p in source_paths}))
    (OUT / "模型归属.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)+"\n")
    pct = lambda value: "—" if value is None else f"{value:+.2%}"
    rate = lambda value: "—" if value is None else f"{value:.1%}"
    lines = [
        "# R61：严格按 D0 月模型归属的 B2 交易与亏损统计",
        "",
        "本报告只重组已冻结 B2/R57 信号，不更换模型、停摆门槛或买卖规则。",
        "模型月就是 D0 所在月：若 D1 买入或 D2 卖出跨月，整笔仍归 D0 模型。",
        "`原模型独立收益`把每个月的原 B2 信号单独放进 100 万元账户，",
        "按原半仓／余款全买与买卖各 0.1% 费用执行，直到该模型最后一笔 D2 结清。",
        "`实买独立收益`只保留 R57 放行的模型月；停摆月为 0。",
        "这些独立收益不能逐月连乘成连续账户总收益。",
        "",
        "## 已确认的归属问题",
        "",
        "- 53 个 D0 模型月有 709 条原候选、306 笔放行实买；31 笔在下一月 D2 卖出。",
        "- 33 个发生过卖出的自然月中，20 个月的第一笔卖出属于上月 D0 模型；用户此前关注的 14 个运行月为 7／14。",
        "- 月首首个交易日的非零影子账户日收益出现 45／53 月；停摆账户 20／53 月。逐笔核对后，这些首日盈亏均来自上月 D0 模型持仓或交易，而非本月模型。",
        "- 旧 R59 按卖出月取前五笔，14 个运行月有 6 月前五累计三亏；改按 D0 模型月为 **8／14**，新增 2024-12 与 2025-10。2026-09 的判断从第 5 笔改为第 4 笔。",
        "- 旧 R58 按卖出月出现三连亏为 9／14；D0 模型月为 **10／14**，新增 2026-04。该模型的第三亏到 2026-05-06 D2 收盘才确认，不能说它在 4 月已经可见。",
        "- 旧 R60 截至 2026-09-30 的 D1 买入日，仅含 708 条信号；完整 9 月 D0 模型还包括 2026-09-30 一笔，10 月 8 日买入、10 月 9 日卖出，费后 −9.26%。R61 的 709／306 均包含它。",
        "",
        "## 53 个 D0 月模型",
        "",
        "| 模型月 | 停摆 | 原候选 | 原模型独立收益 | 实买 | 跨月卖 | 实买胜率 | 实买笔均 | 实买独立收益 | 前五三亏笔 | 三连亏 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in monthly:
        lines.append("| " + " | ".join([
            row["模型月"], row["停摆"], str(row["原候选"]),
            pct(row["原模型独立收益"]), str(row["实买"]), str(row["跨月卖"]),
            rate(row["实买胜率"]), pct(row["实买笔均"]),
            pct(row["实买模型独立收益"]),
            str(row["前五三亏笔数"] or "—"),
            "是" if row["三连亏"] == 1 else ("否" if row["三连亏"] == 0 else "—"),
        ]) + " |")
    lines.extend([
        "", "## 用户先前 14 个运行月的前五笔口径对照", "",
        "| 模型月 | 卖出月首笔属上月 | 旧前五 | 旧触发 | 模型前五 | 模型触发 | 模型三连亏确认日 |",
        "| --- | --- | --- | ---: | --- | ---: | --- |",
    ])
    for row in before_after:
        lines.append("| " + " | ".join([
            row["模型月"], "是" if row["卖出月首笔属上月"] else "否",
            row["旧前五盈亏"], str(row["旧三亏笔数"] or "—"),
            row["模型前五盈亏"], str(row["模型三亏笔数"] or "—"),
            row["首次三连亏确认日"] or "—",
        ]) + " |")
    lines.extend([
        "", "## 使用边界", "",
        "月模型完整交易收益要等最后一笔 D2 才能确定；模型月末 D0 候选的盈亏可能到下月才知道。",
        "本报告不能把这些事后完整收益直接用于下月首日的停摆判断。",
        "原 R57 停摆输入仍是上月末 5／10 个交易日影子账户的当日收盘净值变化；",
        "该指标在月末收盘后可见，但可能包含更早月模型的跨月交易。",
        "`模型归属.json` 保存 53 月结果、306 笔逐笔、31 笔跨月卖、月首日与源哈希。",
        "配套 Excel 在 `outputs/b2_model_month_r61_20261020/B2_严格月模型归属_R61_20261020.xlsx`。",
        "策略定义及 KingdomAI 生产记录未变；这是历史归属诊断。",
        "",
    ])
    (OUT / "README.md").write_text("\n".join(lines))
    print(json.dumps(payload["审计"], ensure_ascii=False, indent=2))
    print(summary.loc[summary.模型月.between("2022-05", "2023-04"),
                      ["模型月", "停摆", "原候选", "原模型独立收益", "实买",
                       "跨月卖", "实买笔均", "实买模型独立收益"]].to_string(index=False))
    print(before_after_frame.to_string(index=False))


if __name__ == "__main__":
    main()
