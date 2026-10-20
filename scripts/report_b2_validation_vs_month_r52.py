"""Read-only R52: compare frozen B2 two-month validation with next-month outcomes."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from scripts import experiment_limit_rebound_four_minutes_r24 as four
from scripts import experiment_limit_rebound_shape_r19 as shape
from scripts.replay_limit_rebound_cash_r23 import dump, replay, sha

SOURCE = Path("outputs/stock_automl/b2_two_month_2024_r35_20261019")
CALENDAR_SOURCE = Path("outputs/stock_automl/b2_full_2024_r34_20261019/calendar.json")
OUT = Path("docs/stock_automl_runs/20261020_b2_validation_vs_month_r52")
KEY = ["symbol", "d0_date"]


def rate(frame: pd.DataFrame) -> float | None:
    return float(frame.return_.gt(0).mean()) if len(frame) else None


def mean(frame: pd.DataFrame) -> float | None:
    return float(frame.return_.mean()) if len(frame) else None


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    files = [SOURCE / x for x in ("daily_metrics.json", "rules_frozen.json", "monthly.json",
                                   "ledger.pkl", "enriched.pkl", "first10.pkl", "signals.pkl")]
    files.append(CALENDAR_SOURCE)
    dump(OUT / "sources.json", [{"file": str(p), "sha256": sha(p)} for p in files])
    daily = {(x["month"], x["part"]): x for x in
             json.loads((SOURCE / "daily_metrics.json").read_text())
             if x["daily_validation"] == 2}
    rules = {x["month"]: x for x in json.loads((SOURCE / "rules_frozen.json").read_text())}
    monthly = [x for x in json.loads((SOURCE / "monthly.json").read_text())
               if x["scope"] == "完整两月2024起" and x["variant"] == "D2S2"
               and "2024-03" <= x["month"] <= "2026-09"]
    assert len(monthly) == 31
    ledger = pd.read_pickle(SOURCE / "ledger.pkl")
    ledger = ledger[ledger.scope.eq("完整两月2024起") & ledger.variant.eq("D2S2")].copy()
    enriched = pd.read_pickle(SOURCE / "enriched.pkl")
    first = pd.read_pickle(SOURCE / "first10.pkl")
    first["symbol"] = first.symbol.str[:6]
    bars = {(sym, d0): group.sort_values("time") for (sym, d0), group in first.groupby(KEY)}
    calendar = json.loads(CALENDAR_SOURCE.read_text())
    rows, valid_details = [], []
    for month_row in monthly:
        month = month_row["month"]
        w = rules[month]["window"]
        assert w["valid"] == [str(pd.Period(month) - 2), str(pd.Period(month) - 1)]
        valid_score = daily[(month, "valid")]["open"]
        selected = rules[month]["valid_selected"]
        test = ledger[ledger.d0_date.str.startswith(month) & ledger.exit_date.notna()].copy()
        q = enriched[enriched.model_month.eq(month) & enriched.part.eq("valid")].copy()
        assert q.d0_rank.eq(1).all()
        cutoff = month + "-01"
        assert all(calendar[int(i) + 4] < cutoff for i in q.date_i)
        mature = q[q.complete.eq(True)]
        recomputed = shape.metrics(mature, rules[month]["selected"])
        assert recomputed["known"] == selected["known"]
        assert recomputed["hits"] == selected["hits"]
        assert np.isclose(recomputed["ret2"]["mean"], selected["ret2"]["mean"])
        picked = four.chosen(mature, rules[month]["selected"])
        delayed = four.delay_same_stock(picked, bars)
        assert delayed.d0_rank.eq(1).all()
        assert not delayed.d1_date.duplicated().any()
        delayed["return_net"] = delayed.d2_close / delayed.entry * .999 / 1.001 - 1
        if len(delayed):
            last_d2 = max(calendar[calendar.index(day) + 1] for day in delayed.d1_date)
            account, _, _, valid_ledger, excluded = replay(delayed, calendar,
                start=w["valid"][0] + "-01", end=last_d2, fee_rate=.001)
            assert account["entries"] == len(delayed) and account["pending"] == 0 and not excluded
            np.testing.assert_allclose(valid_ledger.return_, delayed.sort_values("d1_date").return_net,
                                       rtol=0, atol=1e-12)
            valid_account_return = account["total_return"]
        else:
            valid_account_return = 0.0
        for r in delayed.itertuples():
            valid_details.append(dict(目标月=month, 验证D0=r.d0_date, 验证D1=r.d1_date,
                                      股票=r.symbol, 买价=r.entry, D2收盘=r.d2_close,
                                      D2费后收益=r.return_net, 三日再板=r.label_3))
        row = dict(
            月份=month, 验证期="—".join(w["valid"]), 四K规则=rules[month]["selected"]["id"],
            日线验证Top1样本=valid_score["top1"]["n"],
            日线验证Top1=valid_score["top1"]["rate"],
            日线验证Top3样本=valid_score["top3"]["n"],
            日线验证Top3=valid_score["top3"]["rate"],
            四K验证样本=selected["known"], 四K验证再板率=selected["hit"],
            四K验证D2毛收益代理=selected["ret2"]["mean"],
            四K验证原选规收益代理=selected["strategy"]["mean"],
            验证期延迟可买笔=len(delayed),
            验证期D2费后胜率=float(delayed.return_net.gt(0).mean()) if len(delayed) else None,
            验证期D2费后笔均=float(delayed.return_net.mean()) if len(delayed) else None,
            验证期独立账户收益=valid_account_return,
            测试月账户收益=month_row["return_"], 测试月最大回撤=month_row["drawdown"],
            测试月账户买入=month_row["entries"],
            测试月D0已卖笔=len(test),
            测试月D2费后胜率=rate(test), 测试月D2费后笔均=mean(test),
            测试月三日再板率=float(test.label_3.mean()) if len(test) else None,
            测试月亏超5比例=float(test.return_.lt(-.05).mean()) if len(test) else None)
        rows.append(row)
    data = pd.DataFrame(rows)
    assert len(data) == 31 and data.月份.is_unique
    ranked = data.sort_values("测试月账户收益", kind="stable").reset_index(drop=True)
    ranked["收益名次_低到高"] = np.arange(1, 32)
    ranked["分组"] = ["最差10月"] * 10 + ["中间11月"] * 11 + ["最好10月"] * 10
    ranked.to_csv(OUT / "31月逐月对照.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(valid_details).to_csv(OUT / "验证期逐笔_延迟买入.csv", index=False,
                                      encoding="utf-8-sig")
    groups = []
    for name in ("最差10月", "中间11月", "最好10月"):
        g = ranked[ranked.分组.eq(name)]
        group = dict(分组=name, 月数=len(g), 测试月收益均值=g.测试月账户收益.mean(),
                     测试月收益中位=g.测试月账户收益.median(),
                     测试月D2胜率月均=g.测试月D2费后胜率.mean(),
                     测试月D2费后笔均月均=g.测试月D2费后笔均.mean(),
                     日线验证Top1月均=g.日线验证Top1.mean(),
                     日线验证Top3月均=g.日线验证Top3.mean(),
                     四K验证再板率月均=g.四K验证再板率.mean(),
                     四K验证D2毛收益代理月均=g.四K验证D2毛收益代理.mean(),
                     四K验证原选规收益代理月均=g.四K验证原选规收益代理.mean(),
                     验证期延迟可买笔合计=int(g.验证期延迟可买笔.sum()),
                     验证期D2费后胜率月均=g.验证期D2费后胜率.mean(),
                     验证期D2费后笔均月均=g.验证期D2费后笔均.mean(),
                     验证期独立账户收益月均=g.验证期独立账户收益.mean())
        groups.append(group)
    pd.DataFrame(groups).to_csv(OUT / "三组对照.csv", index=False, encoding="utf-8-sig")
    correlations = []
    for col in ("日线验证Top1", "日线验证Top3", "四K验证再板率", "四K验证D2毛收益代理",
                "四K验证原选规收益代理", "验证期D2费后胜率", "验证期D2费后笔均",
                "验证期独立账户收益"):
        q = data[[col, "测试月账户收益"]].dropna()
        pear = stats.pearsonr(q[col], q.测试月账户收益)
        spear = stats.spearmanr(q[col], q.测试月账户收益)
        correlations.append(dict(验证指标=col, 月数=len(q), Pearson=float(pear.statistic),
                                 Spearman=float(spear.statistic), Pearson_p=float(pear.pvalue)))
    pd.DataFrame(correlations).to_csv(OUT / "相关性.csv", index=False, encoding="utf-8-sig")
    dump(OUT / "audit.json", dict(months=len(data), validation_records=len(valid_details),
                                   monthly_account_source_sha256=sha(SOURCE / "monthly.json"),
                                   no_month_after_2026_09=True,
                                   validation_labels_matured_before_target=True,
                                   frozen_shape_metrics_recomputed=True,
                                   validation_delay_replayed_with_original_fee_and_account=True))
    print(ranked[["月份", "分组", "测试月账户收益", "日线验证Top1", "四K验证再板率",
                  "四K验证D2毛收益代理", "验证期D2费后胜率", "验证期D2费后笔均",
                  "测试月D2费后胜率", "测试月D2费后笔均"]].to_string(index=False))
    print("\nGROUPS")
    print(pd.DataFrame(groups).to_string(index=False))
    print("\nCORRELATIONS")
    print(pd.DataFrame(correlations).to_string(index=False))


if __name__ == "__main__":
    main()
