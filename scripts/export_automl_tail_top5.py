"""Export all daily top-five out-of-fold candidates for manual factor review.

The saved model scores and factors are read unchanged. Next-morning prices are
joined only after selection, and never enter ranking.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pandas as pd
from dotenv import dotenv_values


def load_top_five(workbook_path: Path) -> pd.DataFrame:
    workbook = json.loads(workbook_path.read_text())
    selected = pd.DataFrame(row for row in workbook["all"] if row["rank_binary"] <= 5)
    selected = selected.sort_values(["signal_date", "rank_binary", "symbol6"])
    if selected.empty or selected.duplicated(["signal_date", "symbol6"]).any():
        raise ValueError("No distinct daily top-five selections")
    if not selected.groupby("signal_date").rank_binary.apply(
            lambda ranks: sorted(ranks.tolist()) == [1, 2, 3, 4, 5]).all():
        raise ValueError("Each signal day must have ranks 1 through 5")
    return selected


def fetch_morning_bars(selected: pd.DataFrame, env_path: Path) -> pd.DataFrame:
    config = dotenv_values(env_path)
    url = config.get("SIMPLE_BI_PLATFORM_DB_URL") or config.get("PLATFORM_DB_URL")
    if url:
        os.environ["SIMPLE_BI_PLATFORM_DB_URL"] = url
    from scripts.experiment_automl_1450_grid import frame, read_only_db

    output = []
    with read_only_db() as conn:
        for day, group in selected.groupby("next_date", sort=True):
            symbols = group.symbol6.tolist()
            placeholders = ",".join(["%s"] * len(symbols))
            query = f"""SELECT trade_date, stk_code, bar_end_time, latest_price,
                               is_fallback, is_finalized, source_snapshot_time
                        FROM aiia_stock_realtime_minute_snapshot_full
                        WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
                          AND bar_end_time=%s AND stk_code IN ({placeholders})"""
            output.append(frame(conn, query, (day, f"{day} 09:40:00", *symbols)))
    bars = pd.concat(output, ignore_index=True).rename(columns={
        "trade_date": "next_date", "stk_code": "symbol6", "latest_price": "next_0940"})
    bars["next_date"] = bars.next_date.astype(str)
    if len(bars) != len(selected) or bars.duplicated(["next_date", "symbol6"]).any():
        raise ValueError(f"Expected {len(selected)} unique 09:40 bars, got {len(bars)}")
    if not (bars.is_fallback.eq(0) & bars.is_finalized.eq(1) &
            pd.to_datetime(bars.bar_end_time).eq(pd.to_datetime(bars.source_snapshot_time)) &
            pd.to_datetime(bars.bar_end_time).dt.strftime("%H:%M:%S").eq("09:40:00")).all():
        raise ValueError("09:40 bars must be exact, finalized, and not fallback")
    return bars[["next_date", "symbol6", "next_0940"]]


def export(workbook_path: Path, candidate_path: Path, env_path: Path, output_path: Path):
    selected = load_top_five(workbook_path)
    prices = pd.read_csv(candidate_path, dtype={"symbol6": str})[
        ["signal_date", "symbol6", "entry_1450", "next_open", "next_high5", "next_high10"]]
    bars = fetch_morning_bars(selected, env_path)
    rows = selected.merge(prices, on=["signal_date", "symbol6"], how="left",
                          validate="one_to_one", suffixes=("", "_price"))
    for column in ("entry_1450", "next_open", "next_high5", "next_high10"):
        other = f"{column}_price"
        if other in rows.columns:
            if not pd.to_numeric(rows[column]).round(6).eq(
                    pd.to_numeric(rows[other]).round(6)).all():
                raise ValueError(f"Saved workbook and price source disagree on {column}")
            rows = rows.drop(columns=other)
    rows = rows.merge(bars, on=["next_date", "symbol6"], how="left", validate="one_to_one")
    numeric = ["entry_1450", "next_open", "next_high5", "next_high10", "next_0940"]
    rows[numeric] = rows[numeric].apply(pd.to_numeric, errors="coerce")
    if len(rows) != len(selected) or rows[numeric].isna().any().any() or not rows[numeric].gt(0).all().all():
        raise ValueError("Missing or invalid price in daily top-five rows")
    for rank in (2, 3, 5):
        rows[f"top{rank}_tag"] = rows.rank_binary.le(rank).astype(int)
    rows["return_0940"] = rows.next_0940 / rows.entry_1450 - 1
    rows["critical_tag"] = rows["class"].eq(-1).astype(int)
    rows = rows.sort_values(["signal_date", "rank_binary", "symbol6"])
    columns = ["signal_date", "next_date", "symbol6", "name", "rank_binary",
               "top2_tag", "top3_tag", "top5_tag", "binary_pass_score",
               "signal_return", "volume_ratio", "turnover_so_far_pct",
               "float_mv_100m_cny", "volume_4of5_increasing", "ma_bull_5_10_20",
               "price_above_all_ma", "all_intraday_lows_above_ma", "entry_1450",
               "next_open", "target_next_open_return", "next_high5",
               "target_next_high5_return", "next_high10", "target_next_high10_return",
               "next_0940", "return_0940", "class", "critical_tag",
               "t_final_limit_flag"]
    payload = rows[columns].to_dict(orient="records")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))
    print(json.dumps({"rows": len(payload), "days": rows.signal_date.nunique(),
                      "top2_critical": int(rows.loc[rows.top2_tag.eq(1), "critical_tag"].sum()),
                      "top3_critical": int(rows.loc[rows.top3_tag.eq(1), "critical_tag"].sum()),
                      "top5_critical": int(rows.critical_tag.sum())}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, default=Path(
        "outputs/stock_automl/tail_critical_review/workbook_data.json"))
    parser.add_argument("--candidates", type=Path, default=Path(
        "outputs/stock_automl/tail_standard/candidates_1440.csv"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output", type=Path, default=Path(
        "outputs/stock_automl/tail_critical_review/daily_top5_sheet_data.json"))
    args = parser.parse_args()
    export(args.workbook, args.candidates, args.env_file, args.output)
