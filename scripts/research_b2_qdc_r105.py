"""Recheck QDC first-minute time labels before replaying frozen B2 decisions.

QDC has both 09:30 and 09:31 rows. The archived 09:31 completed bar is
their OHLCV union, while 09:32 onward keeps the same label. Preserve R104's
daily inputs and frozen D0 ranking, and write a separate run for comparison.
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from scripts import research_b2_qdc_r104 as old
from scripts.research_b2_daily_only_r100 import read_inputs

OUT = old.ROOT / "outputs/stock_automl/b2_qdc_r105_20261024"


def opening_rows(candidates: pd.DataFrame) -> pd.DataFrame:
    wanted = candidates[["qdc_symbol", "d1_date"]].drop_duplicates()
    parts = []
    for year in sorted(wanted.d1_date.str[:4].astype(int).unique()):
        subset = wanted[wanted.d1_date.str.startswith(str(year))]
        con = duckdb.connect()
        con.register("wanted", subset)
        path = (str(old.QDC / "1分钟" / f"{year}.parquet") if year < 2026
                else str(old.QDC / "1分钟/2026-*.parquet"))
        frame = con.execute("""
            SELECT m.symbol AS qdc_symbol,
              CAST(CAST(to_timestamp(m.ts/1000) AT TIME ZONE 'Asia/Shanghai' AS DATE) AS VARCHAR) AS d1_date,
              CAST(to_timestamp(m.ts/1000) AT TIME ZONE 'Asia/Shanghai' AS TIMESTAMP) AS time,
              m.open, m.high, m.low, m.close, m.volume, m.amount, m.source
            FROM read_parquet(?) m JOIN wanted w ON m.symbol=w.qdc_symbol
              AND CAST(CAST(to_timestamp(m.ts/1000) AT TIME ZONE 'Asia/Shanghai'
                  AS DATE) AS VARCHAR)=w.d1_date
            WHERE CAST(to_timestamp(m.ts/1000) AT TIME ZONE 'Asia/Shanghai' AS TIME)
              = TIME '09:30:00'
        """, [path]).df()
        con.close()
        parts.append(frame)
        print(f"opening {year}: {len(frame)} / {len(subset)}", flush=True)
    return pd.concat(parts, ignore_index=True)


def merge_first_bar(first10: pd.DataFrame, first30: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    keys = ["qdc_symbol", "d1_date"]
    base = first10.copy()
    at31 = base[base.time.dt.strftime("%H:%M").eq("09:31")].copy()
    at30 = first30.rename(columns={x: f"{x}_30" for x in
                               ["open", "high", "low", "close", "volume", "amount", "source", "time"]})
    combined = at31.merge(at30, on=keys, how="left", validate="one_to_one", indicator=True)
    has30 = combined._merge.eq("both")
    combined.loc[has30, "open"] = combined.loc[has30, "open_30"]
    combined.loc[has30, "high"] = combined.loc[has30, ["high", "high_30"]].max(axis=1)
    combined.loc[has30, "low"] = combined.loc[has30, ["low", "low_30"]].min(axis=1)
    combined.loc[has30, "volume"] += combined.loc[has30, "volume_30"]
    combined.loc[has30, "amount"] += combined.loc[has30, "amount_30"]
    out = pd.concat([combined[base.columns], base[base.time.dt.strftime("%H:%M").ne("09:31")]],
                    ignore_index=True)
    audit = combined[keys + ["_merge"]].copy()
    audit["has_0930"] = has30.to_numpy()
    return out, audit


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    candidates = pd.read_pickle(old.OUT / "candidates.pkl")
    daily = pd.read_pickle(old.OUT / "qdc_candidate_daily.pkl")
    first10 = pd.read_pickle(old.OUT / "qdc_candidate_first10.pkl")
    normalized = OUT / "qdc_candidate_first10_normalized.pkl"
    coverage = OUT / "first_bar_coverage.csv"
    if normalized.exists() and coverage.exists():
        minutes = pd.read_pickle(normalized)
        audit = pd.read_csv(coverage)
        first30_count = int(audit.has_0930.sum())
    else:
        first30 = opening_rows(candidates)
        minutes, audit = merge_first_bar(first10, first30)
        minutes.to_pickle(normalized)
        audit.to_csv(coverage, index=False)
        first30_count = len(first30)
    example = minutes[(minutes.qdc_symbol == "000004.XSHE") &
                      (minutes.d1_date == "2024-09-24") &
                      (minutes.time.dt.strftime("%H:%M") == "09:31")]
    archived = pd.read_csv(old.ROOT / "docs/stock_automl_runs/20261021_b2_d1_risk/minutes/2024-09-24_000004.csv.gz")
    assert len(example) == 1 and np.allclose(
        example[["open", "high", "low", "close", "volume"]].iloc[0].to_numpy(float),
        archived[["open", "high", "low", "close", "volume"]].iloc[0].to_numpy(float),
        rtol=0, atol=.001), "QDC first-minute normalization no longer reproduces archived bar"
    rules = {}
    for path in old.RULE_PATHS:
        rules.update({item["month"]: item["selected"] for item in json.loads(path.read_text())})
    candidates = old.attach_daily(candidates.drop(columns=[x for x in candidates.columns if
                                   x.startswith("qdc_d") and x != "qdc_symbol"]), daily)
    signals, decisions = old.choose(candidates, minutes, rules)
    _, calendar, _ = read_inputs()
    years = list(range(2017, 2027))
    yearly, gates, ledger, shadow = old.summarize(signals, calendar, candidates, years)
    signals.to_pickle(OUT / "signals.pkl")
    decisions.to_csv(OUT / "decisions.csv", index=False)
    yearly.to_csv(OUT / "yearly.csv", index=False)
    gates.to_csv(OUT / "gates.csv", index=False)
    ledger.to_csv(OUT / "ledger.csv", index=False)
    info = dict(method="R104 frozen D0 scores/rules and QDC daily, with QDC 09:30+09:31 merged into archived 09:31 completed bar",
                old_run="b2_qdc_r104_20261024", candidates=len(candidates),
                first30=first30_count, first31=int(first10.time.dt.strftime("%H:%M").eq("09:31").sum()),
                missing_both=int(len(candidates) - len(audit)),
                first30_missing_given_first31=int((~audit.has_0930).sum()),
                signals=len(signals), shadow=shadow)
    (OUT / "manifest.json").write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n")
    print(yearly.to_string(index=False), flush=True)
    print(info, flush=True)


if __name__ == "__main__":
    main()
