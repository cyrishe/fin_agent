"""Frozen Q75 and announcement reverse-transport diagnostic for R108."""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from scripts import research_b2_qdc_r104 as r104
from scripts import research_b2_qdc_r106 as r106
from scripts import research_b2_qdc_r106_sell as sell
from scripts import research_b2_qdc_r107 as r107
from scripts import research_b2_qdc_r107_news as news
from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.replay_limit_rebound_cash_r23 import dump, sha
from scripts.research_b2_qdc_r108 import OUT, ROOT, r90

DOC = ROOT / "docs/stock_automl_runs/20261024_b2_qdc_r108"
MONTHS = r90.TEST
r104.OUT = OUT
r106.OUT = OUT
news.run.OUT = OUT
news.run.DOC = DOC


def risk() -> None:
    DOC.mkdir(parents=True, exist_ok=True)
    gates = pd.read_csv(OUT / "gates.csv")
    paused = set(gates.loc[gates.paused, "model_month"])
    signals = pd.read_pickle(OUT / "signals.pkl")
    frame = signals[~signals.model_month.isin(paused)].copy()
    frame = frame.sort_values(["d0_date", "stk_code"]).reset_index(drop=True)
    # The R104 signal carries the original D0 panel fields, whereas R106
    # attaches those fields itself. Avoid suffixing the authoritative columns.
    frame = frame.drop(columns=["d0_turnover_pct", "board_streak_days",
                                "d0_normal_volume_ratio", "d0_cap"], errors="ignore")
    raw_d0 = r106.d0_minutes(frame)
    early = pd.read_pickle(OUT / "qdc_candidate_first10_normalized.pkl")
    basic = pd.read_pickle(OUT / "tushare_basic_all.pkl")
    basic["trade_date"] = pd.to_datetime(basic.trade_date, format="%Y%m%d").dt.strftime("%Y-%m-%d")
    values = frame[["stk_code", "d0_date", "qdc_d0_close"]].merge(
        basic[["ts_code", "trade_date", "total_share"]],
        left_on=["stk_code", "d0_date"], right_on=["ts_code", "trade_date"],
        how="left", validate="one_to_one")
    values["total_mv"] = values.total_share * 10_000 * values.qdc_d0_close
    assert values.total_mv.notna().all()
    values["total_share"] *= 10_000
    values["requested_d0"] = values.d0_date
    values = values[["stk_code", "trade_date", "total_share", "total_mv", "requested_d0"]]
    values.to_csv(OUT / "d0_valuation_tushare.csv", index=False)
    panel = r106.features(frame, raw_d0, early, values, base_panel=OUT / "panel.pkl")
    panel["mv_source"] = "Tushare D0 total_share x QDC D0 close"
    panel = r107.q75_score(panel)
    panel.to_pickle(OUT / "risk_panel.pkl")
    raw_d2 = sell.extract_d2(panel)
    panel = sell.add_sell(panel, raw_d2)
    panel["d2_cash_dividend"] = 0.0
    held = panel[~np.isclose(panel.d1_close, panel.d2_preclose, atol=.011, rtol=0)]
    if len(held):
        keys = list(zip(held.stk_code, held.d2_date))
        rows = []
        with db_connection() as conn:
            for i in range(0, len(keys), 50):
                batch = keys[i:i + 50]
                marks = ",".join(["(%s,%s)"] * len(batch))
                records = query(conn,
                    "SELECT stk_code,record_date,ex_date,div_cash_pre_tax,div_cash_after_tax,"
                    "div_conversed_rate,div_bonus_rate,div_payout_date,div_progress "
                    f"FROM kcrp_stock_dividend WHERE (stk_code,ex_date) IN ({marks})",
                    tuple(v for pair in batch for v in pair))
                rows.extend(records.to_dict("records"))
            conn.rollback()
        actions = pd.DataFrame(rows)
        actions.to_csv(DOC / "corporate_actions.csv", index=False)
        if len(actions):
            actions["ex_date"] = actions.ex_date.astype(str)
            match = held[["stk_code", "d0_date", "d1_date", "d2_date", "d1_close", "d2_preclose"]].merge(
                actions, left_on=["stk_code", "d2_date"], right_on=["stk_code", "ex_date"],
                how="left", validate="many_to_one")
            match["valid_cash"] = (match.record_date.astype(str).eq(match.d1_date) &
                match.div_bonus_rate.isna() & match.div_conversed_rate.isna() &
                np.isclose(match.d1_close - match.d2_preclose,
                           match.div_cash_pre_tax, atol=.011, rtol=0))
            assert match.valid_cash.all(), match.to_dict("records")[:5]
            lookup = match.set_index(["stk_code", "d0_date"]).div_cash_pre_tax.to_dict()
            panel["d2_cash_dividend"] = [lookup.get((r.stk_code, r.d0_date), 0.0)
                                          for r in panel.itertuples()]
    else:
        pd.DataFrame(columns=["stk_code", "ex_date", "div_cash_pre_tax"]).to_csv(
            DOC / "corporate_actions.csv", index=False)
    panel.to_pickle(OUT / "risk_sell_panel.pkl")
    print(dict(v2=len(panel), q75_veto=int(panel.q75_veto.sum()),
               d0_ohlc_mismatch=int((~panel.d0_ohlc_match).sum()),
               d2_ohlc_mismatch=int((~panel.d2_ohlc_match).sum()),
               d2_floor=int(panel.close_floor.sum()), actions=len(held)), flush=True)


