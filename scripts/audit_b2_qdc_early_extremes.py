"""Compare QDC daily high/low with traded 1-minute extrema, SH/SZ 2014-21.

Use the same 0.011-yuan tolerance and traded-minute definition as the saved
QDC market audit. Report missing and excess extrema separately.
"""
from __future__ import annotations

from pathlib import Path
import sys

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
QDC = Path("/Volumes/ext/minite_k_data")
OUT = ROOT / "docs/stock_automl_runs/20261024_b2_qdc_r108"


def audit_d1_candidates() -> None:
    root = ROOT / "outputs/stock_automl/b2_qdc_r108_20261024"
    candidates = pd.read_pickle(root / "candidates.pkl")
    wanted = candidates[["qdc_symbol", "d1_date"]].drop_duplicates()
    con = duckdb.connect()
    con.execute("SET threads=4")
    con.register("wanted", wanted)
    readings = []
    for year in range(2014, 2018):
        minute = str(QDC / "1分钟" / f"{year}.parquet")
        part = con.execute("""
          WITH minute AS (
            SELECT m.symbol AS qdc_symbol,
              CAST(FLOOR((m.ts+28800000)/86400000) AS BIGINT) AS day,
              MAX(CASE WHEN m.volume>0 OR m.amount>0 THEN m.high END) AS minute_high,
              MIN(CASE WHEN m.volume>0 OR m.amount>0 THEN m.low END) AS minute_low
            FROM read_parquet(?) m
            GROUP BY 1,2
          )
          SELECT m.qdc_symbol,
            CAST(to_timestamp(m.day*86400) AS DATE) AS d1_date,
            m.minute_high,m.minute_low
          FROM minute m JOIN wanted w ON m.qdc_symbol=w.qdc_symbol
            AND CAST(to_timestamp(m.day*86400) AS DATE)=CAST(w.d1_date AS DATE)
        """, [minute]).df()
        part["d1_date"] = part.d1_date.astype(str)
        readings.append(part)
    con.close()
    full = pd.concat(readings, ignore_index=True)
    compared = candidates.merge(full, on=["qdc_symbol", "d1_date"], validate="one_to_one")
    assert len(compared) == len(candidates)
    compared["high_missing"] = compared.minute_high.lt(compared.qdc_d1_high - .011)
    compared["low_missing"] = compared.minute_low.gt(compared.qdc_d1_low + .011)
    compared["high_excess"] = compared.minute_high.gt(compared.qdc_d1_high + .011)
    compared["low_excess"] = compared.minute_low.lt(compared.qdc_d1_low - .011)
    compared["any_conflict"] = compared[["high_missing", "low_missing",
                                           "high_excess", "low_excess"]].any(axis=1)
    signals = pd.read_pickle(root / "risk_sell_news_panel.pkl")
    groups = [("D0 Top3", compared)]
    for name, part in [("V2", signals),
                       ("Q75+news", signals[~(signals.q75_veto | signals.news_veto)])]:
        keys = part[["stk_code", "d0_date"]].drop_duplicates()
        groups.append((name, compared.merge(keys, on=["stk_code", "d0_date"],
                                            validate="one_to_one")))
    rows = []
    for name, group in groups:
        for year, part in group.groupby(group.d0_date.str[:4]):
            rows.append(dict(cohort=name, year=year, stock_days=len(part),
                             high_missing=int(part.high_missing.sum()),
                             low_missing=int(part.low_missing.sum()),
                             high_excess=int(part.high_excess.sum()),
                             low_excess=int(part.low_excess.sum()),
                             any_conflict=int(part.any_conflict.sum())))
    pd.DataFrame(rows).to_csv(OUT / "qdc_d1_candidate_extreme.csv", index=False)
    compared.loc[compared.any_conflict, ["stk_code", "d0_date", "d1_date",
                                        "qdc_d1_high", "minute_high", "qdc_d1_low",
                                        "minute_low"]].to_csv(OUT / "qdc_d1_candidate_extreme_details.csv", index=False)
    print(pd.DataFrame(rows).to_string(index=False), flush=True)


