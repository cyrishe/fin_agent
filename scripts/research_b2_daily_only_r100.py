"""Read-only daily-price proxy for a minute-free B2 alternative.

Reuse frozen monthly D0 RF scores. D1 open is a price proxy, not a proven fill.
This is a distinct research strategy; it does not replay B2's four-K execution.
"""
from __future__ import annotations

import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.replay_limit_rebound_cash_r23 import replay, sha
from scripts.audit_b2_minute_full_market_r98 import daily_paths


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "outputs/stock_automl"
OUT = DATA / "b2_daily_only_r100_20261023"
R90 = OUT / "r90"
R60 = DATA / "b2_2021_minute_r60_20261020"
R41 = DATA / "b2_2022_minute_r41_20261019"
R35 = DATA / "b2_two_month_2024_r35_20261019"
R18 = DATA / "limit_rebound_segments_r18_20261015"
MONTHS = [str(x) for x in pd.period_range("2017-05", "2026-09", freq="M")]
FEE = 0.001


def score_path(month: str) -> Path:
    base = (R90 / "daily2" if month < "2022-01" else
            R60 / "daily2" if month < "2022-09" else
            R41 / "daily2" if month < "2023-07" else
            R35 / "daily2" if month < "2025-01" else R18)
    return base / month / "segments_forecast_scores.pkl"


def read_inputs() -> tuple[pd.DataFrame, list[str], list[dict]]:
    old = pd.read_pickle(R90 / "panel.pkl")
    new = pd.read_pickle(R60 / "panel.pkl")
    columns = ["symbol", "stk_code", "d0_date", "close", "rate", "d0_st", "d1_date", "d1_preclose",
               "d1_open", "d1_close", "d1_volume", "d1_st", "d1_cap",
               "d2_preclose", "d2_close", "observed_3", "label_3"]
    panel = pd.concat([old[old.d0_date.lt("2022-01-01")][columns],
                       new[new.d0_date.ge("2022-01-01")][columns]], ignore_index=True)
    assert not panel.duplicated(["symbol", "d0_date"]).any()
    paths = [score_path(month) for month in MONTHS]
    assert all(p.exists() for p in paths)
    ranks = []
    manifest = []
    for month, path in zip(MONTHS, paths):
        frame = pd.read_pickle(path)
        assert frame.d0_date.str.startswith(month).all()
        assert not frame.duplicated(["symbol", "d0_date"]).any()
        ranks.append(frame.loc[frame.d0_rank.le(3),
                               ["symbol", "d0_date", "score", "d0_rank"]])
        manifest.append(dict(month=month, score_path=str(path.relative_to(ROOT)), sha256=sha(path)))
    ranked = pd.concat(ranks, ignore_index=True)
    assert not ranked.duplicated(["symbol", "d0_date"]).any()
    joined = ranked.merge(panel, on=["symbol", "d0_date"], how="left", validate="one_to_one", indicator=True)
    assert joined._merge.eq("both").all(), joined.loc[joined._merge.ne("both")].head()
    joined = joined.drop(columns="_merge")
    old_calendar = json.loads((R90 / "calendar.json").read_text())
    new_calendar = json.loads((R60 / "calendar.json").read_text())
    calendar = sorted(set(old_calendar) | set(new_calendar))
    assert len(calendar) == len(set(calendar))
    return joined, calendar, manifest


def select(ranked: pd.DataFrame, top: int) -> pd.DataFrame:
    frame = ranked[ranked.d0_rank.le(top)].copy()
    # Full-day positive volume is an ex-post no-trade screen: a zero-volume day
    # cannot have filled. It does not prove that an order could fill at the open.
    # Live use would need an opening quote and auction-volume observation.
    frame["openable"] = (frame.d0_st.eq(0) & frame.d1_st.eq(0) &
                         frame.d1_open.gt(0) & frame.d1_volume.gt(0) & frame.d1_cap.gt(0) &
                         frame.d1_open.lt(frame.d1_cap - .005))
    chosen = (frame[frame.openable].sort_values(["d0_date", "d0_rank", "symbol"])
              .drop_duplicates("d0_date", keep="first").copy())
    chosen["entry"] = chosen.d1_open
    chosen["price_return"] = chosen.d2_close * (1 - FEE) / (chosen.entry * (1 + FEE)) - 1
    chosen["model_month"] = chosen.d0_date.str[:7]
    chosen["year"] = chosen.d0_date.str[:4]
    chosen["variant"] = f"top{top}_first_openable"
    return chosen


