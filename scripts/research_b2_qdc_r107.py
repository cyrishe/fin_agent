"""Backcast frozen R86 Q75 on 2017-21 QDC B2 trades, without refitting.

This is a reverse-transport diagnostic: Q75 was fitted on 2022-24 data and
must not be interpreted as a point-in-time early-history strategy.
"""
from __future__ import annotations

import json

import joblib
import pandas as pd

from scripts import research_b2_qdc_r104 as r104
from scripts import research_b2_qdc_r105 as r105
from scripts import research_b2_qdc_r106 as r106
from scripts import research_b2_qdc_r106_sell as sell
from scripts.research_b2_daily_only_r100 import read_inputs

OUT = r104.ROOT / "outputs/stock_automl/b2_qdc_r107_20261024"
DOC = r104.ROOT / "docs/stock_automl_runs/20261024_b2_qdc_r107"
MONTHS = [str(x) for x in pd.period_range("2017-05", "2021-12", freq="M")]


def q75_score(frame: pd.DataFrame) -> pd.DataFrame:
    selection = json.loads((r106.RISK / "selection_frozen.json").read_text())
    rule = next(x["rule"] for x in selection["candidates"] if x["id"] == "union_early_lr01")
    assert r106.digest(r106.RISK / rule["file"]) == selection["models"]["union_early_lr01"]["sha256"]
    model = joblib.load(r106.RISK / rule["file"])
    result = frame.copy()
    result["q75_score"] = model.predict_proba(result[rule["columns"]])[:, 1]
    result["q75_veto"] = result.q75_score.gt(rule["threshold"])
    return result


