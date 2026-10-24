"""Risk-first audit of R102 D0 positions against B2/V2 D2 downside."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.research_b2_daily_only_r100 import ROOT

DOC = ROOT / "docs/stock_automl_runs"
OUT = DOC / "20261024_b2_d0_valuation_tail_r103"
FACTOR = DOC / "20261021_b2_floor_factor_review/all_trade_factors.csv.gz"
POSITIONS = DOC / "20261024_b2_d0_value_position_r102/d0_top3_positions_fresh.csv.gz"
R69 = DOC / "20261021_b2_d2_limit_follow_r69/results.json"
R70 = DOC / "20261021_b2_floor_minutes_r70/results.json"
SIGNALS = ROOT / "outputs/stock_automl/b2_2021_minute_r60_20261020/signals.pkl"
FEATURES = ["price_pct_20", "price_pct_60", "price_pct_252", "price_high_ratio_60",
            "mv_pct_20", "mv_pct_60", "pe_pct_252", "pe_pct_504"]


def source() -> tuple[pd.DataFrame, pd.DataFrame]:
    val = pd.read_csv(POSITIONS, dtype={"symbol":str,"d0_date":str})
    assert len(val) == 6369 and not val.duplicated(["symbol","d0_date"]).any()
    cols = ["symbol", "d0_date", "d0_rank", "valuation_fresh", "valuation_date",
            "pe_state", "mv_yi", "pe_ttm"] + FEATURES
    val = val[cols].rename(columns={"pe_ttm":"prior_pe_ttm"})
    cols = ["prior_pe_ttm" if c == "pe_ttm" else c for c in cols]
    picked = pd.read_csv(FACTOR, dtype={"symbol":str,"d0_date":str})
    picked.symbol = picked.symbol.str.zfill(6)
    picked = picked.merge(val[cols], on=["symbol","d0_date"], how="left", validate="one_to_one", indicator=True)
    assert len(picked) == 306 and picked._merge.eq("both").all()
    picked.drop(columns="_merge", inplace=True)
    picked["open_floor_bool"] = picked.open_floor.eq("是")
    picked["close_floor_bool"] = picked.close_floor.eq("是")
    picked["floor_union"] = picked.open_floor_bool | picked.close_floor_bool
    picked["d2_down7"] = picked.d2_ret.le(-.07)
    picked["net_loss10"] = picked.original_net.le(-.10)
    picked["period"] = np.where(picked.d0_date.lt("2025"), "2022-24",
                                np.where(picked.d0_date.lt("2026"), "2025", "2026"))

    r69 = json.loads(R69.read_text())
    r70 = json.loads(R70.read_text())
    close = {(r["股票"][:6],r["D0"]) for r in r69["rows"] if r["封板"] == "跌停"}
    opening = {(r["股票"][:6],r["D0"]) for r in r70["rows"] if r["开盘跌停"] == "是"}
    shadow = pd.read_pickle(SIGNALS)
    shadow = shadow[shadow.d0_date.le("2026-09-30")].copy()
    shadow = shadow.merge(val[[c for c in cols if c != "d0_rank"]], on=["symbol","d0_date"],
                          how="left", validate="one_to_one", indicator=True)
    assert len(shadow) == 709 and shadow._merge.eq("both").all()
    shadow.drop(columns="_merge", inplace=True)
    shadow["open_floor_bool"] = [(r.symbol,r.d0_date) in opening for r in shadow.itertuples()]
    shadow["close_floor_bool"] = [(r.symbol,r.d0_date) in close for r in shadow.itertuples()]
    shadow["floor_union"] = shadow.open_floor_bool | shadow.close_floor_bool
    shadow["d2_ret"] = shadow.d2_close / shadow.d2_preclose - 1
    shadow["original_net"] = shadow.d2_close * .999 / (shadow.entry * 1.001) - 1
    shadow["d2_down7"] = shadow.d2_ret.le(-.07)
    shadow["net_loss10"] = shadow.original_net.le(-.10)
    shadow["period"] = np.where(shadow.d0_date.lt("2025"), "2022-24",
                                np.where(shadow.d0_date.lt("2026"), "2025", "2026"))
    check = picked.merge(shadow[["symbol","d0_date","open_floor_bool","close_floor_bool","d2_down7","net_loss10","original_net"]],
                         on=["symbol","d0_date"], suffixes=("","_shadow"), validate="one_to_one")
    assert len(check) == 306
    for key in ["open_floor_bool","close_floor_bool","d2_down7","net_loss10"]:
        assert check[key].eq(check[f"{key}_shadow"]).all(), key
    assert np.allclose(check.original_net, check.original_net_shadow, atol=1e-8, rtol=0)
    assert (picked[["open_floor_bool","close_floor_bool","floor_union"]].sum().tolist() == [34,52,67])
    assert (shadow[["open_floor_bool","close_floor_bool","floor_union"]].sum().tolist() == [85,133,161])
    return picked, shadow


def group_name(frame: pd.DataFrame, key: str) -> pd.Series:
    v = frame[key]
    if key == "price_high_ratio_60":
        return pd.Series(np.select([v.lt(.90),v.lt(.98),v.ge(.98)],
                                   ["below_90pct_high","90_to_98pct_high","at_least_98pct_high"],
                                   default="missing"), index=frame.index)
    return pd.Series(np.select([v.le(.4),v.lt(.8),v.ge(.8)],
                               ["bottom_40pct","middle_40pct","top_20pct"],default="missing"),index=frame.index)


def summaries(picked: pd.DataFrame, shadow: pd.DataFrame) -> tuple[pd.DataFrame,pd.DataFrame,pd.DataFrame]:
    period_rows, group_rows, veto_rows = [], [], []
    for cohort, frame in [("V2_306",picked),("B2_all_709",shadow)]:
        for period in ["2022-24","2025","2026","all"]:
            q = frame if period == "all" else frame[frame.period.eq(period)]
            period_rows.append(dict(cohort=cohort, period=period, n=len(q),
                open_floor=int(q.open_floor_bool.sum()), close_floor=int(q.close_floor_bool.sum()),
                floor_union=int(q.floor_union.sum()), d2_down7=int(q.d2_down7.sum()),
                net_loss10=int(q.net_loss10.sum()), valuation_fresh=int(q.valuation_fresh.sum()),
                pe252_available=int(q.pe_pct_252.notna().sum())))
            for key in FEATURES:
                groups = group_name(q,key)
                for name in (["below_90pct_high","90_to_98pct_high","at_least_98pct_high","missing"]
                             if key == "price_high_ratio_60" else
                             ["bottom_40pct","middle_40pct","top_20pct","missing"]):
                    sub = q[groups.eq(name)]
                    group_rows.append(dict(cohort=cohort, period=period, feature=key, group=name,
                        n=len(sub), open_floor=int(sub.open_floor_bool.sum()),
                        close_floor=int(sub.close_floor_bool.sum()), floor_union=int(sub.floor_union.sum()),
                        d2_down7=int(sub.d2_down7.sum()), net_loss10=int(sub.net_loss10.sum()),
                        positive_old_price_return=int(sub.original_net.gt(0).sum()),
                        mean_old_price_return=float(sub.original_net.mean()) if len(sub) else np.nan))
            for key in ["price_pct_252","mv_pct_60","pe_pct_252","pe_pct_504"]:
                veto = q[key].ge(.8)
                z = q[veto]
                veto_rows.append(dict(cohort=cohort,period=period,rule=f"skip_{key}_top20",
                    skip_n=len(z),floor_caught=int(z.floor_union.sum()),
                    nonfloor_skipped=int((~z.floor_union).sum()),
                    profitable_nonfloor_skipped=int((~z.floor_union & z.original_net.gt(0)).sum()),
                    floor_missed=int(q.loc[~veto,"floor_union"].sum()),
                    old_price_return_sum=float(z.original_net.sum())))
    return pd.DataFrame(period_rows),pd.DataFrame(group_rows),pd.DataFrame(veto_rows)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(4*1024*1024),b""):
            h.update(block)
    return h.hexdigest()


def main() -> None:
    OUT.mkdir(parents=True,exist_ok=True)
    picked, shadow = source()
    cols = ["symbol","d0_date","d0_rank","period","valuation_fresh","valuation_date","pe_state",
            "mv_yi","prior_pe_ttm"] + FEATURES + ["open_floor_bool","close_floor_bool","floor_union",
            "d2_ret","d2_down7","original_net","net_loss10"]
    picked_cols = cols + ["stock_name","industry","board_streak_days","d0_turnover_pct",
                          "d0_body","opened_after_touch","score","entry","d2_close_saved"]
    picked[picked_cols].to_csv(OUT / "v2_306_tail_factors.csv.gz",index=False,compression="gzip")
    shadow[cols].to_csv(OUT / "b2_709_tail_factors.csv.gz",index=False,compression="gzip")
    periods, groups, veto = summaries(picked,shadow)
    periods.to_csv(OUT / "periods.csv",index=False)
    groups.to_csv(OUT / "factor_bins.csv",index=False)
    veto.to_csv(OUT / "veto_cost.csv",index=False)
    paths = [FACTOR,POSITIONS,R69,R70,SIGNALS,OUT / "v2_306_tail_factors.csv.gz",
             OUT / "b2_709_tail_factors.csv.gz",OUT / "periods.csv",OUT / "factor_bins.csv",OUT / "veto_cost.csv"]
    (OUT / "manifest.json").write_text(json.dumps({"experiment":"B2_R103_D0_valuation_tail",
        "data_rule":"R102 prior-trading-day valuation only; frozen B2/V2 outcome labels",
        "files":[dict(path=str(p.relative_to(ROOT)),sha256=digest(p)) for p in paths]},
        ensure_ascii=False,indent=2)+"\n")
    print(periods.to_string(index=False))


if __name__ == "__main__": main()