def monthly_gate(shadow_nav: pd.DataFrame, source: pd.DataFrame) -> pd.DataFrame:
    nav = shadow_nav.set_index("date").sort_index()
    counts = source[source.d0_rank.eq(1)].groupby(source.d0_date.str[:7]).d0_date.nunique().to_dict()
    rows = []
    for month in MONTHS:
        previous = str(pd.Period(month, freq="M") - 1)
        dates = [d for d in nav.index if d.startswith(previous)]
        def trailing(n: int) -> float | None:
            if len(dates) < n:
                return None
            start = nav.index.get_loc(dates[-n])
            return None if start == 0 else float(nav.loc[dates[-1], "nav"] / nav.iloc[start-1].nav - 1)
        r5, r10 = trailing(5), trailing(10)
        prior_count = counts.get(previous)
        pause = bool(prior_count == 0 or
                     (prior_count is not None and r5 is not None and r10 is not None and r5 + r10 < 0))
        rows.append(dict(model_month=month, prior_d0_days=prior_count,
                         prior_last5_return=r5, prior_last10_return=r10, pause=pause))
    return pd.DataFrame(rows)


def account_rows(frame: pd.DataFrame, calendar: list[str], variant: str) -> tuple[list[dict], pd.DataFrame, pd.DataFrame, list[dict]]:
    years = range(2018, 2027)
    rows = []
    monthly_all = []
    excluded_all = []
    ledgers = []
    for year in years:
        q = frame[frame.year.eq(str(year))]
        if q.empty:
            continue
        end = calendar[calendar.index(max(q.d1_date)) + 1]
        result, months, _, ledger, excluded = replay(q, calendar,
            start=f"{year}-01-01", end=end, fee_rate=FEE)
        row = dict(variant=variant, year=year, selected=len(q),
                   selected_missing_d2=int(q.d2_close.isna().sum()),
                   selected_zero_d1_volume=int(q.d1_volume.fillna(0).le(0).sum()),
                   selected_d1_d3_seal_known=int(q.observed_3.sum()),
                   selected_d1_d3_seal_hits=int(q.loc[q.observed_3, "label_3"].sum()),
                   entries=result["entries"], excluded=result["excluded"],
                   price_account_return=result["total_return"],
                   max_drawdown=result["max_drawdown"],
                   account_daily_vol=float(_.return_.std(ddof=1)) if len(_) > 1 else None,
                   positive_months=result["positive_months"], months=result["months"],
                   closed_win_rate=result["closed_win_rate"],
                   closed_mean_return=result["closed_mean_return"])
        rows.append(row)
        monthly_all.extend(dict(variant=variant, model_year=year, **x) for x in months)
        excluded_all.extend(dict(variant=variant, model_year=year, **x) for x in excluded)
        if len(ledger):
            ledgers.append(ledger.assign(variant=variant, model_year=year))
    return rows, pd.DataFrame(monthly_all), pd.concat(ledgers, ignore_index=True), excluded_all


