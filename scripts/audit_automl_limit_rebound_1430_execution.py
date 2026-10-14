"""Apply the D1 14:40 sealed-board gate before C1430-A daily ranking."""

from __future__ import annotations

import hashlib
import json

import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from scripts.experiment_automl_limit_rebound_1430_r7 import MINUTE_ROOT, OUT
from scripts.experiment_automl_limit_rebound_d0_open_r5 import cap_price, normal_limit_rate
from scripts.export_automl_limit_rebound_1430_trade_review import daily_source
from scripts.mk_minute_archive import day_file


def scan_minute(panel: pd.DataFrame) -> pd.DataFrame:
    cache = OUT / "review_all_minute_1440.csv.gz"
    if cache.exists():
        return pd.read_csv(cache, dtype={"symbol": str, "d1_date": str})
    by_day = panel.groupby("d1_date").symbol.apply(set).to_dict()
    expected = [t.strftime("%H:%M:%S") for t in pd.date_range("09:31", "11:30", freq="min")]
    expected += [t.strftime("%H:%M:%S") for t in pd.date_range("13:01", "14:40", freq="min")]
    rows = []
    for n, (day, symbols) in enumerate(sorted(by_day.items()), 1):
        path = day_file(MINUTE_ROOT, day)
        parts = []
        for chunk in pd.read_csv(path, dtype={"symbol": str}, chunksize=200_000):
            x = chunk[chunk.symbol.str[:6].isin(symbols) & chunk.time.str[-8:].le("14:40:00")]
            if not x.empty:
                parts.append(x)
        if not parts:
            continue
        for full_symbol, bars in pd.concat(parts).groupby("symbol"):
            bars = bars.sort_values("time")
            if bars.time.str[-8:].tolist() != expected:
                continue
            values = bars[["close", "volume", "turnover"]].apply(pd.to_numeric, errors="coerce")
            if (values.isna().any().any() or values.close.le(0).any()
                    or values.volume.lt(0).any() or values.turnover.lt(0).any()):
                continue
            rows.append({"symbol": str(full_symbol)[:6], "d1_date": day,
                         "buy_1440": float(values.close.iloc[-1]),
                         "d1_1440_volume": float(values.volume.sum()),
                         "d1_1440_amount": float(values.turnover.sum())})
        if n % 20 == 0:
            print(f"14:40 execution audit {n}/{len(by_day)} days; {len(rows)} minute rows", flush=True)
    result = pd.DataFrame(rows)
    result.to_csv(cache, index=False, compression="gzip")
    return result


