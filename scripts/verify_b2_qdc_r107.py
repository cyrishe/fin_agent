"""Check R107's saved counts, source hashes, R106 compatibility and quality sensitivity."""
from __future__ import annotations

import hashlib
import json

import pandas as pd

from scripts import research_b2_qdc_r107 as run
from scripts.research_b2_qdc_r106_sell import event_account
from scripts.research_b2_daily_only_r100 import read_inputs


def main() -> None:
    manifest = json.loads((run.DOC / "manifest.json").read_text())
    annual = pd.read_csv(run.DOC / "yearly.csv")
    monthly = pd.read_csv(run.DOC / "monthly_summary.csv")
    news = pd.read_csv(run.DOC / "news_decisions.csv")
    panel = pd.read_pickle(run.OUT / "risk_sell_news_panel.pkl")
    assert len(monthly) == 56 and monthly.model_month.tolist() == run.MONTHS
    assert (monthly.loc[monthly.paused, ["v2_buys", "combo_buys"]] == 0).all().all()
    assert len(panel) == 318 and int(panel.q75_veto.sum()) == 79
    assert len(news) == 239 and news.complete.all() and int(news.theme_denial_5.sum()) == 2
    assert panel.loc[~panel.q75_veto, "news_known"].all()
    assert panel.loc[panel.d0_date.ge("2018"), "total_mv_yi"].notna().all()
    assert panel.d2_cash_dividend.gt(0).sum() == 1
    assert panel.loc[panel.d2_cash_dividend.gt(0), "d2_cash_dividend"].iloc[0] == .25
    assert int(monthly.loc[monthly.model_month.ge("2018"), "v2_buys"].sum()) == 294
    assert int(monthly.loc[monthly.model_month.ge("2018"), "combo_buys"].sum()) == 221
    files = {"yearly.csv": "yearly_sha256", "monthly.csv": "monthly_sha256",
             "monthly_summary.csv": "monthly_summary_sha256", "trades.csv": "trades_sha256",
             "news_decisions.csv": "news_decisions_sha256",
             "news_denial_evidence.json": "news_denial_evidence_sha256",
             "raw_news_hashes.csv": "raw_news_hashes_sha256"}
    for name, key in files.items():
        assert hashlib.sha256((run.DOC / name).read_bytes()).hexdigest() == manifest[key]
    raw_hashes = pd.read_csv(run.DOC / "raw_news_hashes.csv")
    assert int(raw_hashes.path.str.startswith("news_windows/").sum()) == 239
    assert raw_hashes.path.str.startswith("news_bodies/").sum() > 0
    for row in raw_hashes.itertuples(index=False):
        path = run.OUT / row.path
        assert path.stat().st_size == row.bytes
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row.sha256
    _, calendar, _ = read_inputs()
    older = pd.read_pickle(run.r104.ROOT / "outputs/stock_automl/b2_qdc_r106_20261024/risk_sell_panel.pkl")
    old_combo = older[~(older.q75_veto | older.news_veto)]
    old_return = event_account(old_combo, calendar, "morning7", "2022-01-01",
                               old_combo.d2_date.max())[0]["return_pct"]
    old_table = pd.read_csv(run.r104.ROOT / "outputs/stock_automl/b2_qdc_r106_20261024/account_both_sell_rules.csv")
    saved_return = old_table[(old_table.rule == "Q75+news") & (old_table.sell == "morning7") &
                             (old_table.period == "2022-26_continuous")].return_pct.iloc[0]
    assert abs(old_return - saved_return) < 1e-9
    sensitivity = {}
    for year in (2017, 2018, 2019, 2020, 2021):
        group = panel[(panel.model_month.str[:4] == str(year)) &
                      ~(panel.q75_veto | panel.news_veto)]
        clean = group[group.d0_ohlc_match & group.d2_ohlc_match]
        result = event_account(clean, calendar, "morning7", f"{year}-01-01",
                               clean.d2_date.max())[0]
        sensitivity[str(year)] = dict(combo_trades=len(group), excluded=len(group) - len(clean),
                                      clean_morning7_pct=result["return_pct"])
    expected = annual[(annual.rule == "Q75+news") &
                      (annual.period == "2018-21_continuous")]
    assert set(expected.sell) == {"close", "morning7"}
    verification = dict(months=len(monthly), base_trades=len(panel),
                        combo_trades_2018_21=int(monthly.loc[monthly.model_month.ge("2018"), "combo_buys"].sum()),
                        news_complete=int(news.complete.sum()), news_denials=int(news.theme_denial_5.sum()),
                        d0_ohlc_mismatch=int((~panel.d0_ohlc_match).sum()),
                        d2_ohlc_mismatch=int((~panel.d2_ohlc_match).sum()),
                        raw_news_hashes_checked=len(raw_hashes),
                        r106_existing_morning7_pct=old_return,
                        quality_sensitivity=sensitivity)
    (run.DOC / "verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(verification, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