def audit_daily_sellability(top3: pd.DataFrame, calendar: list[str]) -> None:
    """Price-only floor flags; they do not establish an actual queue fill."""
    next_day = dict(zip(calendar, calendar[1:]))
    chosen = top3[top3.year.ge("2018")].copy()
    chosen["d2_date"] = chosen.d1_date.map(next_day)
    paths = daily_paths()
    parts = []
    for month, wanted in chosen.groupby(chosen.d2_date.str[:7]):
        path = paths[month]
        prices = pd.read_csv(path, usecols=["trade_date", "stk_code", "high", "low", "close", "volume"],
                             dtype={"trade_date": str, "stk_code": str})
        parts.append(prices[prices.trade_date.isin(wanted.d2_date) &
                            prices.stk_code.isin(wanted.stk_code)])
    daily = pd.concat(parts, ignore_index=True)
    assert not daily.duplicated(["trade_date", "stk_code"]).any()
    result = chosen.merge(daily, left_on=["d2_date", "stk_code"],
                          right_on=["trade_date", "stk_code"], how="left",
                          validate="many_to_one", suffixes=("", "_daily"))
    assert result.trade_date.notna().all(), result.loc[result.trade_date.isna(),
                                                       ["d2_date", "stk_code"]].head()
    assert np.allclose(result.d2_close, result.close_daily, atol=.011, rtol=0)

    def floor_price(preclose: float, rate: float) -> float:
        return float((Decimal(str(preclose)) * (1 - Decimal(str(rate)))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP))

    result["floor_price"] = [floor_price(p, r) for p, r in zip(result.d2_preclose, result.rate)]
    result["d2_floor_close"] = result.close_daily.sub(result.floor_price).abs().le(.005)
    result["d2_oneprice_floor"] = (result.d2_floor_close &
                                   result.high.sub(result.floor_price).abs().le(.005) &
                                   result.low.sub(result.floor_price).abs().le(.005))
    result[["d0_date", "d1_date", "d2_date", "stk_code", "d0_rank", "entry", "d2_close",
            "price_return", "rate", "floor_price", "d2_floor_close", "d2_oneprice_floor"]].to_csv(
                OUT / "top3_d2_daily_floor_audit.csv", index=False)

    outliers = []
    for year in range(2018, 2027):
        folder = (ROOT / "outputs/b2_r99_full_market_2018_2020_20261023" if year <= 2020 else
                  ROOT / "outputs/b2_r101_full_market_2024_20261023" if year == 2024 else
                  ROOT / "outputs/b2_r98_full_market_20261023")
        outliers.append(pd.read_csv(folder / f"price_outliers_{year}.csv", dtype={"code": str}))
    outlier = pd.concat(outliers, ignore_index=True)
    entry = result.merge(outlier[["date", "code", "daily_open", "minute_open"]]
                         .rename(columns={"date": "d1_date", "code": "stk_code"}),
                         on=["d1_date", "stk_code"], how="left")
    exit_ = result.merge(outlier[["date", "code", "daily_close", "minute_close"]]
                          .rename(columns={"date": "d2_date", "code": "stk_code"}),
                          on=["d2_date", "stk_code"], how="left")
    entry["recorded_open_gap"] = entry.minute_open.sub(entry.daily_open).abs().gt(.011)
    exit_["recorded_close_gap"] = exit_.minute_close.sub(exit_.daily_close).abs().gt(.011)
    exit_["minute_price_return"] = exit_.minute_close * (1 - FEE) / (exit_.entry * (1 + FEE)) - 1
    exit_["recorded_return_sign_flip"] = (exit_.recorded_close_gap &
        exit_.price_return.gt(0).ne(exit_.minute_price_return.gt(0)))
    summary = []
    for year in range(2018, 2027):
        mask = result.year.eq(str(year))
        scanned = exit_.loc[mask, "d2_date"].le("2026-08-07") if year == 2026 else pd.Series(True, index=exit_.index[mask])
        summary.append(dict(year=year, selected=int(mask.sum()),
            d2_floor_close=int(result.loc[mask, "d2_floor_close"].sum()),
            d2_oneprice_floor=int(result.loc[mask, "d2_oneprice_floor"].sum()),
            d2_minute_date_in_scan=int(scanned.sum()),
            recorded_d1_open_gap_gt_011=int(entry.loc[mask, "recorded_open_gap"].sum()),
            recorded_d2_close_gap_gt_011=int(exit_.loc[mask, "recorded_close_gap"].sum()),
            recorded_return_sign_flips=int(exit_.loc[mask, "recorded_return_sign_flip"].sum())))
    pd.DataFrame(summary).to_csv(OUT / "top3_price_quality_summary.csv", index=False)


