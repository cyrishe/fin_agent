"""Read-only D0 Top3 price/valuation-position audit; frozen scores stay unchanged."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.research_b2_daily_only_r100 import ROOT, read_inputs

OUT = ROOT / "docs/stock_automl_runs/20261024_b2_d0_value_position_r102"
FEATURES = {
    "price_pct_20": ("price", 20, 15),
    "price_pct_60": ("price", 60, 45),
    "price_pct_252": ("price", 252, 120),
    "mv_pct_20": ("mv", 20, 15),
    "mv_pct_60": ("mv", 60, 45),
    "pe_pct_252": ("pe", 252, 120),
    "pe_pct_504": ("pe", 504, 250),
}


def position(hist: np.ndarray, size: int, minimum: int) -> float:
    if len(hist) == 0 or not np.isfinite(hist[-1]):
        return np.nan
    values = hist[-size:]
    values = values[np.isfinite(values)]
    return float(np.mean(values <= values[-1])) if len(values) >= minimum else np.nan


def fetch(conn, table: str, cols: str, codes: list[str], start: str, end: str) -> pd.DataFrame:
    marks = ",".join(["%s"] * len(codes))
    return query(conn, f"SELECT {cols} FROM {table} WHERE stk_code IN ({marks}) AND trade_date BETWEEN %s AND %s",
                 tuple(codes) + (start, end))


def compute() -> pd.DataFrame:
    ranked, _, _ = read_inputs()
    ranked = ranked[ranked.d0_date.ge("2018-01-01")].copy()
    ranked["year"] = ranked.d0_date.str[:4]
    ranked["d1_openable"] = (ranked.d0_st.eq(0) & ranked.d1_st.eq(0) &
                            ranked.d1_open.gt(0) & ranked.d1_volume.gt(0) &
                            ranked.d1_cap.gt(0) & ranked.d1_open.lt(ranked.d1_cap - .005))
    ranked["d2_open_proxy_return"] = np.where(ranked.d1_openable,
        ranked.d2_close / ranked.d1_open - 1, np.nan)
    result = []
    with db_connection() as conn:
        for year, target in ranked.groupby("year", sort=True):
            codes = sorted(target.stk_code.unique())
            start = f"{int(year)-2}-01-01"
            end = f"{year}-12-31" if year != "2026" else "2026-09-30"
            valuation, prices = [], []
            for i in range(0, len(codes), 80):
                batch = codes[i:i+80]
                valuation.append(fetch(conn, "kcrp_stock_pricevaluate",
                    "trade_date,stk_code,total_mv,pe_ttm,update_time", batch, start, end))
                prices.append(fetch(conn, "kcrp_stock_price",
                    "trade_date,stk_code,adjclose,close", batch,
                    f"{int(year)-1}-01-01", end))
            val = pd.concat(valuation, ignore_index=True)
            pri = pd.concat(prices, ignore_index=True)
            for frame in (val, pri):
                frame["trade_date"] = frame.trade_date.astype(str)
                assert not frame.duplicated(["stk_code", "trade_date"]).any()
                frame.sort_values(["stk_code", "trade_date"], inplace=True)
            for column in ("total_mv", "pe_ttm"):
                val[column] = pd.to_numeric(val[column], errors="coerce")
            for column in ("adjclose", "close"):
                pri[column] = pd.to_numeric(pri[column], errors="coerce")
            vgroups = {code: x.reset_index(drop=True) for code, x in val.groupby("stk_code")}
            pgroups = {code: x.reset_index(drop=True) for code, x in pri.groupby("stk_code")}
            for row in target.itertuples():
                p = pgroups.get(row.stk_code)
                v = vgroups.get(row.stk_code)
                rec = dict(symbol=row.symbol, d0_date=row.d0_date)
                if p is not None:
                    px = p[p.trade_date.le(row.d0_date)]
                    if len(px) and px.trade_date.iloc[-1] == row.d0_date:
                        hist = px.adjclose.to_numpy(dtype=float)
                        hist[hist <= 0] = np.nan
                        for name, (kind, window, minimum) in FEATURES.items():
                            if kind == "price": rec[name] = position(hist, window, minimum)
                        for window in (20, 60):
                            last = hist[-window:]
                            rec[f"price_high_ratio_{window}"] = (float(hist[-1]/np.nanmax(last))
                                if len(last) >= (15 if window == 20 else 45) else np.nan)
                        rec["db_d0_close"] = float(px.close.iloc[-1])
                if v is not None:
                    # Historical valuation rows have an after-close update time; exclude D0.
                    vh = v[v.trade_date.lt(row.d0_date)]
                    if len(vh):
                        mv = vh.total_mv.to_numpy(dtype=float)
                        mv[mv <= 0] = np.nan
                        pe = vh.pe_ttm.to_numpy(dtype=float)
                        pe[pe <= 0] = np.nan
                        rec["valuation_date"] = vh.trade_date.iloc[-1]
                        rec["mv_yi"] = float(vh.total_mv.iloc[-1]/1e8)
                        raw_pe = float(vh.pe_ttm.iloc[-1]) if pd.notna(vh.pe_ttm.iloc[-1]) else np.nan
                        rec["pe_ttm"] = raw_pe
                        rec["pe_state"] = "positive" if raw_pe > 0 else ("nonpositive" if np.isfinite(raw_pe) else "missing")
                        for name, (kind, window, minimum) in FEATURES.items():
                            if kind in ("mv", "pe"):
                                rec[name] = position(mv if kind == "mv" else pe, window, minimum)
                        for window in (20, 60):
                            last = mv[-window:]
                            rec[f"mv_high_ratio_{window}"] = (float(mv[-1]/np.nanmax(last))
                                if len(last) >= (15 if window == 20 else 45) else np.nan)
                result.append(rec)
            print(year, len(target), len(val), len(pri), flush=True)
        conn.rollback()
    enriched = ranked.merge(pd.DataFrame(result), on=["symbol", "d0_date"], validate="one_to_one")
    assert len(enriched) == len(ranked)
    assert enriched.db_d0_close.notna().sum() > .99*len(enriched)
    assert np.isclose(enriched.close, enriched.db_d0_close, atol=.011).mean() > .99
    return enriched


def summarize(frame: pd.DataFrame) -> dict:
    frame = frame[frame.observed_3].copy()
    out = {}
    for key in FEATURES:
        ready = frame[frame[key].notna()]
        bins = [("bottom40", ready[ready[key].le(.4)]),
                ("middle40", ready[ready[key].gt(.4) & ready[key].lt(.8)]),
                ("top20", ready[ready[key].ge(.8)])]
        out[key] = [dict(group=name, n=len(x), hit=int(x.label_3.sum()),
                         rate=float(x.label_3.mean()) if len(x) else None,
                         openable_n=int(x.d1_openable.sum()),
                         d2_open_proxy_mean=(float(x.loc[x.d1_openable,"d2_open_proxy_return"].mean())
                         if x.loc[x.d1_openable,"d2_open_proxy_return"].notna().any() else None))
                    for name, x in bins]
    out["pe_state"] = [dict(state=name, n=len(x), hit=int(x.label_3.sum()),
                             rate=float(x.label_3.mean()) if len(x) else None)
                       for name, x in frame.groupby("pe_state", dropna=False)]
    out["coverage"] = {key:int(frame[key].notna().sum()) for key in FEATURES}
    out["population"] = dict(n=len(frame), hits=int(frame.label_3.sum()), rate=float(frame.label_3.mean()))
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "d0_top3_positions_raw_cache.csv.gz"
    frame = pd.read_csv(path, dtype={"symbol":str,"d0_date":str,"stk_code":str,"d1_date":str}) if path.exists() else compute()
    _, calendar, _ = read_inputs()
    previous = dict(zip(calendar[1:], calendar[:-1]))
    frame["valuation_fresh"] = frame.valuation_date.eq(frame.d0_date.map(previous))
    stale = ~frame.valuation_fresh
    for key in ["mv_yi", "pe_ttm", "mv_high_ratio_20", "mv_high_ratio_60", "mv_pct_20", "mv_pct_60",
                "pe_pct_252", "pe_pct_504"]:
        frame.loc[stale, key] = np.nan
    frame.loc[stale, "pe_state"] = "stale_or_missing"
    frame["pe_state"] = frame.pe_state.fillna("missing")
    frame.to_csv(OUT / "d0_top3_positions_fresh.csv.gz", index=False, compression="gzip")
    if not path.exists(): frame.to_csv(path, index=False, compression="gzip")
    windows = {"2018-2022_explore": frame[frame.year.between(2018,2022)] if pd.api.types.is_integer_dtype(frame.year) else frame[frame.year.between("2018","2022")],
               "2023-2024_validate": frame[frame.year.between(2023,2024)] if pd.api.types.is_integer_dtype(frame.year) else frame[frame.year.between("2023","2024")],
               "2025-2026_check": frame[frame.year.between(2025,2026)] if pd.api.types.is_integer_dtype(frame.year) else frame[frame.year.between("2025","2026")],
               "all": frame}
    summary = {name:summarize(subset) for name,subset in windows.items()}
    (OUT / "summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    proxy = frame[frame.d1_openable & frame.pe_pct_252.notna() &
                  frame.d2_open_proxy_return.notna()].copy()
    proxy["pe_group"] = np.where(proxy.pe_pct_252.le(.4), "low",
                          np.where(proxy.pe_pct_252.ge(.8), "high", "middle"))
    proxy = proxy[proxy.pe_group.ne("middle")].copy()
    proxy["month"] = proxy.d0_date.str[:7]
    by_month = proxy.pivot_table(index="month", columns="pe_group",
                                 values="d2_open_proxy_return", aggfunc="mean").dropna()
    by_month["low_minus_high"] = by_month.low - by_month.high
    by_month.to_csv(OUT / "monthly_pe_pairs.csv")
    monthly_check = {}
    for name, (begin, end) in {"2018-2022_explore":("2018","2022"),
                               "2023-2024_validate":("2023","2024"),
                               "2025-2026_check":("2025","2026")}.items():
        years = by_month.index.str[:4]
        d = by_month.loc[(years >= begin) & (years <= end), "low_minus_high"]
        mean = float(d.mean())
        se = float(d.std(ddof=1) / np.sqrt(len(d)))
        monthly_check[name] = dict(months=len(d), mean_difference=mean,
                                   approximate_95_interval=[mean-1.96*se, mean+1.96*se])
    (OUT / "monthly_pe_check.json").write_text(json.dumps(monthly_check,ensure_ascii=False,indent=2)+"\n")
    def digest(p: Path) -> str:
        h = hashlib.sha256()
        with p.open("rb") as f:
            for block in iter(lambda: f.read(4*1024*1024), b""):
                h.update(block)
        return h.hexdigest()
    r100 = ROOT / "docs/stock_automl_runs/20261023_b2_daily_only_r100"
    source_paths = [path, OUT / "d0_top3_positions_fresh.csv.gz",
                    r100 / "input_manifest.json", r100 / "score_manifest.json"]
    (OUT / "manifest.json").write_text(json.dumps({
        "experiment": "B2_R102_D0_valuation_position",
        "command": "python -m scripts.research_b2_d0_valuation_position_r102",
        "database": "kingdomai read-only current historical rows; valuation as of previous trading day",
        "candidate_count": len(frame),
        "observed_label_count": int(frame.observed_3.sum()),
        "files": [{"path": str(p.relative_to(ROOT)), "sha256": digest(p)} for p in source_paths],
    }, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({name:stats["population"] for name,stats in summary.items()},ensure_ascii=False))


if __name__ == "__main__": main()
