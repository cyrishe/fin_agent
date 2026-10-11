"""Build the frozen 14:40 seven-factor cohort directly from supplied raw 1m CSVs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.benchmark_automl_1440_inference import FEATURES, db_connection
from scripts.build_automl_tail_standard import prior_profile


START, END = "2026-05-06", "2026-07-31"
ARCHIVE = Path("outputs/stock_automl/mk_archive/mk/none")
OUT = Path("outputs/stock_automl/mk_1m_study")


def summarize_day(path: Path) -> tuple[pd.DataFrame, dict]:
    day = path.stem
    bars = pd.read_csv(path, usecols=["time", "symbol", "open", "high", "low",
                                      "close", "volume"], dtype={"symbol": str})
    if bars.empty or not bars.time.str[:10].eq(day).all():
        raise ValueError(f"Wrong timestamp date in {path}")
    if not bars.symbol.str.endswith((".SH", ".SZ")).all():
        raise ValueError(f"Unexpected exchange in {path}")
    clock = bars.time.str[-8:]
    bad = (~bars[["open", "high", "low", "close"]].gt(0).all(axis=1) |
           ~bars.volume.ge(0) |
           ~bars.high.ge(bars[["open", "close"]].max(axis=1)) |
           ~bars.low.le(bars[["open", "close"]].min(axis=1)))
    bad_required_symbols = set(bars.loc[bad & clock.le("14:40:00"), "symbol"])
    audit = {"date": day, "invalid_bars_anytime": int(bad.sum()),
             "invalid_required_stock_days": 0}
    # The signal is decided at 14:40. Later bars must not decide whether a
    # stock-day is eligible, even when replaying a complete historical file.
    visible = bars.loc[clock.le("14:40:00")].copy()
    visible_clock = visible.time.str[-8:]
    expected_clock = set(pd.date_range("09:31", periods=120, freq="min").strftime("%H:%M:%S"))
    expected_clock.update(pd.date_range("13:01", periods=100, freq="min").strftime("%H:%M:%S"))
    counts = visible.groupby("symbol", sort=False).size()
    duplicates = set(visible.loc[
        visible[["symbol", "time"]].duplicated(keep=False), "symbol"])
    unexpected = set(visible.loc[~visible_clock.isin(expected_clock), "symbol"])
    incomplete = set(counts.index[~counts.eq(220)]) | duplicates | unexpected
    excluded = bad_required_symbols | incomplete
    audit["invalid_required_stock_days"] = len(excluded)
    visible = visible.loc[~visible.symbol.isin(excluded)].copy()
    visible_clock = visible.time.str[-8:]
    counts = visible.groupby("symbol", sort=False).size()
    if visible.empty:
        raise ValueError(f"No complete 14:40 stock-day in {path}")
    signal = visible.loc[visible_clock.eq("14:40:00"), ["symbol", "close"]].rename(
        columns={"close": "signal_price"})
    intraday = visible.groupby("symbol", sort=False).agg(
        minute_bars=("low", "size"), minute_volume_shares=("volume", "sum"),
        min_low_so_far=("low", "min")).reset_index()
    morning = visible.loc[visible_clock.le("09:40:00"),
                          ["symbol", "time", "high", "close", "volume"]]
    morning_count = morning.groupby("symbol", sort=False).size()
    if (not intraday.minute_bars.eq(220).all() or not morning_count.eq(10).all()
            or len(signal) != len(counts)):
        raise ValueError(f"Wrong 14:40 or morning coverage in {path}")
    ordered_highs = morning.sort_values(["symbol", "high"], ascending=[True, False])
    second = ordered_highs.groupby("symbol", sort=False).nth(1).reset_index()[
        ["symbol", "high"]].rename(columns={"high": "morning_second_high"})
    close40 = morning.loc[morning.time.str.endswith("09:40:00"),
                          ["symbol", "close"]].rename(columns={"close": "morning_close_40"})
    morning_closes = morning.assign(slot=morning.time.str[-8:-3]).pivot(
        index="symbol", columns="slot", values="close")
    morning_closes = morning_closes.rename(
        columns={f"09:{minute:02d}": f"morning_close_{minute:02d}"
                 for minute in range(31, 40)})[
                     [f"morning_close_{minute:02d}" for minute in range(31, 40)]].reset_index()
    volume5 = morning.loc[morning.time.str[-8:].le("09:35:00")].groupby(
        "symbol", sort=False).volume.sum().rename("morning_volume5_shares").reset_index()
    merged = signal.merge(intraday, on="symbol", validate="one_to_one")
    for part in (second, close40, volume5, morning_closes):
        merged = merged.merge(part, on="symbol", validate="one_to_one")
    if len(merged) != len(counts):
        raise ValueError(f"Lost stock keys in {path}")
    merged["signal_date"] = day
    merged["symbol6"] = merged.symbol.str[:6]
    return merged.drop(columns="symbol"), audit


def load_daily_profile(env_file: Path, start: str = START,
                       end: str = END) -> tuple[pd.DataFrame, pd.DataFrame, list, dict]:
    history_start = (pd.Timestamp(start) - pd.Timedelta(days=70)).strftime("%Y-%m-%d")
    with db_connection(env_file) as db:
        with db.cursor() as cursor:
            cursor.execute("""SELECT trade_date AS date,LEFT(stk_code,6) AS symbol6,
                preclose,close,adjpreclose,adjclose,volume,turn_ratio
                FROM kcrp_stock_price WHERE trade_date BETWEEN %s AND %s""",
                           (history_start, end))
            daily = pd.DataFrame(cursor.fetchall())
            cursor.execute("SELECT LEFT(stk_code,6) AS symbol6,stk_name AS name "
                           "FROM kcrp_stock_baseinfo")
            names = pd.DataFrame(cursor.fetchall()).drop_duplicates("symbol6", keep=False)
            cursor.execute("""SELECT trade_date AS date,LEFT(stk_code,6) AS symbol6,float_mv
                FROM kcrp_stock_pricevaluate WHERE trade_date BETWEEN %s AND %s""",
                           (history_start, end))
            reported = pd.DataFrame(cursor.fetchall())
    daily["date"] = pd.to_datetime(daily.date)
    daily["symbol6"] = daily.symbol6.astype(str).str.zfill(6)
    daily = daily.drop_duplicates(["date", "symbol6"], keep=False)
    for col in ("preclose", "close", "adjpreclose", "adjclose", "volume", "turn_ratio"):
        daily[col] = pd.to_numeric(daily[col], errors="coerce")
    daily["float_share"] = daily.volume / (daily.turn_ratio / 100)
    daily["float_mv"] = daily.float_share * daily.close
    valid = (daily.turn_ratio.gt(0) & daily.volume.gt(0) & daily.close.gt(0) &
             np.isfinite(daily.float_share) & np.isfinite(daily.float_mv))
    value = daily.loc[valid, ["date", "symbol6", "float_share", "float_mv"]].copy()
    profile, calendar = prior_profile(daily, value)
    reported["date"] = pd.to_datetime(reported.date)
    reported["symbol6"] = reported.symbol6.astype(str).str.zfill(6)
    reported["float_mv"] = pd.to_numeric(reported.float_mv, errors="coerce")
    audit = daily.loc[valid, ["date", "symbol6", "float_mv"]].merge(
        reported, on=["date", "symbol6"], how="inner", suffixes=("_proxy", "_reported"))
    audit = audit[audit.float_mv_reported.gt(0)]
    ratio = audit.float_mv_proxy / audit.float_mv_reported
    proxy_audit = {"overlap": len(audit), "within_1pct": float(ratio.between(.99, 1.01).mean()),
                   "within_5pct": float(ratio.between(.95, 1.05).mean()),
                   "p95_abs_relative_error": float((ratio - 1).abs().quantile(.95))}
    return daily.merge(names, on="symbol6", how="left", validate="many_to_one"), profile, calendar, proxy_audit


def build(archive: Path | list[Path], env_file: Path, output: Path,
          reuse_snapshots: bool = False) -> dict:
    archives = [archive] if isinstance(archive, Path) else archive
    files = sorted((file for root in archives for file in root.glob("2026-*.csv")),
                   key=lambda file: file.stem)
    if not files or len({file.stem for file in files}) != len(files):
        raise ValueError("Missing or duplicate unadjusted minute days")
    start, end = files[0].stem, files[-1].stem
    daily, profile, calendar, proxy_audit = load_daily_profile(env_file, start, end)
    expected = [str(day)[:10] for day in calendar
                if pd.Timestamp(start) <= day <= pd.Timestamp(end)]
    if [file.stem for file in files] != expected:
        raise ValueError("Minute archive days differ from the daily trading calendar")
    next_date = {pd.Timestamp(a): pd.Timestamp(b)
                 for a, b in zip(calendar[:-1], calendar[1:])}
    output.mkdir(parents=True, exist_ok=True)
    if reuse_snapshots:
        snapshots = pd.read_csv(output / "minute_daily_snapshots.csv.gz",
                                dtype={"symbol6": str})
        minute_audits = pd.read_csv(output / "minute_source_audit.csv").to_dict("records")
        if "morning_close_40_x" in snapshots:
            if not np.allclose(snapshots.morning_close_40_x,
                               snapshots.morning_close_40_y):
                raise ValueError("Conflicting duplicated 09:40 close columns")
            snapshots = snapshots.rename(columns={"morning_close_40_x": "morning_close_40"})
            snapshots = snapshots.drop(columns="morning_close_40_y")
    else:
        snapshots, minute_audits = [], []
        for file in files:
            frame, audit = summarize_day(file)
            snapshots.append(frame)
            minute_audits.append(audit)
            print(f"summarized {file.stem}", flush=True)
        snapshots = pd.concat(snapshots, ignore_index=True)
        snapshots.to_csv(output / "minute_daily_snapshots.csv.gz", index=False,
                         compression="gzip")
        pd.DataFrame(minute_audits).to_csv(output / "minute_source_audit.csv", index=False)
    snapshots["signal_date"] = pd.to_datetime(snapshots.signal_date)
    if sorted(snapshots.signal_date.dt.strftime("%Y-%m-%d").unique()) != expected:
        raise ValueError("Saved minute snapshots do not match the archive calendar")
    signals = snapshots.merge(
        daily[["date", "symbol6", "preclose", "name"]].rename(
            columns={"date": "signal_date", "preclose": "t_reference_preclose"}),
        on=["signal_date", "symbol6"], validate="one_to_one")
    signals = signals.merge(profile, on=["signal_date", "symbol6"], how="left",
                            validate="one_to_one")
    signals["next_date"] = signals.signal_date.map(next_date)
    morning = snapshots[["signal_date", "symbol6", "morning_second_high",
                         "morning_close_40", "morning_volume5_shares",
                         *(f"morning_close_{minute:02d}" for minute in range(31, 40))]].rename(
        columns={"signal_date": "next_date", "morning_second_high": "second_high",
                 "morning_close_40": "close_40", "morning_volume5_shares": "next_volume5",
                 **{f"morning_close_{minute:02d}": f"next_close_{minute:02d}"
                    for minute in range(31, 40)}})
    morning["next_close_40"] = morning.close_40
    signals = signals.merge(morning, on=["next_date", "symbol6"], how="left",
                            validate="one_to_one")
    signals["signal_return"] = signals.signal_price / signals.t_reference_preclose - 1
    signals["volume_ratio"] = (signals.minute_volume_shares * 240 /
                               (220 * signals.avg_volume5_shares))
    signals["turnover_so_far_pct"] = signals.minute_volume_shares / signals.float_share * 100
    signals["float_mv_100m_cny"] = signals.float_mv / 1e8
    for window in (5, 10, 20):
        signals[f"ma{window}"] = (signals[f"ma{window}_adj"] *
                                  signals.t_reference_preclose / signals.prior_adjclose)
    signals["ma_bull_5_10_20"] = (signals.ma5.gt(signals.ma10) &
                                   signals.ma10.gt(signals.ma20)).astype("Int8")
    highest_ma = signals[["ma5", "ma10", "ma20"]].max(axis=1)
    signals["all_intraday_lows_above_ma"] = signals.min_low_so_far.gt(highest_ma).astype("Int8")
    keep = (signals.signal_return.between(.03, .06, inclusive="both") &
            signals.t_reference_preclose.gt(0) & signals.signal_price.gt(0) &
            signals.minute_volume_shares.gt(0) & signals.min_low_so_far.gt(0) &
            signals.history_complete.eq(True) & signals.adj_price_break_20d.eq(False) &
            signals.value_complete.eq(True) & signals[list(FEATURES)].notna().all(axis=1))
    candidates = signals.loc[keep].copy()
    for col in ("volume_4of5_increasing", "ma_bull_5_10_20",
                "all_intraday_lows_above_ma"):
        candidates[col] = candidates[col].astype(int)
    candidates.loc[candidates.next_volume5.le(0), ["second_high", "close_40"]] = np.nan
    candidates["label_complete"] = candidates.second_high.notna() & candidates.close_40.notna()
    if candidates.duplicated(["signal_date", "symbol6"]).any():
        raise ValueError("Duplicate candidate stock-day")
    candidates.sort_values(["signal_date", "symbol6"]).to_csv(
        output / "trainable_candidates.csv.gz", index=False, compression="gzip")
    coverage = candidates.groupby("signal_date").agg(
        candidates=("symbol6", "size"), labeled=("label_complete", "sum"))
    coverage.to_csv(output / "candidate_coverage.csv")
    summary = {"archive": str(archives[0]) if len(archives) == 1 else
               [str(root) for root in archives],
               "signal_days": int(candidates.signal_date.nunique()),
               "first_signal_date": str(candidates.signal_date.min())[:10],
               "last_signal_date": str(candidates.signal_date.max())[:10],
               "candidates": len(candidates), "labeled": int(candidates.label_complete.sum()),
               "invalid_bars_anytime": sum(x["invalid_bars_anytime"] for x in minute_audits),
               "invalid_required_stock_days": sum(x["invalid_required_stock_days"]
                                                  for x in minute_audits),
               "proxy_float_mv": proxy_audit,
               "feature_source": "unadjusted raw 1m; T-1 daily/derived float share and market value",
               "universe": "active SH/SZ only; no ST name filter or next-day candidate filter"}
    (output / "build_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, action="append",
                        help="Unadjusted minute directory; repeat for adjoining archives")
    parser.add_argument("--env-file", type=Path, default=Path("/Volumes/ext/fin_agent/.env"))
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--reuse-snapshots", action="store_true")
    args = parser.parse_args()
    print(json.dumps(build(args.archive or ARCHIVE, args.env_file, args.output,
                           args.reuse_snapshots), ensure_ascii=False, indent=2))