def audit_later_open_proxy(source: pd.DataFrame) -> None:
    """Count D1 daily lows below cap when the auction/open price was capped."""
    source = source[source.d0_date.ge("2018-01-01")].copy()
    paths = daily_paths()
    pieces = []
    for month, wanted in source.groupby(source.d1_date.str[:7]):
        prices = pd.read_csv(paths[month], usecols=["trade_date", "stk_code", "low"],
                             dtype={"trade_date": str, "stk_code": str})
        pieces.append(prices[prices.trade_date.isin(wanted.d1_date) &
                             prices.stk_code.isin(wanted.stk_code)])
    lows = pd.concat(pieces, ignore_index=True).rename(columns={"trade_date": "d1_date",
                                                            "low": "d1_low"})
    assert not lows.duplicated(["stk_code", "d1_date"]).any()
    frame = source.merge(lows, on=["stk_code", "d1_date"], how="left", validate="many_to_one")
    frame["open_below"] = frame.d1_open.lt(frame.d1_cap - .005) & frame.d1_volume.gt(0)
    frame["capped_open_later_low"] = (frame.d1_open.ge(frame.d1_cap - .005) &
                                      frame.d1_low.lt(frame.d1_cap - .005) &
                                      frame.d1_volume.gt(0))
    summary = []
    for year, group in frame.groupby(frame.d0_date.str[:4]):
        openable_days = set(group.loc[group.open_below, "d0_date"])
        summary.append(dict(year=year, d0_days=group.d0_date.nunique(),
            top3_stock_days=len(group), d1_daily_low_missing=int(group.d1_low.isna().sum()),
            openable_d0_days=len(openable_days),
            capped_open_later_low_stock_days=int(group.capped_open_later_low.sum()),
            later_low_without_other_openable_d0_days=group.loc[
                group.capped_open_later_low & ~group.d0_date.isin(openable_days), "d0_date"].nunique()))
    pd.DataFrame(summary).to_csv(OUT / "openability_summary.csv", index=False)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    source, calendar, manifest = read_inputs()
    audit_later_open_proxy(source)
    (OUT / "score_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    (OUT / "input_manifest.json").write_text(json.dumps({
        "r90_panel_sha256": sha(R90 / "panel.pkl"),
        "r60_panel_sha256": sha(R60 / "panel.pkl"),
        "r90_calendar_sha256": sha(R90 / "calendar.json"),
        "r60_calendar_sha256": sha(R60 / "calendar.json"),
        "monthly_models": len(manifest), "first_model_month": MONTHS[0],
        "last_model_month": MONTHS[-1]}, ensure_ascii=False, indent=2) + "\n")
    yearly = []; months = []; ledgers = []; excluded = []; gates = []
    for top in (1, 3):
        shadow = select(source, top)
        assert not shadow.d1_date.duplicated().any()
        end = calendar[calendar.index(max(shadow.d1_date)) + 1]
        _, _, nav, _, shadow_excluded = replay(shadow, calendar,
            start="2017-05-01", end=end, fee_rate=FEE)
        gate = monthly_gate(nav, source).assign(variant=f"top{top}")
        gates.append(gate)
        paused = set(gate.loc[gate.pause, "model_month"])
        for suffix, picks in (("ungated", shadow),
                              ("monthly_pause", shadow[~shadow.model_month.isin(paused)])):
            name = f"top{top}_{suffix}"
            result, month, ledger, bad = account_rows(picks, calendar, name)
            yearly.extend(result); months.append(month); ledgers.append(ledger); excluded.extend(bad)
            print(name, len(picks), "2018-26", [(r["year"], r["entries"],
                round(r["price_account_return"], 4)) for r in result], flush=True)
        print("shadow_replay_exclusions", top, len(shadow_excluded), flush=True)
        shadow[shadow.year.ge("2018")].to_csv(OUT / f"top{top}_signals.csv", index=False)
        if top == 3:
            audit_daily_sellability(shadow, calendar)
    pd.DataFrame(yearly).to_csv(OUT / "yearly.csv", index=False)
    pd.concat(months, ignore_index=True).to_csv(OUT / "monthly.csv", index=False)
    pd.concat(ledgers, ignore_index=True).to_csv(OUT / "ledgers.csv", index=False)
    pd.DataFrame(excluded).to_csv(OUT / "excluded.csv", index=False)
    pd.concat(gates, ignore_index=True).to_csv(OUT / "monthly_gates.csv", index=False)


if __name__ == "__main__":
    main()
