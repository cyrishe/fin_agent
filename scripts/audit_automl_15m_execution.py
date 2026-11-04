"""Retrospective limit-close check for the 15m rolling selections."""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

import pandas as pd

from scripts.benchmark_automl_1440_inference import db_connection
from scripts.build_automl_sina_15m_rolling import at_nominal_five_limit
from scripts.experiment_automl_four_class_15m_rolling import OUT


def run(env_file: Path = Path("/Volumes/ext/fin_agent/.env"),
        output: Path = OUT) -> dict:
    selected = pd.read_csv(output / "selected_with_outcomes.csv", dtype={"symbol6": str})
    symbols = sorted(selected.symbol6.unique())
    with db_connection(env_file) as db:
        with db.cursor() as cursor:
            cursor.execute("""
                SELECT trade_date AS signal_date, LEFT(stk_code,6) AS symbol6,
                       preclose AS daily_preclose, close AS daily_close,
                       is_limit_price
                FROM kcrp_stock_price
                WHERE trade_date BETWEEN %s AND %s
                  AND LEFT(stk_code,6) IN (""" + ",".join(["%s"] * len(symbols)) + ")",
                ("2026-07-01", "2026-08-10", *symbols))
            daily = pd.DataFrame(cursor.fetchall())
    daily["signal_date"] = daily.signal_date.astype(str)
    daily["symbol6"] = daily.symbol6.astype(str).str.zfill(6)
    for field in ("daily_preclose", "daily_close", "is_limit_price"):
        daily[field] = pd.to_numeric(daily[field], errors="coerce")
    checked = selected.merge(daily, on=["signal_date", "symbol6"],
                             how="left", validate="one_to_one")
    if checked.daily_close.isna().any():
        raise ValueError("Selected stock-day missing from daily price table")
    checked["entry_at_daily_limit_close"] = (
        checked.is_limit_price.eq(1) &
        checked.daily_close.gt(checked.daily_preclose) &
        checked.signal_price.sub(checked.daily_close).abs().le(.001))
    bar_values = {}
    cache = Path("outputs/stock_automl/sina_15m_rolling/cache")
    for symbol, group in checked.groupby("symbol6"):
        with gzip.open(cache / f"{symbol}.json.gz", "rt", encoding="utf8") as source:
            bars = json.load(source)
        dates = set(group.signal_date)
        for bar in bars:
            stamp = bar.get("day", "")
            if stamp[11:16] == "14:45" and stamp[:10] in dates:
                bar_values[(stamp[:10], symbol)] = {
                    "bar_open": float(bar["open"]),
                    "bar_high": float(bar["high"]),
                    "bar_low": float(bar["low"]),
                    "bar_close": float(bar["close"]),
                    "bar_volume": float(bar["volume"]),
                }
    if len(bar_values) != len(checked):
        raise ValueError("Missing 14:45 bar for a selected stock-day")
    for field in ("bar_open", "bar_high", "bar_low", "bar_close", "bar_volume"):
        checked[field] = [bar_values[(row.signal_date, row.symbol6)][field]
                          for row in checked.itertuples()]
    checked["entry_at_flat_five_limit"] = [
        at_nominal_five_limit(row.daily_preclose, row.signal_price) and
        max(row.bar_open, row.bar_high, row.bar_low, row.bar_close) -
        min(row.bar_open, row.bar_high, row.bar_low, row.bar_close) <= .001
        for row in checked.itertuples()]
    checked.to_csv(output / "selected_limit_close_audit.csv", index=False)
    top1 = checked[checked.selection_rank.eq(1)]
    july_selected = checked[checked.signal_date.between("2026-07-01", "2026-07-31")]
    july_selected_not_limit = july_selected[
        ~july_selected.entry_at_daily_limit_close]
    july = top1[top1.signal_date.between("2026-07-01", "2026-07-31")]
    july_not_limit = july[~july.entry_at_daily_limit_close]
    summary = {
        "definition": "Retrospective diagnostic: daily is_limit_price=1, daily close above preclose, and signal 14:45 close equals daily limit close within 0.001 CNY.",
        "selected_trades": len(checked),
        "selected_at_limit_close": int(checked.entry_at_daily_limit_close.sum()),
        "selected_at_flat_five_limit": int(checked.entry_at_flat_five_limit.sum()),
        "limit_close_all_flat_five": bool(checked.loc[
            checked.entry_at_daily_limit_close, "entry_at_flat_five_limit"].all()),
        "july_top1_trades": len(july),
        "july_top1_at_limit_close": int(july.entry_at_daily_limit_close.sum()),
        "july_top1_reported_mean_0945_pct": float(july.return_0945_pct.mean()),
        "july_top1_excluding_limit_close_trades": len(july_not_limit),
        "july_top1_excluding_limit_close_mean_0945_pct": float(
            july_not_limit.return_0945_pct.mean()),
        "july_top2_trades": len(july_selected),
        "july_top2_at_limit_close": int(
            july_selected.entry_at_daily_limit_close.sum()),
        "july_top2_reported_mean_0945_pct": float(
            july_selected.return_0945_pct.mean()),
        "july_top2_excluding_limit_close_trades": len(july_selected_not_limit),
        "july_top2_excluding_limit_close_mean_0945_pct": float(
            july_selected_not_limit.return_0945_pct.mean()),
        "flagged_july_top1": july[july.entry_at_daily_limit_close][
            ["signal_date", "symbol6", "name", "signal_price",
             "return_0945_pct"]].to_dict("records"),
        "caution": "This uses end-of-day data only as a retrospective fill-risk audit; it was not used to filter or rerank the 14:45 predictions. A limit-close flag does not prove an individual order could or could not fill.",
    }
    (output / "execution_audit_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path,
                        default=Path("/Volumes/ext/fin_agent/.env"))
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    run(args.env_file, args.output)
