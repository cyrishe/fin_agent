"""Top-3 C1430-A review with a hypothetical D1 14:40 minute-close entry."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.experiment_automl_limit_rebound_1430_r7 import MINUTE_ROOT, OUT
from scripts.experiment_automl_limit_rebound_d0_open_r5 import cap_price, normal_limit_rate
from scripts.experiment_automl_limit_rebound_st_size_r4 import OUT as SIZE_OUT
from scripts.experiment_automl_limit_rebound_2y import SOURCE
from scripts.mk_minute_archive import day_file


FIELDS = (
    "split", "symbol", "d0_date", "d1_date", "day_rank", "score",
    "d0_volume_10k", "d0_turnover_pct", "d1_1440_volume_10k",
    "d1_1440_amount_100m", "d1_1440_turnover_pct_est",
    "d1_1440_market_cap_100m_est", "market_cap_basis", "market_cap_age_days",
    "buy_1440", "at_cap_1440", "d1_close_reboard", "d2_d4_reboard",
    "sell_same_day_return_pct", "sell_d1_return_pct", "sell_d2_return_pct",
    "sell_d3_return_pct", "sell_d4_return_pct", "sell_d5_return_pct",
)


def daily_source(symbols: set[str]) -> tuple[pd.DataFrame, list[str]]:
    parts = []
    dates = set()
    for month in pd.period_range("2025-12", "2026-08", freq="M"):
        path = SOURCE / "price_months" / f"price_{month}.csv.gz"
        raw = pd.read_csv(path, usecols=["trade_date", "stk_code", "preclose", "close",
                                         "volume", "turn_ratio", "is_limit_price"],
                          dtype={"trade_date": str, "stk_code": str})
        raw["trade_date"] = pd.to_datetime(raw.trade_date).dt.strftime("%Y-%m-%d")
        dates.update(raw.trade_date.unique())
        raw["symbol"] = raw.stk_code.str[:6]
        parts.append(raw[raw.symbol.isin(symbols)].copy())
    daily = pd.concat(parts, ignore_index=True)
    if daily.duplicated(["symbol", "trade_date"]).any():
        raise ValueError("Duplicate daily stock-date")
    return daily, sorted(dates)


def minute_1440(picks: pd.DataFrame) -> pd.DataFrame:
    all_cache = OUT / "review_all_minute_1440.csv.gz"
    if all_cache.exists():
        all_rows = pd.read_csv(all_cache, dtype={"symbol": str, "d1_date": str})
        wanted = picks[["symbol", "d1_date"]].drop_duplicates()
        result = wanted.merge(all_rows, on=["symbol", "d1_date"], validate="one_to_one")
        if len(result) != len(wanted):
            raise ValueError("Missing 14:40 minute path for an eligible Top3 row")
        return result
    cache = OUT / "review_top3_minute_1440.csv.gz"
    if cache.exists():
        return pd.read_csv(cache, dtype={"symbol": str, "d1_date": str})
    by_day = picks.groupby("d1_date").symbol.apply(set).to_dict()
    rows = []
    expected = [t.strftime("%H:%M:%S") for t in pd.date_range("09:31", "11:30", freq="min")]
    expected += [t.strftime("%H:%M:%S") for t in pd.date_range("13:01", "14:40", freq="min")]
    for day, symbols in sorted(by_day.items()):
        path = day_file(MINUTE_ROOT, day)
        found = []
        for chunk in pd.read_csv(path, dtype={"symbol": str}, chunksize=200_000):
            x = chunk[chunk.symbol.str[:6].isin(symbols) & chunk.time.str[-8:].le("14:40:00")]
            if not x.empty:
                found.append(x)
        if not found:
            continue
        for full_symbol, bars in pd.concat(found).groupby("symbol"):
            symbol = str(full_symbol)[:6]
            bars = bars.sort_values("time")
            if bars.time.str[-8:].tolist() != expected:
                continue
            values = bars[["close", "volume", "turnover"]].apply(pd.to_numeric, errors="coerce")
            if values.isna().any().any() or values.close.le(0).any() or values.volume.lt(0).any() or values.turnover.lt(0).any():
                continue
            rows.append({"symbol": symbol, "d1_date": day,
                         "buy_1440": float(values.close.iloc[-1]),
                         "d1_1440_volume": float(values.volume.sum()),
                         "d1_1440_amount": float(values.turnover.sum())})
    result = pd.DataFrame(rows)
    result.to_csv(cache, index=False, compression="gzip")
    return result


def d0_size(picks: pd.DataFrame, out: Path) -> pd.DataFrame:
    path = out / "review_top3_d0_size.csv.gz"
    if path.exists():
        return pd.read_csv(path, dtype={"symbol": str, "d0_date": str})
    wanted = picks[["symbol", "d0_date", "d0_code"]].drop_duplicates()
    rows = []
    pairs = list(wanted[["d0_date", "d0_code"]].itertuples(index=False, name=None))
    with db_connection() as conn:
        for start in range(0, len(pairs), 100):
            block = pairs[start:start + 100]
            placeholders = ",".join(["(%s,%s)"] * len(block))
            sql = ("SELECT trade_date,stk_code,total_mv,total_share "
                   "FROM kcrp_stock_pricevaluate WHERE (trade_date,stk_code) IN (" + placeholders + ")")
            raw = query(conn, sql, tuple(value for pair in block for value in pair))
            conn.rollback()
            rows.append(raw)
    result = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    if not result.empty:
        result["d0_date"] = result.trade_date.astype(str)
        result["symbol"] = result.stk_code.str[:6]
        result = result[["symbol", "d0_date", "total_mv", "total_share"]].copy()
        if result.duplicated(["symbol", "d0_date"]).any():
            raise ValueError("Duplicate D0 market cap stock-date")
    result.to_csv(path, index=False, compression="gzip")
    return result


def prior_shares(picks: pd.DataFrame) -> pd.DataFrame:
    root = SIZE_OUT / "share_months"
    history = pd.concat((pd.read_csv(path, dtype={"symbol": str, "trade_date": str})
                         for path in sorted(root.glob("total_share_*.csv.gz"))), ignore_index=True)
    history = history[history.symbol.isin(set(picks.symbol))].copy()
    history["trade_date"] = pd.to_datetime(history.trade_date)
    x = picks[["symbol", "d0_date"]].drop_duplicates().copy()
    x["asof"] = pd.to_datetime(x.d0_date)
    merged = pd.merge_asof(x.sort_values("asof"), history.sort_values("trade_date"),
                           left_on="asof", right_on="trade_date", by="symbol", direction="backward")
    return merged[["symbol", "d0_date", "total_share", "trade_date"]].rename(
        columns={"total_share": "prior_total_share", "trade_date": "prior_share_date"})


def main(out: Path = OUT) -> dict:
    selection = out / "review_eligible_top3_1440.csv.gz"
    if not selection.exists():
        raise FileNotFoundError("Run 14:40 full-pool execution audit before trade review")
    picks = pd.read_csv(selection, dtype={"symbol": str, "d0_date": str, "d1_date": str})
    picks = picks.drop(columns=["buy_1440"])
    assert picks.groupby(["split", "d1_date"]).size().eq(3).all()
    daily, calendar = daily_source(set(picks.symbol))
    day_i = {day: i for i, day in enumerate(calendar)}
    d0 = daily.rename(columns={"trade_date": "d0_date", "stk_code": "d0_code",
                               "close": "d0_close", "volume": "d0_volume",
                               "turn_ratio": "d0_turnover_pct"})
    picks = picks.merge(d0[["symbol", "d0_date", "d0_code", "d0_close", "d0_volume",
                            "d0_turnover_pct"]], on=["symbol", "d0_date"], validate="many_to_one")
    d1 = daily.rename(columns={"trade_date": "d1_date", "preclose": "d1_preclose"})
    picks = picks.merge(d1[["symbol", "d1_date", "d1_preclose"]],
                        on=["symbol", "d1_date"], validate="many_to_one")
    minute = minute_1440(picks)
    picks = picks.merge(minute, on=["symbol", "d1_date"], how="left", validate="one_to_one")
    picks = picks.merge(d0_size(picks, out), on=["symbol", "d0_date"],
                        how="left", validate="many_to_one")
    picks = picks.merge(prior_shares(picks), on=["symbol", "d0_date"],
                        how="left", validate="many_to_one")
    picks["total_mv"] = pd.to_numeric(picks.total_mv, errors="coerce")
    picks["total_share"] = pd.to_numeric(picks.total_share, errors="coerce")
    picks["prior_total_share"] = pd.to_numeric(picks.prior_total_share, errors="coerce")
    direct = picks.total_mv.gt(0)
    same_day_share = picks.total_share.gt(0)
    picks["market_cap_basis"] = np.select(
        [direct, same_day_share, picks.prior_total_share.gt(0)],
        ["D0总市值折算", "D0总股本估算", "此前总股本估算"], default="缺失")
    capital = picks.total_mv.where(direct, picks.total_share.where(same_day_share,
                             picks.prior_total_share) * picks.d0_close)
    picks["d1_1440_market_cap_100m_est"] = capital * picks.buy_1440 / picks.d0_close / 1e8
    picks["market_cap_age_days"] = np.where(
        direct | same_day_share, 0,
        (pd.to_datetime(picks.d0_date) - pd.to_datetime(picks.prior_share_date)).dt.days)
    picks.loc[picks.market_cap_basis.eq("缺失"), "market_cap_age_days"] = np.nan
    picks["d0_volume_10k"] = picks.d0_volume / 1e4
    picks["d1_1440_volume_10k"] = picks.d1_1440_volume / 1e4
    picks["d1_1440_amount_100m"] = picks.d1_1440_amount / 1e8
    picks["d1_1440_turnover_pct_est"] = (picks.d1_1440_volume / picks.d0_volume * picks.d0_turnover_pct)
    picks.loc[picks.d0_volume.le(0) | picks.d0_turnover_pct.le(0), "d1_1440_turnover_pct_est"] = np.nan
    picks["d1_cap"] = [cap_price(preclose, normal_limit_rate(symbol))
                       for preclose, symbol in zip(picks.d1_preclose, picks.symbol)]
    picks["at_cap_1440"] = picks.buy_1440.ge(picks.d1_cap - .005).astype(int)
    picks["d1_close_reboard"] = picks.d1_up
    picks["d2_d4_reboard"] = picks.label_next3
    close = daily.set_index(["symbol", "trade_date"])["close"]
    for horizon in range(0, 6):
        dates = [calendar[day_i[day] + horizon] if day_i[day] + horizon < len(calendar)
                 else None for day in picks.d1_date]
        value = close.reindex(pd.MultiIndex.from_arrays([picks.symbol, dates])).to_numpy(dtype=float)
        column = "sell_same_day_return_pct" if horizon == 0 else f"sell_d{horizon}_return_pct"
        picks[column] = (value / picks.buy_1440 - 1) * 100
    review = picks[list(FIELDS)].sort_values(["split", "d1_date", "day_rank"])
    path = out / "review_top3_trade_1440.csv.gz"
    review.to_csv(path, index=False, compression="gzip", float_format="%.10g")
    result = {"experiment": "C1430-A", "entry": "D1 14:40 minute close, hypothetical",
              "rows": len(review), "minute_price_present": int(review.buy_1440.notna().sum()),
              "size_present": int(review.d1_1440_market_cap_100m_est.notna().sum()),
              "prior_share_estimates": int(review.market_cap_basis.eq("此前总股本估算").sum()),
              "prior_share_age_median_days": float(review.loc[review.market_cap_basis.eq("此前总股本估算"), "market_cap_age_days"].median()),
              "prior_share_age_max_days": int(review.market_cap_age_days.max()),
              "at_cap_1440": int(review.at_cap_1440.sum()),
              "sets": {}, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    for split, x in review.groupby("split"):
        result["sets"][split] = {"rows": len(x), "reboard_hits": int(x.d2_d4_reboard.sum()),
                                 "mean_return_pct": {"same_day": float(x.sell_same_day_return_pct.mean()),
                                                     **{f"D{h}": float(x[f"sell_d{h}_return_pct"].mean())
                                                        for h in range(1, 6)}}}
    (out / "review_top3_trade_1440_manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