def announcements() -> None:
    (OUT / "news_windows").mkdir(parents=True, exist_ok=True)
    (OUT / "news_bodies").mkdir(parents=True, exist_ok=True)
    panel = pd.read_pickle(OUT / "risk_sell_panel.pkl")
    retained = panel[~panel.q75_veto][["stk_code", "d0_date", "d1_date"]].to_dict("records")
    stockmap = json.loads((news.SOURCE / "cninfo_stockmap.json").read_text())
    mapping = {row["code"]: row for row in stockmap["stockList"]}
    calendar = json.loads((OUT / "calendar.json").read_text())
    assert calendar.index(min(row["d0_date"] for row in retained)) >= 5
    results = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(news.fetch_window, row, mapping, calendar)
                   for row in retained if row["stk_code"][:6] in mapping]
        results += [dict(**row, complete=False, theme_denial_5=None,
                         error="stock absent from CNINFO map")
                    for row in retained if row["stk_code"][:6] not in mapping]
        for future in as_completed(futures):
            results.append(future.result())
            if len(results) % 20 == 0:
                print("news", len(results), "/", len(retained), flush=True)
    summary = pd.DataFrame([{k: row.get(k) for k in
                             ["stk_code", "d0_date", "d1_date", "start5", "complete",
                              "total_documents", "theme_denial_5", "error"]}
                            for row in results])
    assert not summary.duplicated(["stk_code", "d0_date"]).any()
    summary.to_csv(DOC / "news_decisions.csv", index=False)
    denials = [dict(stk_code=row["stk_code"], d0_date=row["d0_date"],
                    documents=[doc for doc in row.get("documents", []) if doc.get("evidence")])
               for row in results if row.get("theme_denial_5") == 1]
    dump(DOC / "news_denial_evidence.json", denials)
    panel = panel.merge(summary[["stk_code", "d0_date", "complete", "theme_denial_5"]]
                        .rename(columns={"complete": "news_complete"}),
                        on=["stk_code", "d0_date"], how="left", validate="one_to_one")
    panel["news_known"] = panel.news_complete.eq(True)
    panel["news_veto"] = panel.theme_denial_5.eq(1)
    panel.to_pickle(OUT / "risk_sell_news_panel.pkl")
    print(dict(retained=len(retained), complete=int(summary.complete.sum()),
               denied=int(summary.theme_denial_5.fillna(0).sum())), flush=True)


