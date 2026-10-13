"""Freeze six afternoon cuts and three morning alternatives for close→open research."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.mk_minute_archive import COMPACT_ROOT, COMPACT_ROOT_2025, day_file
from scripts.research_automl_close_open_objectives import DATA as OLD_DATA, SOURCES


DATA = Path("outputs/stock_automl/close_open_screen_sequence_targets_r1")
CUTS = ("14:30:00", "14:35:00", "14:40:00", "14:45:00", "14:50:00", "14:55:00")
CHANNELS = ("signal_return", "volume_ratio", "turnover_so_far_pct", "float_mv_100m_cny",
            "all_intraday_lows_above_ma", "open_gap", "from_open", "prior5_return",
            "relative_turnover", "distance_ma5")
AFTERNOON = tuple(pd.date_range("13:01", "14:55", freq="min").strftime("%H:%M:%S"))
FULL_TO_DECISION = tuple(pd.date_range("09:31", periods=120, freq="min").strftime("%H:%M:%S")) + AFTERNOON
MORNING = tuple(pd.date_range("09:31", periods=10, freq="min").strftime("%H:%M:%S"))
SOURCE_COLS = ("signal_date", "next_date", "symbol6", "signal_price", "minute_volume_shares",
               "avg_volume5_shares", "turnover_so_far_pct", "ma5", "ma10", "ma20")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_metadata() -> pd.DataFrame:
    paths = pd.read_csv(OLD_DATA / "candidate_paths.csv.gz", dtype={"symbol6": str, "period": str})
    raw = []
    for period, path in SOURCES.items():
        frame = pd.read_csv(path, dtype={"symbol6": str}, usecols=list(SOURCE_COLS))
        raw.append(frame.assign(period=period))
    original = pd.concat(raw, ignore_index=True)
    rows = paths.merge(original, on=["period", "signal_date", "next_date", "symbol6"],
                       how="left", suffixes=("", "_source"), validate="one_to_one")
    if rows.signal_price.isna().any() or rows.duplicated(["period", "signal_date", "symbol6"]).any():
        raise ValueError("Frozen candidates and raw metadata do not align")
    if not np.allclose(rows.turnover_so_far_pct, rows.turnover_so_far_pct_source):
        raise ValueError("Turnover disagrees with previous frozen panel")
    rows = rows.drop(columns="turnover_so_far_pct_source")
    return rows


def local_bars(day: str) -> tuple[pd.DataFrame, str]:
    root = COMPACT_ROOT_2025 if day.startswith("2025-") else COMPACT_ROOT
    path = day_file(root, day)
    bars = pd.read_csv(path, usecols=["time", "symbol", "open", "high", "low", "close", "volume"],
                       dtype={"symbol": str})
    if not bars.time.str[:10].eq(day).all():
        raise ValueError(f"Wrong raw minute day: {day}")
    return bars, str(path)


def db_bars(db, day: str, symbols: set[str]) -> pd.DataFrame:
    placeholders = ",".join(["%s"] * len(symbols))
    ordered = sorted(symbols)
    sql = ("SELECT stk_code,bar_end_time,open_price,high_price,low_price,latest_price,volume,"
           "is_finalized,is_fallback,source_snapshot_time "
           "FROM aiia_stock_realtime_minute_snapshot_full "
           "WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1 "
           "AND bar_end_time BETWEEN %s AND %s "
           f"AND stk_code IN ({placeholders}) ORDER BY stk_code,bar_end_time")
    rows = query(db, sql, (day, f"{day} 09:31:00", f"{day} 14:56:00", *ordered))
    if rows.empty:
        return pd.DataFrame(columns=["time", "symbol", "open", "high", "low", "close", "volume"])
    if not (rows.is_finalized.eq(1).all() and rows.is_fallback.eq(0).all() and
            pd.to_datetime(rows.source_snapshot_time).eq(pd.to_datetime(rows.bar_end_time)).all()):
        raise ValueError(f"Database minute version not finalized/exact for {day}")
    result = pd.DataFrame({"time": pd.to_datetime(rows.bar_end_time).dt.strftime("%Y-%m-%d %H:%M:%S"),
                           "symbol": rows.stk_code.astype(str).str[:6],
                           "open": pd.to_numeric(rows.open_price, errors="raise"),
                           "high": pd.to_numeric(rows.high_price, errors="raise"),
                           "low": pd.to_numeric(rows.low_price, errors="raise"),
                           "close": pd.to_numeric(rows.latest_price, errors="raise"),
                           # This DB archive stores volume in 100-share lots;
                           # the frozen candidate and local MK archives use shares.
                           "volume": pd.to_numeric(rows.volume, errors="raise") * 100})
    return result


def morning_labels(stock: pd.DataFrame) -> dict | None:
    m = stock[stock.clock.between("09:31:00", "09:40:00")].sort_values("clock")
    if tuple(m.clock) != MORNING or not np.isfinite(m[["open", "high", "low", "close"]].to_numpy()).all():
        return None
    highs = np.sort(m.high.to_numpy(float))
    return {"max_high": float(highs[-1]), "second_high": float(highs[-2]),
            "open_40": float(m.open.iloc[-1]), "close_40_raw": float(m.close.iloc[-1]),
            "morning_10_complete": True}


def afternoon_matrix(stock: pd.DataFrame, candidate: pd.Series) -> dict | None:
    s = stock[stock.clock.le("14:55:00")].sort_values("clock").copy()
    if tuple(s.clock) != FULL_TO_DECISION:
        return None
    if (not np.isfinite(s[["open", "high", "low", "close", "volume"]].to_numpy()).all() or
            not s[["open", "high", "low", "close"]].gt(0).all().all() or
            not s.volume.ge(0).all() or
            not s.high.ge(s[["open", "close"]].max(axis=1)).all() or
            not s.low.le(s[["open", "close"]].min(axis=1)).all()):
        return None
    if candidate.avg_volume5_shares <= 0 or candidate.turnover_so_far_pct <= 0:
        return None
    prior_close = candidate.signal_price / (1 + candidate.signal_return)
    today_open = prior_close * (1 + candidate.open_gap)
    float_share = candidate.minute_volume_shares / (candidate.turnover_so_far_pct / 100)
    prior_turnover = candidate.turnover_so_far_pct / candidate.relative_turnover
    if min(prior_close, today_open, float_share, prior_turnover, candidate.ma5) <= 0:
        return None
    s["cum_volume"] = s.volume.cumsum()
    s["cum_low"] = s.low.cummin()
    captured = s[s.clock.isin(CUTS)].set_index("clock")
    if tuple(sorted(captured.index)) != CUTS:
        return None
    entry = stock.loc[stock.clock.eq("14:56:00"), "open"]
    entry_price = float(entry.iloc[0]) if len(entry) == 1 else np.nan
    out = {"entry_1456": entry_price if np.isfinite(entry_price) and entry_price > 0 else np.nan}
    highest_ma = max(candidate.ma5, candidate.ma10, candidate.ma20)
    for j, clock in enumerate(CUTS):
        row = captured.loc[clock]
        count = 210 + j * 5
        v = float(row.cum_volume)
        turnover = v / float_share * 100
        values = (row.close/prior_close - 1, v*240/(count*candidate.avg_volume5_shares),
                  turnover, candidate.float_mv_100m_cny,
                  float(row.cum_low > highest_ma), candidate.open_gap,
                  row.close/today_open - 1, candidate.prior5_return,
                  turnover/prior_turnover, row.close/candidate.ma5 - 1)
        out.update({f"{name}_{clock[:5].replace(':', '')}": float(value)
                    for name, value in zip(CHANNELS, values)})
    out["still_in_band_1455"] = .03 <= out["signal_return_1455"] <= .06
    return out


def day_extract(bars: pd.DataFrame, day: str, signal: pd.DataFrame,
                next_rows: pd.DataFrame) -> tuple[list[dict], list[dict]]:
    wanted = set(signal.symbol6) | set(next_rows.symbol6)
    bars = bars.loc[bars.symbol.str[:6].isin(wanted)].copy()
    bars["symbol6"] = bars.symbol.str[:6]
    bars["clock"] = bars.time.str[-8:]
    grouped = {symbol: data for symbol, data in bars.groupby("symbol6", sort=False)}
    afternoon, morning = [], []
    for item in signal.itertuples(index=False):
        stock = grouped.get(item.symbol6)
        result = afternoon_matrix(stock, item) if stock is not None else None
        afternoon.append({"period": item.period, "signal_date": day, "symbol6": item.symbol6,
                          "sequence_complete": result is not None, **(result or {})})
    for item in next_rows.itertuples(index=False):
        stock = grouped.get(item.symbol6)
        result = morning_labels(stock) if stock is not None else None
        morning.append({"period": item.period, "signal_date": item.signal_date,
                        "symbol6": item.symbol6, "next_date": day,
                        "morning_10_complete": result is not None, **(result or {})})
    return afternoon, morning


def build() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    rows = load_metadata()
    signal_dates = {day: frame for day, frame in rows.groupby("signal_date")}
    next_dates = {day: frame for day, frame in rows.groupby("next_date")}
    days = sorted(set(signal_dates) | set(next_dates))
    afternoon, morning, audit = [], [], []
    db = None
    try:
        for i, day in enumerate(days, 1):
            signal = signal_dates.get(day, rows.iloc[:0])
            next_rows = next_dates.get(day, rows.iloc[:0])
            symbols = set(signal.symbol6) | set(next_rows.symbol6)
            if day >= "2026-08-01":
                if db is None:
                    db = db_connection()
                bars, source = db_bars(db, day, symbols), "read-only minute database"
            else:
                bars, source = local_bars(day)
            a, m = day_extract(bars, day, signal, next_rows)
            afternoon.extend(a)
            morning.extend(m)
            audit.append({"day": day, "source": source, "signal_candidates": len(signal),
                          "sequence_complete": sum(x["sequence_complete"] for x in a),
                          "next_candidates": len(next_rows),
                          "morning_complete": sum(x["morning_10_complete"] for x in m)})
            if i % 20 == 0 or i == len(days):
                print(f"Minute extract {i}/{len(days)}: {day}", flush=True)
    finally:
        if db is not None:
            db.close()
    a, m = pd.DataFrame(afternoon), pd.DataFrame(morning)
    if a.duplicated(["period", "signal_date", "symbol6"]).any() or m.duplicated(
            ["period", "signal_date", "symbol6"]).any():
        raise ValueError("Duplicate minute derived candidate")
    result = rows.merge(a, on=["period", "signal_date", "symbol6"], validate="one_to_one")
    result = result.merge(m, on=["period", "signal_date", "symbol6", "next_date"],
                          how="left", validate="one_to_one")
    if len(result) != len(rows):
        raise ValueError("Minute join dropped frozen candidates")
    result["morning_10_complete"] = result.morning_10_complete.fillna(False).astype(bool)
    compare = result[result.sequence_complete]
    cut = "1440"
    for name in ("signal_return", "volume_ratio", "turnover_so_far_pct",
                 "all_intraday_lows_above_ma", "from_open", "relative_turnover", "distance_ma5"):
        if not np.allclose(compare[name], compare[f"{name}_{cut}"], atol=1e-5, rtol=1e-4):
            bad = compare.loc[~np.isclose(compare[name], compare[f"{name}_{cut}"],
                                           atol=1e-5, rtol=1e-4)]
            raise ValueError(f"14:40 reconstructed {name} mismatches frozen panel: {len(bad)}")
    same = result[result.morning_10_complete & result.actual.notna()]
    if not np.allclose(same.close_40_raw/same.signal_price-1, same.actual,
                       atol=1e-5, rtol=1e-4):
        raise ValueError("Raw 09:40 close disagrees with frozen realized return")
    result.to_csv(DATA / "candidate_sequence_targets.csv.gz", index=False)
    pd.DataFrame(audit).to_csv(DATA / "minute_coverage.csv", index=False)
    manifest = {"raw_candidates": {p: {"path": str(path), "sha256": sha(path)}
                                    for p, path in SOURCES.items()},
                "frozen_path_panel": {"path": str(OLD_DATA / "candidate_paths.csv.gz"),
                                      "sha256": sha(OLD_DATA / "candidate_paths.csv.gz")},
                "source_audit": "minute_coverage.csv; local files or read-only finalized exact-timestamp DB",
                "rows": len(result), "complete_sequences": int(result.sequence_complete.sum()),
                "complete_mornings": int(result.morning_10_complete.sum()),
                "script_sha256": sha(Path(__file__)),
                "output_sha256": sha(DATA / "candidate_sequence_targets.csv.gz")}
    (DATA / "build_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print({k: manifest[k] for k in ("rows", "complete_sequences", "complete_mornings")})


if __name__ == "__main__":
    build()
