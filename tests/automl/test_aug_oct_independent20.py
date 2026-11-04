from pathlib import Path

import pandas as pd

from scripts.benchmark_automl_1440_inference import FEATURES


ROOT = Path("docs/stock_automl_runs/20261011_aug_oct_independent20")


def test_independent_pool_keeps_unlabeled_candidates_and_seven_factors():
    rows = pd.read_csv(ROOT / "candidates.csv.gz", dtype={"symbol6": str})
    assert len(rows) == 15696
    assert rows.signal_date.min() == "2026-08-05"
    assert rows.signal_date.max() == "2026-10-09"
    assert rows.signal_date.nunique() == 42
    assert rows[list(FEATURES)].notna().all().all()
    assert rows.signal_return.between(.03, .06, inclusive="both").all()
    assert not (rows.st_type.isin(("S", "Y")) & rows.entry_at_limit).any()
    assert rows.loc[rows.signal_date.lt("2026-10-09"), "second_high"].isna().any()


def test_prediction_starts_after_20_contiguous_complete_trading_days():
    calendar = pd.read_csv(ROOT / "market_context.csv").trade_date.tolist()
    folds = pd.read_csv(ROOT / "folds.csv").set_index("选股日")
    assert len(calendar) == 44
    assert not folds.loc[folds.index < "2026-09-02", "入选数"].any()
    assert folds.loc["2026-09-02", "窗口起始日"] == "2026-08-05"
    assert folds.loc["2026-09-02", "窗口截止日"] == "2026-09-01"
    assert folds.loc["2026-09-02", "训练日数"] == 20
    assert folds.loc["2026-09-02", "窗口交易日数"] == 20
    assert folds.loc["2026-09-02", "缺失训练日数"] == 0
    valid = folds[folds.入选数.gt(0)]
    assert len(valid) == 22
    assert valid.训练日数.eq(20).all()
    assert valid.缺失训练日数.eq(0).all()
    assert calendar[calendar.index("2026-09-02")-20:calendar.index("2026-09-02")][0] == "2026-08-05"


def test_daily_monthly_and_verified_picks_reconcile():
    daily = pd.read_csv(ROOT / "daily.csv", dtype={"Top1股票": str,
                                                  "Top2股票": str})
    monthly = pd.read_csv(ROOT / "monthly.csv")
    picks = pd.read_csv(ROOT / "top2_verified.csv", dtype={"symbol6": str})
    assert len(daily) == 44
    assert len(picks[picks.selection_rank.eq(1)]) == 22
    assert daily.loc[daily.选股日.lt("2026-09-02"), "Top1股票"].isna().all()
    assert daily.loc[daily.选股日.eq("2026-10-09"),
                     "Top1 09:40收益（%）"].isna().all()
    for _, month in monthly.iterrows():
        frame = daily[daily.选股日.str[:7].eq(month["月份"])]
        returns = frame["Top1 09:40收益（%）"].dropna()
        assert len(returns) == month["Top1已验证笔数"]
        assert abs(returns.sum() - month["Top1收益简单求和（%）"]) < 1e-5
        if len(returns):
            assert abs(returns.mean() - month["Top1平均收益（%）"]) < 1e-5
    september = monthly[monthly.月份.eq("2026-09")].iloc[0]
    assert september["Top1已验证笔数"] == 20
    assert abs(september["Top1平均收益（%）"] - .984472) < 1e-6
