"""Attach as-of market amounts and an experimental learned activity cutoff to picks."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path("docs/stock_automl_runs")
OUT = Path("outputs/stock_automl/market_state_threshold_exploration")
POLICY = "只去掉14:40涨停ST"


def market_features(market: pd.DataFrame) -> pd.DataFrame:
    """All values for T use only market bars ending no later than T 14:40."""
    market = market.sort_values("trade_date").reset_index(drop=True)
    if market.trade_date.duplicated().any():
        raise ValueError("Duplicate market trading day")
    records = []
    amount = pd.to_numeric(market.amount_1440_100m_cny, errors="coerce")
    for i, day in enumerate(market.trade_date):
        record = {"选股日": day, "当日截至14:40两市成交额（亿元）": amount.iloc[i],
                  "此前20日成交额（亿元，远到近）": "", "此前5日成交额（亿元，远到近）": "",
                  "此前20日均额（亿元）": np.nan, "此前20日中位数（亿元）": np.nan,
                  "此前5日均额（亿元）": np.nan, "此前5日递增次数（共4次比较）": np.nan,
                  "当日相对前20日中位数": np.nan, "前5日均额相对前20日中位数": np.nan,
                  "当日在前20日中的百分位": np.nan}
        if i >= 20:
            prior20 = amount.iloc[i-20:i]
            if prior20.notna().all():
                prior5 = prior20.iloc[-5:]
                median = prior20.median()
                record.update({
                    "此前20日成交额（亿元，远到近）": ";".join(f"{v:.2f}" for v in prior20),
                    "此前5日成交额（亿元，远到近）": ";".join(f"{v:.2f}" for v in prior5),
                    "此前20日均额（亿元）": prior20.mean(),
                    "此前20日中位数（亿元）": median,
                    "此前5日均额（亿元）": prior5.mean(),
                    "此前5日递增次数（共4次比较）": int(prior5.diff().iloc[1:].gt(0).sum()),
                })
                if pd.notna(amount.iloc[i]) and median > 0:
                    record.update({
                        "当日相对前20日中位数": amount.iloc[i] / median,
                        "前5日均额相对前20日中位数": prior5.mean() / median,
                        "当日在前20日中的百分位": (
                            prior20.lt(amount.iloc[i]).sum() +
                            prior20.eq(amount.iloc[i]).sum() / 2) / 20,
                    })
        records.append(record)
    return pd.DataFrame(records)


def learned_rise_gate(history: pd.DataFrame, current: pd.DataFrame) -> pd.DataFrame:
    """Learn a one-variable split from only mature earlier Top1 trades."""
    prior = history.copy().sort_values("选股日")
    results = []
    for _, row in current.sort_values("选股日").iterrows():
        date = row["选股日"]
        visible = prior[prior["次交易日"].le(date) & prior["Top1收益率"].notna()].tail(120)
        candidates = []
        for limit in (1, 2, 3):
            low = visible.loc[visible["此前5日递增次数（共4次比较）"].le(limit), "Top1收益率"]
            high = visible.loc[visible["此前5日递增次数（共4次比较）"].gt(limit), "Top1收益率"]
            if len(low) >= 20 and len(high) >= 20:
                candidates.append((low.mean() - high.mean(), limit))
        improvement, limit = max(candidates) if candidates else (np.nan, np.nan)
        trade = (bool(row["此前5日递增次数（共4次比较）"] <= limit)
                 if pd.notna(improvement) and improvement > 0 else True)
        results.append({"选股日": date, "自动学习的递增次数上限": limit,
                        "历史两组均值差（百分点）": improvement * 100,
                        "自动阈值试验标记": "通过" if trade else "未通过"})
        if pd.notna(row["Top1收益率"]):
            prior = pd.concat([prior, pd.DataFrame([{
                "选股日": date, "次交易日": row["次交易日"],
                "此前5日递增次数（共4次比较）": row["此前5日递增次数（共4次比较）"],
                "Top1收益率": row["Top1收益率"],
            }])], ignore_index=True)
    return pd.DataFrame(results)


def main() -> None:
    market_2025 = pd.concat([
        pd.read_csv(ROOT / "20261011_mk_2025_mar_aug_backtest/market_amount_1440.csv"),
        pd.read_csv(ROOT / "20261011_mk_2025_sep_oct_backtest/market_amount_1440.csv"),
    ], ignore_index=True).drop_duplicates("trade_date")
    market_2026_old = pd.read_csv(
        ROOT / "20261011_mk_1m_market_amount_board/experiments/daily_market.csv")
    market_2026_new = pd.read_csv(
        ROOT / "20261011_aug_oct_independent20/market_context.csv")
    market_2026_new.loc[~market_2026_new.amount_valid, "amount_1440_100m_cny"] = np.nan
    features_2025 = market_features(market_2025)
    features_2026 = market_features(pd.concat([
        market_2026_old[["trade_date", "amount_1440_100m_cny"]],
        market_2026_new[["trade_date", "amount_1440_100m_cny"]],
    ], ignore_index=True))
    history_parts = []
    for name in ("mar_aug", "sep_oct"):
        trades = pd.read_csv(ROOT / f"20261011_mk_2025_{name}_backtest/trades.csv")
        trades = trades[trades.selection_rank.eq(1) & trades.policy.eq(POLICY)]
        history_parts.append(trades[["signal_date", "next_date", "actual_0940_return"]])
    history = pd.concat(history_parts, ignore_index=True).rename(columns={
        "signal_date": "选股日", "next_date": "次交易日",
        "actual_0940_return": "Top1收益率"})
    history = history.merge(features_2025[["选股日", "此前5日递增次数（共4次比较）"]],
                            on="选股日", validate="one_to_one")
    early = pd.read_csv(ROOT / "20261011_mk_1m_tradable_st_rolling20/top2_verified.csv",
                        dtype={"symbol6": str})
    late = pd.read_csv(ROOT / "20261011_aug_oct_independent20/top2_verified.csv",
                       dtype={"symbol6": str})
    picks = pd.concat([early, late], ignore_index=True)
    dates = pd.concat([
        pd.read_csv(ROOT / "20261011_mk_1m_tradable_st_rolling20/daily_effect_market_context.csv",
                    usecols=["选股日"]),
        pd.read_csv(ROOT / "20261011_aug_oct_independent20/daily.csv",
                    usecols=["选股日"]),
    ], ignore_index=True)
    if dates.选股日.duplicated().any() or picks.duplicated(["signal_date", "selection_rank"]).any():
        raise ValueError("Duplicate prediction date or pick rank")
    if not set(picks.signal_date).issubset(set(dates.选股日)):
        raise ValueError("A saved pick is absent from the test calendar")
    daily = dates.merge(features_2026, on="选股日", how="left", validate="one_to_one")
    for rank in (1, 2):
        part = picks[picks.selection_rank.eq(rank)].copy().rename(columns={
            "signal_date": "选股日", "next_date": "次交易日", "symbol6": f"Top{rank}代码",
            "name": f"Top{rank}股票", "actual_0940_return": f"Top{rank}收益率",
            "actual_class": f"Top{rank}次晨类别"})
        columns = ["选股日", f"Top{rank}代码", f"Top{rank}股票",
                   f"Top{rank}收益率", f"Top{rank}次晨类别"]
        if rank == 1:
            columns.insert(1, "次交易日")
        daily = daily.merge(part[columns], on="选股日", how="left", validate="one_to_one")
        daily[f"Top{rank}次晨类别"] = daily[f"Top{rank}次晨类别"].map({
            "ge3": "达标", "1to3": "可接受", "0to1": "中性", "lt0": "错误",
            "UNKNOWN": "待验证",
        })
    selected = daily.Top1代码.notna()
    if daily.loc[selected, ["当日截至14:40两市成交额（亿元）",
                            "此前20日均额（亿元）"]].isna().any().any():
        raise ValueError("A selected day lacks its as-of 14:40 market amounts")
    calibration = daily[daily.Top1代码.notna()].copy()
    calibration["Top1收益率"] = pd.to_numeric(calibration["Top1收益率"])
    learned = learned_rise_gate(history, calibration)
    daily = daily.merge(learned, on="选股日", how="left", validate="one_to_one")
    for rank in (1, 2):
        daily[f"Top{rank}收益（%）"] = daily[f"Top{rank}收益率"] * 100
        daily = daily.drop(columns=f"Top{rank}收益率")
    OUT.mkdir(parents=True, exist_ok=True)
    daily.to_csv(OUT / "daily_2026.csv", index=False, float_format="%.6f")
    compact_columns = ["选股日", "Top1代码", "Top1股票", "Top1收益（%）",
                       "当日截至14:40两市成交额（亿元）", "此前20日均额（亿元）",
                       "此前5日均额（亿元）", "此前5日递增次数（共4次比较）",
                       "当日在前20日中的百分位", "自动学习的递增次数上限",
                       "自动阈值试验标记"]
    daily[compact_columns].to_csv(OUT / "daily_compact_2026.csv", index=False,
                                  float_format="%.6f")
    summary = {}
    for period, group in (("2026-02_to_07", daily[daily.选股日.lt("2026-08")]),
                          ("2026-09", daily[daily.选股日.str.startswith("2026-09")])):
        valid = group[group.Top1代码.notna()].copy()
        chosen = valid[valid.自动阈值试验标记.eq("通过")]
        skipped = valid[valid.自动阈值试验标记.eq("未通过")]
        summary[period] = {
            "model_days": len(valid), "trial_passed": len(chosen),
            "trial_not_passed": len(skipped),
            "all_top1_mean_0940_pct": valid["Top1收益（%）"].mean(),
            "passed_top1_mean_0940_pct": chosen["Top1收益（%）"].mean(),
            "not_passed_top1_mean_0940_pct": skipped["Top1收益（%）"].mean(),
        }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False,
                                                  indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
