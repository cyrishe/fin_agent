"""Read-only R53: B2 daily model without four-K selection; last validation month."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import pearsonr

from scripts import experiment_b2_two_month_2024_r35 as b2
from scripts import experiment_limit_rebound_four_minutes_r24 as four
from scripts.replay_limit_rebound_cash_r23 import dump, replay, sha

OUT = Path("docs/stock_automl_runs/20261020_b2_no_shape_last_valid_r53")
R52 = Path("docs/stock_automl_runs/20261020_b2_validation_vs_month_r52")
R35 = b2.OUT
PANEL = b2.PANEL
KEY = ["symbol", "d0_date"]


def label_stats(frame: pd.DataFrame, k: int) -> tuple[int, float | None]:
    q = frame[frame.d0_rank.le(k) & frame.activated.eq(True) &
              frame.observed_3.eq(True)]
    return len(q), float(q.label_3.mean()) if len(q) else None


def cash_summary(frame: pd.DataFrame, month: str, calendar: list[str]) -> dict:
    if frame.empty:
        return dict(n=0, win=None, mean=None, account=0., label=None,
                    final_date=None, ranks={})
    end = max(calendar[calendar.index(day) + 1] for day in frame.d1_date)
    account, _, _, ledger, excluded = replay(frame, calendar,
                                              start=month + "-01", end=end, fee_rate=.001)
    assert not excluded and account["entries"] == len(frame) and account["pending"] == 0
    assert ledger.sell.eq(ledger.d2_close).all()
    np.testing.assert_allclose(ledger.return_, ledger.sell / ledger.buy * .999 / 1.001 - 1,
                               atol=1e-12, rtol=0)
    return dict(n=len(ledger), win=float(ledger.return_.gt(0).mean()),
                mean=float(ledger.return_.mean()), account=account["total_return"],
                label=float(ledger.label_3.mean()), final_date=end,
                ranks={str(int(k)): int(v) for k, v in frame.d0_rank.value_counts().items()})


def no_shape(frame: pd.DataFrame, bars: dict, top: int) -> pd.DataFrame:
    # Minute data locate the same executable-price proxy. No four-K shape or
    # body threshold participates in stock selection.
    picks = four.select(frame, top, False)
    done = four.delay_same_stock(picks, bars)
    assert done.d0_rank.le(top).all() and not done.d1_date.duplicated().any()
    return done


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    panel = pd.read_pickle(PANEL / "panel.pkl")
    calendar = json.loads((PANEL / "calendar.json").read_text())
    enriched = pd.read_pickle(R35 / "enriched.pkl")
    first = pd.read_pickle(R35 / "first10.pkl")
    first["symbol"] = first.symbol.str[:6]
    bars = {(sym, d0): g.sort_values("time") for (sym, d0), g in first.groupby(KEY)}
    original = pd.read_pickle(R35 / "signals.pkl")
    rankings = pd.read_csv(R52 / "31月逐月对照.csv")
    assert len(rankings) == 31 and rankings.月份.is_unique
    sources = [R35 / "enriched.pkl", R35 / "first10.pkl", R35 / "signals.pkl",
               PANEL / "panel.pkl", PANEL / "calendar.json", R52 / "31月逐月对照.csv"]
    rows, trades = [], []
    for record in rankings.itertuples():
        month = record.月份
        prev = str(pd.Period(month) - 1)
        last_score_path = b2.source(2, month, "valid")
        test_score_path = b2.source(2, month, "forecast")
        sources.extend([last_score_path, test_score_path])
        valid_scores = pd.read_pickle(last_score_path)
        valid_scores = valid_scores[valid_scores.d0_date.str.startswith(prev)].merge(
            panel, on=KEY, validate="one_to_one")
        test_scores = pd.read_pickle(test_score_path).merge(panel, on=KEY,
                                                             validate="one_to_one")
        assert test_scores.d0_date.str.startswith(month).all()
        assert all(calendar[int(i) + 4] < month + "-01" for i in valid_scores.date_i)
        valid_n1, valid_hit1 = label_stats(valid_scores, 1)
        valid_n3, valid_hit3 = label_stats(valid_scores, 3)
        test_n1, test_hit1 = label_stats(test_scores, 1)
        test_n3, test_hit3 = label_stats(test_scores, 3)
        valid_minute = enriched[(enriched.model_month == month) &
                                enriched.part.eq("valid") &
                                enriched.d0_date.str.startswith(prev) &
                                enriched.complete.eq(True)].copy()
        assert valid_minute.d0_rank.eq(1).all()
        test_minute = enriched[(enriched.model_month == month) &
                               enriched.part.eq("test")].copy()
        valid_pick = no_shape(valid_minute, bars, 1)
        top1_pick = no_shape(test_minute, bars, 1)
        top3_pick = no_shape(test_minute, bars, 3)
        frozen = original[original.d0_date.str.startswith(month)].copy()
        for name, picks in (("前一验证月无形态Top1", valid_pick),
                            ("测试月无形态Top1", top1_pick),
                            ("测试月无形态Top3", top3_pick),
                            ("测试月原B2", frozen)):
            start_month = prev if name.startswith("前一") else month
            result = cash_summary(picks, start_month, calendar)
            for trade in picks.to_dict("records"):
                trades.append(dict(目标月=month, 方案=name, D0=trade["d0_date"],
                                   D1=trade["d1_date"], 股票=trade["symbol"],
                                   D0排名=trade["d0_rank"], 买价=trade["entry"],
                                   D2收盘=trade["d2_close"],
                                   费后D2收益=trade["d2_close"] / trade["entry"] * .999 / 1.001 - 1))
            if name == "前一验证月无形态Top1":
                v1 = result
            elif name == "测试月无形态Top1":
                t1 = result
            elif name == "测试月无形态Top3":
                t3 = result
            else:
                base = result
        row = dict(测试月=month, 分组=record.分组, B2日历月收益=record.测试月账户收益,
                   紧前验证月=prev,
                   验证末月日线Top1样本=valid_n1, 验证末月日线Top1再板率=valid_hit1,
                   验证末月日线Top3样本=valid_n3, 验证末月日线Top3再板率=valid_hit3,
                   验证末月无形态Top1笔=v1["n"],
                   验证末月无形态Top1胜率=v1["win"],
                   验证末月无形态Top1笔均=v1["mean"],
                   验证末月无形态Top1独立账户=v1["account"],
                   测试月日线Top1再板率=test_hit1, 测试月日线Top1样本=test_n1,
                   测试月日线Top3再板率=test_hit3, 测试月日线Top3样本=test_n3)
        for prefix, result in (("测试无形态Top1", t1), ("测试无形态Top3", t3),
                               ("测试原B2", base)):
            row.update({prefix + "笔": result["n"], prefix + "D2胜率": result["win"],
                        prefix + "D2笔均": result["mean"], prefix + "独立月账户": result["account"]})
        row["无形态Top3减原B2账户"] = t3["account"] - base["account"]
        rows.append(row)
    data = pd.DataFrame(rows).sort_values("B2日历月收益").reset_index(drop=True)
    data.to_csv(OUT / "31月无形态与末月验证.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(trades).to_csv(OUT / "逐笔无形态对照.csv", index=False,
                                encoding="utf-8-sig")
    group_rows = []
    for name in ("最差10月", "中间11月", "最好10月"):
        q = data[data.分组.eq(name)]
        fields = ["B2日历月收益", "验证末月日线Top1再板率", "验证末月日线Top3再板率",
                  "验证末月无形态Top1胜率", "验证末月无形态Top1笔均",
                  "验证末月无形态Top1独立账户", "测试月日线Top1再板率",
                  "测试月日线Top3再板率", "测试无形态Top1独立月账户",
                  "测试无形态Top3独立月账户", "测试原B2独立月账户",
                  "无形态Top3减原B2账户"]
        group_rows.append(dict(分组=name, 月数=len(q), **{x: float(q[x].mean()) for x in fields}))
    pd.DataFrame(group_rows).to_csv(OUT / "三组无形态对照.csv", index=False,
                                     encoding="utf-8-sig")
    correlations = []
    for field in ("验证末月日线Top1再板率", "验证末月日线Top3再板率",
                  "验证末月无形态Top1胜率", "验证末月无形态Top1笔均",
                  "验证末月无形态Top1独立账户"):
        q = data[[field, "B2日历月收益", "测试无形态Top3独立月账户"]].dropna()
        correlations.append(dict(验证指标=field, 月数=len(q),
            与原B2月收益Pearson=float(pearsonr(q[field], q.B2日历月收益).statistic),
            与无形态Top3月收益Pearson=float(pearsonr(q[field], q.测试无形态Top3独立月账户).statistic)))
    pd.DataFrame(correlations).to_csv(OUT / "末月验证相关性.csv", index=False,
                                      encoding="utf-8-sig")
    dump(OUT / "sources.json", [{"file": str(p), "sha256": sha(p)}
                                 for p in sorted(set(sources))])
    dump(OUT / "audit.json", dict(months=len(data), frozen_b2_signals_sha256=sha(R35 / "signals.pkl"),
                                   all_validation_labels_mature_before_test=True,
                                   all_accounts_d2_close_and_fee_verified=True,
                                   no_four_k_shape_or_body_filter=True,
                                   minute_used_only_for_same_entry_window=True,
                                   rows_of_trade_policies=len(trades)))
    print(data[["测试月", "分组", "紧前验证月", "验证末月日线Top1再板率",
                "验证末月日线Top3再板率", "验证末月无形态Top1独立账户",
                "测试无形态Top1独立月账户", "测试无形态Top3独立月账户",
                "测试原B2独立月账户", "B2日历月收益"]].to_string(index=False))
    print("\nGROUPS")
    print(pd.DataFrame(group_rows).to_string(index=False))
    print("\nCORRELATIONS")
    print(pd.DataFrame(correlations).to_string(index=False))


if __name__ == "__main__":
    main()