def main() -> dict:
    pieces = []
    for split in ("train", "valid", "test"):
        x = pd.read_csv(OUT / f"review_next3_{split}.csv.gz",
                        dtype={"symbol": str, "d0_date": str, "d1_date": str})
        x["split"] = split
        x = x.rename(columns={"day_rank": "original_rank"})
        pieces.append(x)
    panel = pd.concat(pieces, ignore_index=True)
    minute = scan_minute(panel)
    daily, calendar = daily_source(set(panel.symbol))
    d1 = daily[["symbol", "trade_date", "preclose"]].rename(
        columns={"trade_date": "d1_date", "preclose": "d1_preclose"})
    panel = panel.merge(d1, on=["symbol", "d1_date"], validate="many_to_one")
    panel = panel.merge(minute, on=["symbol", "d1_date"], how="left", validate="many_to_one")
    panel["d1_cap"] = [cap_price(preclose, normal_limit_rate(symbol))
                       for preclose, symbol in zip(panel.d1_preclose, panel.symbol)]
    panel["at_cap_1440"] = panel.buy_1440.ge(panel.d1_cap - .005)
    eligible = panel[panel.buy_1440.notna() & ~panel.at_cap_1440].copy()
    eligible = eligible.sort_values(["split", "d1_date", "score", "symbol"],
                                    ascending=[True, True, False, True])
    eligible["day_rank"] = eligible.groupby(["split", "d1_date"]).cumcount() + 1
    top3 = eligible[eligible.day_rank.le(3)].copy()
    expected = panel.groupby(["split", "d1_date"]).size()
    actual = top3.groupby(["split", "d1_date"]).size()
    if not actual.reindex(expected.index, fill_value=0).eq(expected.clip(upper=3)).all():
        raise AssertionError("Insufficient 14:40 eligible rows on a score day")
    columns = ["split", "symbol", "d0_date", "d1_date", "day_rank", "original_rank",
               "score", "d1_1430_price", "d1_up", "label_next3", "buy_1440"]
    path = OUT / "review_eligible_top3_1440.csv.gz"
    top3[columns].to_csv(path, index=False, compression="gzip", float_format="%.10g")
    original = panel[panel.original_rank.le(3)]
    next_day = dict(zip(calendar[:-1], calendar[1:]))
    successor_rows = []
    for row in original[original.at_cap_1440].itertuples(index=False):
        tomorrow = next_day.get(row.d1_date)
        continuation = panel[(panel.split.eq(row.split)) & (panel.symbol.eq(row.symbol)) &
                             (panel.d0_date.eq(row.d1_date)) & (panel.d1_date.eq(tomorrow))]
        if bool(row.d1_up) and len(continuation) == 1:
            next_row = continuation.iloc[0]
            next_rank = eligible[(eligible.split.eq(row.split)) &
                                 (eligible.symbol.eq(row.symbol)) &
                                 (eligible.d1_date.eq(tomorrow))]
            reason = ("次日14:40仍封板" if bool(next_row.at_cap_1440) else
                      "次日重新评分并进入Top3" if not next_rank.empty and int(next_rank.day_rank.iloc[0]) <= 3
                      else "次日重新评分但未进Top3")
        elif bool(row.d1_up):
            next_rank = pd.DataFrame()
            quote = daily[(daily.symbol.eq(row.symbol)) & (daily.trade_date.eq(tomorrow))]
            cap = (cap_price(float(quote.preclose.iloc[0]), normal_limit_rate(row.symbol))
                   if len(quote) == 1 else None)
            at_1430 = None
            if cap is not None:
                bars = pd.read_csv(day_file(MINUTE_ROOT, tomorrow),
                                   usecols=["time", "symbol", "close"], dtype={"symbol": str})
                at = bars[bars.symbol.str[:6].eq(row.symbol) &
                          bars.time.str[-8:].eq("14:30:00")]
                if len(at) == 1:
                    at_1430 = float(at.close.iloc[0]) >= cap - .005
            reason = ("次日14:30仍封板，不入候选" if at_1430 else
                      "次日未进入当前模型评分池，原因待核查")
        else:
            next_rank = pd.DataFrame()
            reason = "当日收盘已开板；第二冷静日不在当前模型范围"
        successor_rows.append({"split": row.split, "symbol": row.symbol,
                               "blocked_d1": row.d1_date, "d1_close_board": int(row.d1_up),
                               "next_day": tomorrow, "next_rank_after_gate":
                               int(next_rank.day_rank.iloc[0]) if not next_rank.empty else None,
                               "reason": reason})
    pd.DataFrame(successor_rows).to_csv(OUT / "review_1440_deferred_cases.csv",
                                        index=False)
    result = {"raw_scored_rows": len(panel), "minute_rows": len(minute),
              "at_cap_1440_all": int(panel.at_cap_1440.sum()),
              "at_cap_1440_original_top3": int(original.at_cap_1440.sum()),
              "eligible_rows": len(eligible), "top3_rows": len(top3),
              "deferred_original_top3": len(successor_rows),
              "sets": {}, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    for split, x in top3.groupby("split"):
        full = eligible[eligible.split.eq(split)]
        result["sets"][split] = {"rows": len(x), "days": int(x.d1_date.nunique()),
                                 "reboard_hits": int(x.label_next3.sum()),
                                 "replacements_from_below_top3": int(x.original_rank.gt(3).sum()),
                                 "eligible_rows": len(full),
                                 "eligible_positive": int(full.label_next3.sum()),
                                 "eligible_ap": float(average_precision_score(full.label_next3, full.score)),
                                 "eligible_auc": float(roc_auc_score(full.label_next3, full.score))}
    (OUT / "review_eligible_top3_1440_manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