def main() -> None:
    con = duckdb.connect()
    con.execute("SET threads=4")
    con.execute("SET memory_limit='8GB'")
    rows = []
    years = [] if "--selected-only" in sys.argv else range(2014, 2022)
    for year in years:
        minute = str(QDC / "1分钟" / f"{year}.parquet")
        daily = str(QDC / "日线" / f"{year}.parquet")
        row = con.execute("""
          WITH minute AS (
            SELECT symbol, CAST(FLOOR((ts+28800000)/86400000) AS BIGINT) AS day,
              MAX(CASE WHEN volume>0 OR amount>0 THEN high END) AS minute_high,
              MIN(CASE WHEN volume>0 OR amount>0 THEN low END) AS minute_low
            FROM read_parquet(?)
            WHERE split_part(symbol,'.',2) IN ('XSHG','XSHE')
            GROUP BY 1,2
          ), daily AS (
            SELECT symbol,CAST(FLOOR((ts+28800000)/86400000) AS BIGINT) AS day,
              high AS daily_high,low AS daily_low
            FROM read_parquet(?)
            WHERE volume>0 AND split_part(symbol,'.',2) IN ('XSHG','XSHE')
          ), paired AS (
            SELECT d.*,m.minute_high,m.minute_low
            FROM daily d JOIN minute m USING(symbol,day)
          )
          SELECT COUNT(*) AS paired,
            COUNT(*) FILTER (WHERE minute_high IS NULL OR minute_low IS NULL) AS no_traded_extreme,
            COUNT(*) FILTER (WHERE minute_high < daily_high-.011) AS high_missing,
            COUNT(*) FILTER (WHERE minute_low > daily_low+.011) AS low_missing,
            COUNT(*) FILTER (WHERE minute_high > daily_high+.011) AS high_excess,
            COUNT(*) FILTER (WHERE minute_low < daily_low-.011) AS low_excess,
            COUNT(*) FILTER (WHERE minute_high < daily_high-.011 OR minute_low > daily_low+.011) AS missing_either,
            COUNT(*) FILTER (WHERE ABS(minute_high-daily_high)>.011 OR ABS(minute_low-daily_low)>.011) AS any_conflict
          FROM paired
        """, [minute, daily]).fetchdf().iloc[0].to_dict()
        row = {key: int(value) for key, value in row.items()}
        row["year"] = year
        row["missing_pct"] = row["missing_either"] / row["paired"] * 100
        row["any_conflict_pct"] = row["any_conflict"] / row["paired"] * 100
        rows.append(row)
        print(row, flush=True)
    con.close()
    if rows:
        pd.DataFrame(rows).to_csv(OUT / "qdc_shsz_extreme_2014_2021.csv", index=False)
    selected_rows = []
    details = []
    for run in ("r108", "r107"):
        root = ROOT / "outputs/stock_automl" / f"b2_qdc_{run}_20261024"
        panel = pd.read_pickle(root / "risk_sell_news_panel.pkl")
        for cohort, sample in (("V2", panel),
                               ("Q75+news", panel[~(panel.q75_veto | panel.news_veto)])):
            for tag in ("d0", "d2"):
                minute = pd.read_pickle(root / f"qdc_selected_{tag}_minutes.pkl")
                minute = minute[minute.volume.gt(0) | minute.amount.gt(0)]
                grouped = minute.groupby(["qdc_symbol", f"{tag}_date"]).agg(
                    minute_high=("high", "max"), minute_low=("low", "min")).reset_index()
                compared = sample.merge(grouped, on=["qdc_symbol", f"{tag}_date"],
                                        validate="one_to_one")
                assert len(compared) == len(sample)
                compared["high_missing"] = compared.minute_high.lt(compared[f"qdc_{tag}_high"] - .011)
                compared["low_missing"] = compared.minute_low.gt(compared[f"qdc_{tag}_low"] + .011)
                compared["high_excess"] = compared.minute_high.gt(compared[f"qdc_{tag}_high"] + .011)
                compared["low_excess"] = compared.minute_low.lt(compared[f"qdc_{tag}_low"] - .011)
                compared["extreme_missing"] = compared.high_missing | compared.low_missing
                compared["any_conflict"] = (compared.extreme_missing | compared.high_excess |
                                            compared.low_excess)
                for year, group in compared.groupby(compared.d0_date.str[:4]):
                    selected_rows.append(dict(run=run, cohort=cohort, tag=tag, year=year,
                                              stock_days=len(group),
                                              high_missing=int(group.high_missing.sum()),
                                              low_missing=int(group.low_missing.sum()),
                                              high_excess=int(group.high_excess.sum()),
                                              low_excess=int(group.low_excess.sum()),
                                              extreme_missing=int(group.extreme_missing.sum()),
                                              any_conflict=int(group.any_conflict.sum())))
                for row in compared[compared.any_conflict].itertuples():
                    details.append(dict(run=run, cohort=cohort, tag=tag, stk_code=row.stk_code,
                                        d0_date=row.d0_date, day=getattr(row, f"{tag}_date"),
                                        daily_high=getattr(row, f"qdc_{tag}_high"),
                                        minute_high=row.minute_high,
                                        daily_low=getattr(row, f"qdc_{tag}_low"),
                                        minute_low=row.minute_low,
                                        high_missing=row.high_missing,low_missing=row.low_missing,
                                        high_excess=row.high_excess,low_excess=row.low_excess))
    pd.DataFrame(selected_rows).to_csv(OUT / "qdc_selected_extreme_2014_2021.csv", index=False)
    pd.DataFrame(details).to_csv(OUT / "qdc_selected_extreme_details.csv", index=False)


if __name__ == "__main__":
    if "--selected-d1" in sys.argv:
        audit_d1_candidates()
    else:
        main()
