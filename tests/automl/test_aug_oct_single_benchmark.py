from pathlib import Path

import pandas as pd


ROOT = Path("docs/stock_automl_runs/20261011_aug_oct_single_benchmark")


def test_daily_shows_all_model_picks_without_market_amount_gate():
    daily = pd.read_csv(ROOT / "daily.csv", dtype={"Top1股票": str,
                                                         "Top2股票": str})
    assert len(daily) == 44
    assert "是否交易" not in daily and "备注" in daily
    assert daily["Top1股票"].count() == 42
    assert daily["Top1 09:40收益（%）"].count() == 41
    assert daily.loc[daily["选股日"].eq("2026-08-05"), "Top1股票"].notna().all()
    assert daily.loc[daily["选股日"].eq("2026-08-05"),
                     "前五日平均成交额（亿元）"].isna().all()
    assert daily.loc[daily["选股日"].eq("2026-10-09"),
                     "Top1结果"].iloc[0] == "待验证"


def test_monthly_reconciles_with_daily_and_repaired_market():
    daily = pd.read_csv(ROOT / "daily.csv")
    monthly = pd.read_csv(ROOT / "monthly.csv")
    market = pd.read_csv(ROOT / "market_context.csv")
    assert monthly["选股日数"].sum() == len(daily)
    assert monthly["Top1入选笔数"].sum() == daily["Top1股票"].count()
    assert monthly["Top1已验证笔数"].sum() == daily["Top1 09:40收益（%）"].count()
    assert abs(monthly["Top1收益简单求和（%）"].sum() -
               daily["Top1 09:40收益（%）"].sum()) < 1e-5
    repaired = market.set_index("trade_date").loc[["2026-09-14", "2026-09-15",
                                                    "2026-09-16"]]
    assert repaired.amount_valid.all()
    assert repaired.symbols_at_1440.ge(5200).all()