def report() -> None:
    panel = pd.read_pickle(OUT / "risk_sell_news_panel.pkl")
    calendar = json.loads((OUT / "calendar.json").read_text())
    gates = pd.read_csv(OUT / "gates.csv")
    yearly, monthly = [], []
    masks = {
        "V2": pd.Series(True, index=panel.index),
        "Q75": ~panel.q75_veto,
        "Q75+news_unknown_allowed": ~(panel.q75_veto | panel.news_veto),
        "Q75+news_known_only": ~(panel.q75_veto | panel.news_veto) & panel.news_known,
    }
    for rule, mask in masks.items():
        subset = panel[mask]
        for mode in ("close", "morning7"):
            for year in (2014, 2015, 2016):
                part = subset[subset.model_month.str.startswith(str(year))]
                if not len(part):
                    continue
                result, ledger, nav = sell.event_account(part, calendar, mode,
                                                         f"{year}-01-01", part.d2_date.max())
                yearly.append(dict(rule=rule, sell=mode, period=str(year), **result,
                                   floor_close=int(part.close_floor.sum()),
                                   unverified_exit=int((part.close_unverified if mode == "close"
                                                        else part.seven_unverified).sum())))
            if len(subset):
                result, ledger, nav = sell.event_account(subset, calendar, mode,
                                                         "2014-01-01", subset.d2_date.max())
                yearly.append(dict(rule=rule, sell=mode, period="2014-16_continuous", **result,
                                   floor_close=int(subset.close_floor.sum()),
                                   unverified_exit=int((subset.close_unverified if mode == "close"
                                                        else subset.seven_unverified).sum())))
                ledger.to_csv(OUT / f"ledger_{rule}_{mode}.csv", index=False)
                nav.to_csv(OUT / f"nav_{rule}_{mode}.csv", index=False)
            for month in MONTHS:
                part = subset[subset.model_month.eq(month)]
                result = (sell.event_account(part, calendar, mode, f"{month}-01",
                                             part.d2_date.max())[0] if len(part)
                          else dict(buys=0, return_pct=0.0, drawdown_pct=0.0,
                                    final_nav=1_000_000.0))
                monthly.append(dict(model_month=month, rule=rule, sell=mode, **result,
                                    floor_close=int(part.close_floor.sum()),
                                    unverified_exit=int((part.close_unverified if mode == "close"
                                                         else part.seven_unverified).sum())))
    annual = pd.DataFrame(yearly)
    month = pd.DataFrame(monthly).merge(gates[["model_month", "paused", "prior_candidates",
                                               "prior_r5", "prior_r10"]], on="model_month",
                                         validate="many_to_one")
    annual.to_csv(DOC / "yearly.csv", index=False)
    month.to_csv(DOC / "monthly.csv", index=False)
    compact = []
    for model_month in MONTHS:
        block = month[month.model_month.eq(model_month)]
        trades = panel[panel.model_month.eq(model_month)]
        def metric(rule: str, sell_mode: str, column: str):
            return block[(block.rule == rule) & (block.sell == sell_mode)][column].iloc[0]
        compact.append(dict(model_month=model_month,
            paused=bool(block.paused.iloc[0]),
            v2_buys=int(metric("V2", "close", "buys")),
            q75_veto=int(trades.q75_veto.sum()),
            news_veto_after_q75=int((trades.news_veto & ~trades.q75_veto).sum()),
            unknown_news_after_q75=int((~trades.news_known & ~trades.q75_veto).sum()),
            combo_buys_unknown_allowed=int(metric("Q75+news_unknown_allowed", "close", "buys")),
            combo_buys_known_only=int(metric("Q75+news_known_only", "close", "buys")),
            combo_close_pct=float(metric("Q75+news_unknown_allowed", "close", "return_pct")),
            combo_morning7_pct=float(metric("Q75+news_unknown_allowed", "morning7", "return_pct")),
            combo_floor_close=int(metric("Q75+news_unknown_allowed", "close", "floor_close"))))
    pd.DataFrame(compact).to_csv(DOC / "monthly_summary.csv", index=False)
    cols = ["stk_code", "model_month", "d0_date", "d1_date", "d2_date", "entry",
            "qdc_d2_close", "sell7", "q75_score", "q75_veto", "news_known",
            "news_veto", "total_mv_yi", "d0_turnover_pct", "board_streak_days",
            "d2_cash_dividend", "close_floor", "close_unverified", "seven_unverified",
            "d0_ohlc_match", "d2_ohlc_match"]
    panel[cols].to_csv(DOC / "trades.csv", index=False)
    history = []
    periods = [
        (DOC / "yearly.csv", "Q75+news_unknown_allowed", "R108", 2014, 2016),
        (ROOT / "docs/stock_automl_runs/20261024_b2_qdc_r107/yearly.csv",
         "Q75+news", "R107", 2017, 2021),
        (ROOT / "docs/stock_automl_runs/20261024_b2_qdc_r106/account_both_sell_rules.csv",
         "Q75+news", "R106", 2022, 2026),
    ]
    for path, strategy, source, start, end in periods:
        source_rows = pd.read_csv(path)
        for year in range(start, end + 1):
            group = source_rows[source_rows.rule.eq(strategy) & source_rows.period.eq(str(year))]
            close = group[group.sell.eq("close")].iloc[0]
            morning = group[group.sell.eq("morning7")].iloc[0]
            assert close.buys == morning.buys
            history.append(dict(year=year, source=source,
                                partial_year=year in (2014, 2017, 2022, 2026),
                                buys=int(close.buys), close_return_pct=close.return_pct,
                                morning7_return_pct=morning.return_pct,
                                morning7_drawdown_pct=morning.drawdown_pct,
                                floor_close=int(close.floor_close) if "floor_close" in close else None))
    pd.DataFrame(history).to_csv(DOC / "historic_stability_2014_2026.csv", index=False)
    print(annual.to_string(index=False), flush=True)
    dump(DOC / "manifest.json", dict(signals=len(panel), q75_veto=int(panel.q75_veto.sum()),
         news_known=int(panel.news_known.sum()), news_unknown=int((~panel.news_known & ~panel.q75_veto).sum()),
         news_veto=int(panel.news_veto.sum()), q75_model_sha256=sha(r106.RISK / "union_early_lr01.joblib"),
         qdc_manifest_sha256=sha(OUT / "qdc_manifest.json"),
         panel_sha256=sha(OUT / "panel.pkl"), rules_sha256=sha(OUT / "rules_frozen.json"),
         yearly_sha256=sha(DOC / "yearly.csv"), monthly_sha256=sha(DOC / "monthly.csv"),
         monthly_summary_sha256=sha(DOC / "monthly_summary.csv"),
         trades_sha256=sha(DOC / "trades.csv"),
         historic_stability_sha256=sha(DOC / "historic_stability_2014_2026.csv")))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["risk", "announcements", "report"])
    globals()[parser.parse_args().stage]()