def yearly_and_monthly(frame: pd.DataFrame, gates: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    _, calendar, _ = read_inputs()
    yearly = []
    monthly = []
    rules = [("V2", pd.Series(True, index=frame.index)), ("Q75", ~frame.q75_veto)]
    if "news_veto" in frame:
        rules.append(("Q75+news", ~(frame.q75_veto | frame.news_veto)))
    for rule, mask in rules:
        picks = frame[mask]
        for mode in ["close", "morning7"]:
            for year in range(2017, 2022):
                part = picks[picks.model_month.str[:4].eq(str(year))]
                if part.empty:
                    continue
                result, ledger, nav = sell.event_account(
                    part, calendar, mode, f"{year}-01-01", part.d2_date.max())
                yearly.append(dict(rule=rule, sell=mode, period=str(year), **result,
                                   floor_close=int(part.close_floor.sum()),
                                   unverified_exit=int((part.close_unverified if mode == "close"
                                                        else part.seven_unverified).sum())))
            for start_year in (2017, 2018):
                part = picks[picks.model_month.ge(f"{start_year}-01") &
                             picks.model_month.le(MONTHS[-1])]
                result, ledger, nav = sell.event_account(
                    part, calendar, mode, f"{start_year}-01-01", part.d2_date.max())
                yearly.append(dict(rule=rule, sell=mode,
                                   period=f"{start_year}-21_continuous", **result,
                                   floor_close=int(part.close_floor.sum()),
                                   unverified_exit=int((part.close_unverified if mode == "close"
                                                        else part.seven_unverified).sum())))
                ledger.to_csv(OUT / f"ledger_{rule}_{mode}_{start_year}.csv", index=False)
                nav.to_csv(OUT / f"nav_{rule}_{mode}_{start_year}.csv", index=False)
            for month in MONTHS:
                cohort = picks[picks.model_month.eq(month)]
                if cohort.empty:
                    ret = dict(buys=0, return_pct=0.0, drawdown_pct=0.0,
                               final_nav=1_000_000.0)
                else:
                    ret, _, _ = sell.event_account(
                        cohort, calendar, mode, f"{month}-01", cohort.d2_date.max())
                monthly.append(dict(model_month=month, rule=rule, sell=mode, **ret,
                                    floor_close=int(cohort.close_floor.sum()),
                                    unverified_exit=int((cohort.close_unverified if mode == "close"
                                                         else cohort.seven_unverified).sum())))
    annual = pd.DataFrame(yearly)
    month = pd.DataFrame(monthly)
    gatecols = gates[gates.model_month.isin(MONTHS)][["model_month", "paused", "prior_candidates",
                                                       "prior_r5", "prior_r10"]]
    month = month.merge(gatecols, on="model_month", validate="many_to_one")
    return annual, month


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    DOC.mkdir(parents=True, exist_ok=True)
    r106.OUT = OUT
    signals = pd.read_pickle(r105.OUT / "signals.pkl")
    gates = pd.read_csv(r105.OUT / "gates.csv")
    paused = set(gates.loc[gates.paused, "model_month"])
    frame = signals[signals.model_month.isin(MONTHS) & ~signals.model_month.isin(paused)].copy()
    frame = frame.sort_values(["d0_date", "stk_code"]).reset_index(drop=True)
    assert not frame.duplicated(["stk_code", "d0_date"]).any()
    raw_d0 = r106.d0_minutes(frame)
    early = pd.read_pickle(r105.OUT / "qdc_candidate_first10_normalized.pkl")
    values = r106.valuation(frame)
    base = r104.ROOT / "outputs/stock_automl/b2_daily_only_r100_20261023/r90/panel.pkl"
    panel = r106.features(frame, raw_d0, early, values, base_panel=base)
    panel = q75_score(panel)
    panel.to_pickle(OUT / "risk_panel.pkl")
    raw_d2 = sell.extract_d2(panel)
    panel = sell.add_sell(panel, raw_d2)
    actions = pd.read_csv(r104.ROOT / "docs/stock_automl_runs/20261023_b2_2018_2021_r90/evidence/corporate_actions.csv",
                          dtype={"stk_code": str})
    actions = actions[["stk_code", "ex_date", "div_cash_pre_tax"]].rename(
        columns={"ex_date": "d2_date", "div_cash_pre_tax": "d2_cash_dividend"})
    panel = panel.merge(actions, on=["stk_code", "d2_date"], how="left", validate="many_to_one")
    panel["d2_cash_dividend"] = panel.d2_cash_dividend.fillna(0.0)
    assert panel.d2_cash_dividend.gt(0).sum() == 1
    panel.to_pickle(OUT / "risk_sell_panel.pkl")
    annual, monthly = yearly_and_monthly(panel, gates)
    annual.to_csv(DOC / "yearly.csv", index=False)
    monthly.to_csv(DOC / "monthly.csv", index=False)
    panel[["stk_code", "model_month", "d0_date", "d1_date", "d2_date",
           "entry", "qdc_d2_close", "sell7", "q75_score", "q75_veto",
           "total_mv_yi", "mv_source", "d0_turnover_pct", "d2_cash_dividend", "close_floor",
           "close_unverified", "seven_unverified", "d0_ohlc_match",
           "d2_ohlc_match"]].to_csv(DOC / "trades.csv", index=False)
    summary = dict(signals=len(frame), q75_veto=int(panel.q75_veto.sum()),
                   retained=int((~panel.q75_veto).sum()),
                   missing_mv=int(panel.total_mv_yi.isna().sum()),
                   d0_ohlc_mismatch=int((~panel.d0_ohlc_match).sum()),
                   d2_ohlc_mismatch=int((~panel.d2_ohlc_match).sum()),
                   q75_model_sha256=r106.digest(r106.RISK / "union_early_lr01.joblib"),
                   q75_selection_sha256=r106.digest(r106.RISK / "selection_frozen.json"),
                   qdc_minute_source="/Volumes/ext/minite_k_data/1分钟/{year}.parquet",
                   qdc_sources={path: value for path, value in
                                json.loads((r104.OUT / "manifest.json").read_text())["qdc_files"].items()
                                if any(f"/{year}.parquet" in path for year in range(2017, 2023))},
                   historical_d0_panel_sha256=r106.digest(base),
                   corporate_actions_sha256=r106.digest(
                       r104.ROOT / "docs/stock_automl_runs/20261023_b2_2018_2021_r90/evidence/corporate_actions.csv"),
                   d0_valuation_cache_sha256=r106.digest(OUT / "d0_valuation_readonly.csv"),
                   monthly_method="Each D0 model month starts with independent 1m cash; trades close by D2.")
    (DOC / "manifest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(annual.to_string(index=False), flush=True)
    print(summary, flush=True)


if __name__ == "__main__":
    main()
