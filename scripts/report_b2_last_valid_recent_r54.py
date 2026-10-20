"""Read-only B2 last-validation-month shape and preceding realized 5/10-day returns."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts import experiment_limit_rebound_shape_r19 as shape
from scripts.replay_limit_rebound_cash_r23 import dump, sha

BASE = Path("outputs/stock_automl/b2_two_month_2024_r35_20261019")
R52 = Path("docs/stock_automl_runs/20261020_b2_validation_vs_month_r52")
R53 = Path("docs/stock_automl_runs/20261020_b2_no_shape_last_valid_r53")
OUT = Path("docs/stock_automl_runs/20261020_b2_last_valid_recent_r54")


def rate(value: float | None) -> str:
    return "—" if value is None or pd.isna(value) else f"{value:.1%}"


def ret(value: float | None) -> str:
    return "—" if value is None or pd.isna(value) else f"{value:+.2%}"


def trade(win: float | None, mean: float | None, count: int) -> str:
    return "—（0）" if count == 0 else f"{rate(win)}／{ret(mean)}（{count}）"


def actual_window(nav: pd.DataFrame, trades: pd.DataFrame, month: str, n: int) -> dict:
    month_days = nav.index[nav.index.str.startswith(month)].tolist()
    if not month_days:
        return dict(available=False, days=0, account=None, closed=0, win=None, mean=None)
    days = month_days[-n:]
    previous = nav.index[nav.index.get_loc(days[0]) - 1] if nav.index.get_loc(days[0]) else None
    if previous is None:
        return dict(available=False, days=len(days), account=None, closed=0, win=None, mean=None)
    closed = trades[trades.exit_date.isin(days)]
    account_return = float(nav.loc[days[-1], "nav"] / nav.loc[previous, "nav"] - 1)
    np.testing.assert_allclose(account_return, np.prod(1 + nav.loc[days, "return_"]) - 1,
                               rtol=0, atol=1e-12)
    return dict(available=True, days=len(days),
                account=account_return,
                closed=len(closed), win=float(closed.return_.gt(0).mean()) if len(closed) else None,
                mean=float(closed.return_.mean()) if len(closed) else None)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    inputs = [BASE / "enriched.pkl", BASE / "rules_frozen.json", BASE / "nav.pkl",
              BASE / "ledger.pkl", R52 / "验证期逐笔_延迟买入.csv",
              R53 / "31月简表_原口径加末月及无四K.csv", R52 / "31月逐月对照.csv"]
    enriched = pd.read_pickle(inputs[0])
    rules = {item["month"]: item for item in json.loads(inputs[1].read_text())}
    nav = pd.read_pickle(inputs[2])
    nav = nav[nav.scope.eq("完整两月2024起") & nav.variant.eq("D2S2")].set_index("date").sort_index()
    assert nav.index.is_unique and nav.index.is_monotonic_increasing
    ledger = pd.read_pickle(inputs[3])
    ledger = ledger[ledger.scope.eq("完整两月2024起") & ledger.variant.eq("D2S2") &
                    ledger.exit_date.notna()].copy()
    validation_trades = pd.read_csv(inputs[4])
    compact = pd.read_csv(inputs[5])
    two_month = pd.read_csv(inputs[6]).set_index("月份")
    assert len(compact) == 31 and compact.测试月.is_unique
    rows = []
    for item in compact.itertuples():
        month = item.测试月
        prior = str(pd.Period(month) - 1)
        frame = enriched[enriched.model_month.eq(month) & enriched.part.eq("valid") &
                         enriched.d0_date.str.startswith(prior) & enriched.complete.eq(True)]
        assert frame.d0_rank.eq(1).all()
        selected = shape.metrics(frame, rules[month]["selected"])
        full_validation = validation_trades[validation_trades.目标月.eq(month)]
        full_frame = enriched[enriched.model_month.eq(month) & enriched.part.eq("valid") &
                              enriched.complete.eq(True)]
        full_stats = shape.metrics(full_frame, rules[month]["selected"])
        assert full_stats["known"] == two_month.loc[month, "四K验证样本"]
        np.testing.assert_allclose(full_stats["hit"], two_month.loc[month, "四K验证再板率"],
                                   rtol=0, atol=1e-12)
        assert len(full_validation) == two_month.loc[month, "验证期延迟可买笔"]
        recent_validation = full_validation[full_validation.验证D0.str.startswith(prior)]
        assert len(recent_validation) <= selected["known"]
        val_n = len(recent_validation)
        val_win = float(recent_validation.D2费后收益.gt(0).mean()) if val_n else None
        val_mean = float(recent_validation.D2费后收益.mean()) if val_n else None
        actual5 = actual_window(nav, ledger, prior, 5)
        actual10 = actual_window(nav, ledger, prior, 10)
        rows.append(dict(测试月=month, 验证末月=prior, 四K末月再板样本=selected["known"],
                         四K末月再板率=selected["hit"], 四K末月实际可买笔=val_n,
                         四K末月D2费后胜率=val_win, 四K末月D2费后笔均=val_mean,
                         末5日账户涨跌幅=actual5["account"], 末5日已卖笔=actual5["closed"],
                         末5日已卖胜率=actual5["win"], 末5日已卖笔均=actual5["mean"],
                         末10日账户涨跌幅=actual10["account"], 末10日已卖笔=actual10["closed"],
                         末10日已卖胜率=actual10["win"], 末10日已卖笔均=actual10["mean"]))
    raw = pd.DataFrame(rows)
    raw.to_csv(OUT / "31月末月四K与实际末5末10_数值.csv", index=False, encoding="utf-8-sig")
    joined = compact.merge(raw, on="测试月", validate="one_to_one")
    joined["末月四 K 再板率"] = [f"{rate(p)}（{n}）" for p, n in
                                zip(joined.四K末月再板率, joined.四K末月再板样本)]
    joined["末月四 K D2 费后胜率／笔均"] = [trade(w, m, int(n)) for w, m, n in
                                         zip(joined.四K末月D2费后胜率, joined.四K末月D2费后笔均,
                                             joined.四K末月实际可买笔)]
    for n in (5, 10):
        joined[f"验证末月实际末{n}日 账户涨跌幅／D2胜率"] = [
            "—" if pd.isna(account) else f"{ret(account)}／{rate(win)}（{int(count)}）"
            for account, win, count in zip(joined[f"末{n}日账户涨跌幅"],
                                           joined[f"末{n}日已卖胜率"], joined[f"末{n}日已卖笔"])]
    table = joined[list(compact.columns) + ["末月四 K 再板率", "末月四 K D2 费后胜率／笔均",
                                            "验证末月实际末5日 账户涨跌幅／D2胜率",
                                            "验证末月实际末10日 账户涨跌幅／D2胜率"]]
    table.to_csv(OUT / "31月简表_加末月四K及末5末10.csv", index=False, encoding="utf-8-sig")
    assert len(raw) == len(table) == 31
    assert raw.loc[raw.测试月.eq("2024-03"), "末5日账户涨跌幅"].isna().all()
    assert np.isfinite(raw.loc[~raw.测试月.eq("2024-03"), ["末5日账户涨跌幅",
                                                       "末10日账户涨跌幅"]].to_numpy()).all()
    dump(OUT / "sources.json", [{"file": str(path), "sha256": sha(path)} for path in inputs])
    dump(OUT / "audit.json", dict(months=len(raw), last_month_shape_uses_target_model_validation=True,
                                   recent_days_use_prior_month_active_b2=True,
                                   recent_account_returns_from_frozen_nav=True,
                                   recent_wins_from_trades_exited_in_window=True,
                                   pre_2024_03_active_b2_unavailable=True))
    print(table[table.测试月.isin(["2024-04", "2024-08", "2024-10", "2025-06", "2026-09"])][
        ["测试月", "末月四 K 再板率", "末月四 K D2 费后胜率／笔均",
         "验证末月实际末5日 账户涨跌幅／D2胜率", "验证末月实际末10日 账户涨跌幅／D2胜率"]].to_string(index=False))


if __name__ == "__main__":
    main()
