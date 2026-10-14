"""C1430-B: exact 14:40 gate and hypothetical close-to-close returns."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from scripts.experiment_automl_limit_rebound_1430_long import OUT, split
from scripts.experiment_automl_limit_rebound_d0_open_r5 import load_daily
from scripts.export_automl_limit_rebound_1430_trade_review import d0_size, prior_shares
from scripts.mk_minute_archive import COMPACT_ROOT, COMPACT_ROOT_2025, day_file


OLD_MINUTE = Path("outputs/stock_automl/limit_rebound_1430_r7_20261014/review_all_minute_1440.csv.gz")
KEY = ["symbol", "d0_date", "d1_date", "first_limit_date"]
EXPECTED = [t.strftime("%H:%M:%S") for t in pd.date_range("09:31", "11:30", freq="min")]
EXPECTED += [t.strftime("%H:%M:%S") for t in pd.date_range("13:01", "14:40", freq="min")]


def scored_pool(panel: pd.DataFrame, calendar: list[str]) -> pd.DataFrame:
    train, valid, _, test = split(panel, calendar, False)
    pieces = []
    for name, rows in (("train", train), ("valid", valid), ("test", test)):
        scored = pd.read_csv(OUT / f"full35_{name}.csv.gz", dtype={"symbol": str})
        x = rows[KEY].merge(scored[KEY + ["score"]], on=KEY, validate="one_to_one")
        if len(x) != len(rows):
            raise ValueError(f"Missing score in {name}")
        pieces.append(x.assign(split=name))
    scores = pd.concat(pieces, ignore_index=True)
    details = panel[KEY + ["date_i", "stk_code", "close", "volume", "amount", "turn_ratio",
                           "d1_cap", "d1_up", "label_next3"]]
    return scores.merge(details, on=KEY, validate="one_to_one")


def scan_minutes(pool: pd.DataFrame, root: Path, cache: Path) -> pd.DataFrame:
    if cache.exists():
        return pd.read_csv(cache, dtype={"symbol": str, "d1_date": str})
    by_day = pool.groupby("d1_date").symbol.apply(set).to_dict()
    rows = []
    for n, (day, symbols) in enumerate(sorted(by_day.items()), 1):
        chunks = []
        path = day_file(root, day)
        for chunk in pd.read_csv(path, dtype={"symbol": str},
                                 usecols=["time", "symbol", "close", "volume", "turnover"],
                                 chunksize=200_000):
            selected = chunk[chunk.symbol.str[:6].isin(symbols) & chunk.time.str[-8:].le("14:40:00")]
            if not selected.empty:
                chunks.append(selected)
        if chunks:
            for full_symbol, bars in pd.concat(chunks).groupby("symbol"):
                bars = bars.sort_values("time")
                if bars.time.str[-8:].tolist() != EXPECTED:
                    continue
                value = bars[["close", "volume", "turnover"]].apply(pd.to_numeric, errors="coerce")
                if (value.isna().any().any() or value.close.le(0).any() or
                        value.volume.lt(0).any() or value.turnover.lt(0).any()):
                    continue
                rows.append({"symbol": str(full_symbol)[:6], "d1_date": day,
                             "buy_1440": float(value.close.iloc[-1]),
                             "d1_1440_volume": float(value.volume.sum()),
                             "d1_1440_amount": float(value.turnover.sum())})
        if n % 20 == 0:
            print(f"14:40 audit {cache.stem} {n}/{len(by_day)} days, {len(rows)} paths", flush=True)
    result = pd.DataFrame(rows)
    result.to_csv(cache, index=False, compression="gzip")
    return result


def minute_pool(pool: pd.DataFrame) -> pd.DataFrame:
    required = pool[["symbol", "d1_date"]].drop_duplicates()
    old = pd.read_csv(OLD_MINUTE, dtype={"symbol": str, "d1_date": str})
    old = old.merge(required, on=["symbol", "d1_date"])
    missing = required.merge(old[["symbol", "d1_date"]], on=["symbol", "d1_date"],
                             how="left", indicator=True)
    missing = missing[missing._merge.eq("left_only")].drop(columns="_merge")
    parts = [old]
    for year, root in (("2025", COMPACT_ROOT_2025), ("2026", COMPACT_ROOT)):
        target = missing[missing.d1_date.str.startswith(year)]
        if not target.empty:
            parts.append(scan_minutes(target, root, OUT / f"minute_1440_{year}.csv.gz"))
    minute = pd.concat(parts, ignore_index=True)
    if minute.duplicated(["symbol", "d1_date"]).any():
        raise ValueError("Duplicate 14:40 path")
    return minute


def add_trade_fields(picks: pd.DataFrame, daily: pd.DataFrame, calendar: list[str]) -> pd.DataFrame:
    picks = picks.copy()
    picks["d0_volume_10k"] = picks.volume / 1e4
    picks["d0_turnover_pct"] = picks.turn_ratio
    picks["d1_1440_volume_10k"] = picks.d1_1440_volume / 1e4
    picks["d1_1440_amount_100m"] = picks.d1_1440_amount / 1e8
    picks["d1_1440_turnover_pct_est"] = picks.d1_1440_volume / picks.volume * picks.turn_ratio
    picks.loc[picks.volume.le(0) | picks.turn_ratio.le(0), "d1_1440_turnover_pct_est"] = np.nan
    picks["d0_code"] = picks.stk_code
    picks = picks.merge(d0_size(picks, OUT), on=["symbol", "d0_date"], how="left", validate="many_to_one")
    picks = picks.merge(prior_shares(picks), on=["symbol", "d0_date"], how="left", validate="many_to_one")
    for col in ("total_mv", "total_share", "prior_total_share"):
        picks[col] = pd.to_numeric(picks[col], errors="coerce")
    direct = picks.total_mv.gt(0)
    same_share = picks.total_share.gt(0)
    picks["market_cap_basis"] = np.select(
        [direct, same_share, picks.prior_total_share.gt(0)],
        ["D0总市值折算", "D0总股本估算", "此前总股本估算"], default="缺失")
    cap = picks.total_mv.where(direct, picks.total_share.where(same_share,
                                 picks.prior_total_share) * picks.close)
    picks["d1_1440_market_cap_100m_est"] = cap * picks.buy_1440 / picks.close / 1e8
    picks["market_cap_age_days"] = np.where(
        direct | same_share, 0,
        (pd.to_datetime(picks.d0_date) - pd.to_datetime(picks.prior_share_date)).dt.days)
    picks.loc[picks.market_cap_basis.eq("缺失"), "market_cap_age_days"] = np.nan
    price = daily.set_index(["symbol", "date_i"])["close"]
    for horizon in range(6):
        value = price.reindex(pd.MultiIndex.from_arrays([picks.symbol, picks.date_i + 1 + horizon])).to_numpy(dtype=float)
        col = "sell_same_day_return_pct" if horizon == 0 else f"sell_d{horizon}_return_pct"
        picks[col] = (value / picks.buy_1440 - 1) * 100
    return picks


def main() -> dict:
    panel = pd.read_csv(OUT / "panel.csv.gz", dtype={"symbol": str, "stk_code": str,
                                                        "d0_date": str, "d1_date": str,
                                                        "first_limit_date": str})
    daily = load_daily()
    calendar = sorted(daily.trade_date.unique())
    pool = scored_pool(panel, calendar)
    minute = minute_pool(pool)
    pool = pool.merge(minute, on=["symbol", "d1_date"], how="left", validate="many_to_one")
    if pool.buy_1440.isna().any():
        raise ValueError(f"Missing 14:40 path for {pool.buy_1440.isna().sum()} scored rows")
    pool["at_cap_1440"] = pool.buy_1440.ge(pool.d1_cap - .005)
    pool = pool.sort_values(["split", "d1_date", "score", "symbol"],
                            ascending=[True, True, False, True])
    pool["original_rank"] = pool.groupby(["split", "d1_date"]).cumcount() + 1
    pool.to_csv(OUT / "review_scored_full_1440.csv.gz", index=False, compression="gzip")
    blocked_original = pool[pool.original_rank.le(3) & pool.at_cap_1440]
    eligible = pool[~pool.at_cap_1440].sort_values(
        ["split", "d1_date", "score", "symbol"], ascending=[True, True, False, True]).copy()
    eligible["day_rank"] = eligible.groupby(["split", "d1_date"]).cumcount() + 1
    eligible.to_csv(OUT / "review_eligible_full_1440.csv.gz", index=False, compression="gzip")
    price = daily.set_index(["symbol", "date_i"])["close"]
    sell_d4 = price.reindex(pd.MultiIndex.from_arrays([eligible.symbol, eligible.date_i + 5])).to_numpy(dtype=float)
    eligible["sell_d4_return_pct"] = (sell_d4 / eligible.buy_1440 - 1) * 100
    pool_day = eligible.groupby(["split", "d1_date"]).sell_d4_return_pct.mean().rename("pool_mean_d4")
    first = eligible[eligible.day_rank.eq(1)].set_index(["split", "d1_date"])
    compare = first[["symbol", "score", "sell_d4_return_pct"]].join(pool_day)
    compare["top1_minus_pool_d4"] = compare.sell_d4_return_pct - compare.pool_mean_d4
    compare.to_csv(OUT / "top1_vs_pool_d4.csv")
    top3 = eligible[eligible.day_rank.le(3)].copy()
    top3 = add_trade_fields(top3, daily, calendar)
    columns = ["split", "symbol", "d0_date", "d1_date", "day_rank", "score",
               "d0_volume_10k", "d0_turnover_pct", "d1_1440_volume_10k",
               "d1_1440_amount_100m", "d1_1440_turnover_pct_est",
               "d1_1440_market_cap_100m_est", "market_cap_basis", "market_cap_age_days",
               "buy_1440", "at_cap_1440", "d1_up", "label_next3",
               "sell_same_day_return_pct", "sell_d1_return_pct", "sell_d2_return_pct",
               "sell_d3_return_pct", "sell_d4_return_pct", "sell_d5_return_pct"]
    output = OUT / "review_eligible_top3_1440.csv.gz"
    top3[columns].to_csv(output, index=False, compression="gzip", float_format="%.10g")
    result = {"experiment": "C1430-B_long_window_trade_review",
              "scored": len(pool), "minute_paths": len(minute),
              "at_cap_1440": int(pool.at_cap_1440.sum()),
              "blocked_original_top3": len(blocked_original),
              "blocked_original_top3_close_board": int(blocked_original.d1_up.sum()),
              "eligible": len(eligible), "top3": len(top3),
              "top3_at_cap": int(top3.at_cap_1440.sum()),
              "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "sets": {}}
    for name, x in top3.groupby("split"):
        all_rows = eligible[eligible.split.eq(name)]
        result["sets"][name] = {
            "days": int(x.d1_date.nunique()), "rows": len(x),
            "positive": int(x.label_next3.sum()),
            "replacements_from_below_original_top3": int(x.original_rank.gt(3).sum()),
            "top1_positive": int(x[x.day_rank.eq(1)].label_next3.sum()),
            "eligible_ap": float(average_precision_score(all_rows.label_next3, all_rows.score)),
            "eligible_auc": float(roc_auc_score(all_rows.label_next3, all_rows.score)),
            "mean_returns_pct": {"same_day": float(x.sell_same_day_return_pct.mean()),
                                 **{f"D{i}": float(x[f"sell_d{i}_return_pct"].mean())
                                    for i in range(1, 6)}},
            "top1_mean_returns_pct": {f"D{i}": float(x.loc[x.day_rank.eq(1), f"sell_d{i}_return_pct"].mean())
                                       for i in range(1, 6)},
            "pool_day_mean_d4_pct": float(compare.loc[name, "pool_mean_d4"].mean()),
            "top1_minus_pool_d4_pctpt": float(compare.loc[name, "top1_minus_pool_d4"].mean()),
        }
    (OUT / "trade_review_summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return result


if __name__ == "__main__":
    main()
