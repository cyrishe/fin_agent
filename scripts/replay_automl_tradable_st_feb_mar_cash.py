"""Re-fit 2026 Feb–Mar 20-day models and replay the prior-five-day amount gate."""
from __future__ import annotations

import hashlib
import json
import tempfile
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.experiment_automl_mk_1m_rolling20 import one_fold
from scripts.mk_minute_archive import COMPACT_ROOT, day_file, day_files, trading_day


SOURCE = Path("outputs/stock_automl/mk_1m_jan_jul/tradable_st_candidates.csv.gz")
REFERENCE = Path("docs/stock_automl_runs/20261011_mk_1m_tradable_st_rolling20")
AMOUNT_REFERENCE = Path("docs/stock_automl_runs/20261011_mk_1m_three_st_policies/"
                        "tradable_st_new_exit_market/market_amount_1440.csv")
MINUTE_MANIFEST = Path("docs/stock_automl_runs/20261011_mk_1m_compact/"
                       "compressed_manifest.json")
OUTPUT = Path("docs/stock_automl_runs/20261012_feb_mar_cash_replay")
STARTING_CASH = 1_000_000.0
MIN_AMOUNT_100M = 20_000.0


def refit(rows: pd.DataFrame, dates: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    picks, folds = [], []
    with tempfile.TemporaryDirectory() as directory:
        checkpoint = Path(directory)
        for index, day in enumerate(dates):
            if not day.startswith(("2026-02", "2026-03")):
                continue
            one_fold(rows, dates, index, checkpoint)
            fold = json.loads((checkpoint / f"{day}_fold.json").read_text())
            folds.append(fold)
            selected = pd.read_csv(checkpoint / f"{day}_picks.csv",
                                   dtype={"symbol6": str, "next_date": str})
            picks.append(selected[selected.selection_rank.eq(1)])
            print(f"retrained {day}: {fold['training_rows']} rows, "
                  f"{fold['inference_candidates']} candidates, "
                  f"{len(picks[-1])} Top1", flush=True)
    result = pd.concat(picks, ignore_index=True)
    saved = pd.read_csv(REFERENCE / "top1_verified.csv", dtype={"symbol6": str})
    saved = saved[saved.signal_date.str.startswith(("2026-02", "2026-03"))]
    aligned = result.merge(saved, on="signal_date", validate="one_to_one",
                           suffixes=("_new", "_saved"))
    if len(aligned) != len(result) or len(aligned) != len(saved):
        raise ValueError("The new and saved Top1 dates differ")
    for column in ("symbol6", "predicted_class"):
        if not aligned[f"{column}_new"].equals(aligned[f"{column}_saved"]):
            raise ValueError(f"Re-fitted Top1 {column} differs from the saved result")
    for column in ("p_ge3", "p_1to3", "signal_price", "close_40",
                   "actual_0940_return"):
        if not np.allclose(aligned[f"{column}_new"], aligned[f"{column}_saved"],
                           atol=1e-12, rtol=0, equal_nan=True):
            raise ValueError(f"Re-fitted Top1 {column} differs from the saved result")
    return result, pd.DataFrame(folds)


def replay_minute_bars(picks: pd.DataFrame, dates: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    expected_minute_hashes = {
        item["day"]: item["compressed_sha256"]
        for item in json.loads(MINUTE_MANIFEST.read_text())["files"]
    }
    requested = defaultdict(dict)
    for pick in picks.itertuples():
        suffix = "SH" if pick.symbol6.startswith("6") else "SZ"
        symbol = f"{pick.symbol6}.{suffix}"
        requested[pick.signal_date][(symbol, "14:40:00")] = (pick.signal_date, "entry")
        requested[pick.next_date][(symbol, "09:40:00")] = (pick.signal_date, "exit")
    market_days = dates[:dates.index("2026-03-31") + 1]
    market, observed = [], []
    for day in sorted(set(market_days) | set(requested)):
        path = day_file(COMPACT_ROOT, day)
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_minute_hashes[day]:
            raise ValueError(f"Raw minute archive hash differs on {day}")
        amount, at_1440 = 0.0, set()
        targets = requested[day]
        symbols = {symbol for symbol, _ in targets}
        for chunk in pd.read_csv(path, usecols=["time", "symbol", "open", "high",
                                                 "low", "close", "volume", "turnover"],
                                 dtype={"symbol": str}, chunksize=250_000):
            if not chunk.time.str[:10].eq(day).all():
                raise ValueError(f"Wrong source date in {path}")
            clock = chunk.time.str[-8:]
            if day in market_days:
                a_share = (chunk.symbol.str.endswith((".SH", ".SZ")) &
                           chunk.symbol.str[0].isin(("0", "3", "6")))
                visible = chunk.loc[a_share & clock.le("14:40:00")]
                if visible.turnover.isna().any() or visible.turnover.lt(0).any():
                    raise ValueError(f"Invalid market turnover on {day}")
                amount += float(visible.turnover.sum())
                at_1440.update(visible.loc[visible.time.str.endswith("14:40:00"),
                                              "symbol"])
            if targets:
                selected = chunk.loc[chunk.symbol.isin(symbols) &
                                     clock.isin(("14:40:00", "09:40:00"))]
                for bar in selected.itertuples():
                    key = (bar.symbol, bar.time[-8:])
                    if key not in targets:
                        continue
                    signal_day, role = targets[key]
                    observed.append({"signal_date": signal_day, "role": role,
                                     "source_day": day, "symbol": bar.symbol,
                                     "price": float(bar.close),
                                     "minute_volume_shares": float(bar.volume),
                                     "ohlc_valid": bool(bar.low <= bar.close <= bar.high)})
        if day in market_days:
            if len(at_1440) < 4_000:
                raise ValueError(f"Too few market symbols at 14:40 on {day}")
            market.append({"trade_date": day,
                           "amount_1440_100m_cny": amount / 1e8,
                           "symbols_at_1440": len(at_1440)})
        print(f"audited raw minute bars {day}", flush=True)
    market = pd.DataFrame(market)
    observed = pd.DataFrame(observed)
    if observed.duplicated(["signal_date", "role"]).any() or len(observed) != 2 * len(picks):
        raise ValueError("Missing or duplicate entry/exit minute bars")
    price = observed.pivot(index="signal_date", columns="role", values="price")
    expected = picks.set_index("signal_date")
    if (not np.allclose(price.entry, expected.signal_price, atol=1e-9, rtol=0) or
            not np.allclose(price.exit, expected.close_40, atol=1e-9, rtol=0) or
            not observed.ohlc_valid.all()):
        raise ValueError("Re-fitted prices differ from raw unadjusted minute bars")
    saved_market = pd.read_csv(AMOUNT_REFERENCE, dtype={"trade_date": str})
    same = market.merge(saved_market, on="trade_date", validate="one_to_one",
                        suffixes=("_new", "_saved"))
    if (len(same) != len(market) or
            not np.allclose(same.amount_1440_100m_cny_new,
                            same.amount_1440_100m_cny_saved, atol=1e-6, rtol=0) or
            not same.symbols_at_1440_new.equals(same.symbols_at_1440_saved)):
        raise ValueError("Recomputed raw market amount differs from saved report")
    return market, observed


def cash_ledger(picks: pd.DataFrame, folds: pd.DataFrame,
                market: pd.DataFrame, bars: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    fold_by_day = folds.set_index("test_date")
    pick_by_day = picks.set_index("signal_date")
    entry_volume = bars[bars.role.eq("entry")].set_index("signal_date").minute_volume_shares
    exit_volume = bars[bars.role.eq("exit")].set_index("signal_date").minute_volume_shares
    cash, records = STARTING_CASH, []
    for day in folds.test_date:
        previous = market.loc[market.trade_date.lt(day)].tail(5)
        if len(previous) != 5:
            raise ValueError(f"Missing five market days before {day}")
        mean = float(previous.amount_1440_100m_cny.mean())
        gate = mean >= MIN_AMOUNT_100M
        chosen = pick_by_day.loc[day] if day in pick_by_day.index else None
        trade = bool(gate and chosen is not None)
        entry = float(chosen.signal_price) if trade else np.nan
        exit_price = float(chosen.close_40) if trade else np.nan
        if trade and (not np.isfinite(exit_price) or entry <= 0):
            raise ValueError(f"Unknown sell price for selected trade on {day}")
        shares = cash / entry if trade else 0.0
        pnl = shares * (exit_price - entry) if trade else 0.0
        cash += pnl
        records.append({
            "选股日": day, "前五日14:40两市成交额均值（亿元）": mean,
            "成交额达标": gate,
            "动作": "交易" if trade else "轮空（成交额不足）" if not gate else "轮空（无Top1）",
            "训练样本数": int(fold_by_day.loc[day, "training_rows"]),
            "当天候选数": int(fold_by_day.loc[day, "inference_candidates"]),
            "股票代码": chosen.symbol6 if trade else "",
            "股票名称": chosen["name"] if trade else "",
            "买入价（T日14:40）": entry,
            "卖出日": chosen.next_date if trade else "",
            "卖出价（T+1日09:40）": exit_price,
            "买入股数（允许小数）": shares,
            "买入股数占14:40分钟成交量": shares / entry_volume.loc[day] if trade else np.nan,
            "卖出股数占09:40分钟成交量": shares / exit_volume.loc[day] if trade else np.nan,
            "单笔收益率（%）": (exit_price / entry - 1) * 100 if trade else np.nan,
            "本笔盈亏（元）": pnl, "卖出后资金（元）": cash,
        })
    daily = pd.DataFrame(records)
    monthly = []
    start_cash = STARTING_CASH
    for month, frame in daily.groupby(daily["选股日"].str[:7], sort=True):
        traded = frame[frame["动作"].eq("交易")]
        end_cash = float(frame["卖出后资金（元）"].iloc[-1])
        monthly.append({"月份": month, "测试日数": len(frame),
                        "成交额达标日数": int(frame["成交额达标"].sum()),
                        "交易笔数": len(traded),
                        "盈利笔数": int(traded["单笔收益率（%）"].gt(0).sum()),
                        "亏损笔数": int(traded["单笔收益率（%）"].lt(0).sum()),
                        "轮空日数": len(frame) - len(traded),
                        "单笔平均收益（%）": float(traded["单笔收益率（%）"].mean()),
                        "月初资金（元）": start_cash, "月末资金（元）": end_cash,
                        "月盈亏（元）": end_cash - start_cash,
                        "月资金收益（%）": (end_cash / start_cash - 1) * 100})
        start_cash = end_cash
    return daily, pd.DataFrame(monthly)


def main() -> None:
    expected_hash = json.loads((REFERENCE / "summary.json").read_text())["data_sha256"]
    source_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    if source_hash != expected_hash:
        raise ValueError("The frozen training input hash differs from the original experiment")
    rows = pd.read_csv(SOURCE, dtype={"symbol6": str, "signal_date": str,
                                      "next_date": str}, low_memory=False)
    dates = [trading_day(path) for path in day_files([COMPACT_ROOT])]
    if len(dates) != 139 or dates != sorted(rows.signal_date.unique()):
        raise ValueError("The original 139-day raw calendar is unavailable")
    picks, folds = refit(rows, dates)
    market, bars = replay_minute_bars(picks, dates)
    daily, monthly = cash_ledger(picks, folds, market, bars)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    daily.to_csv(OUTPUT / "daily.csv", index=False, float_format="%.10f")
    monthly.to_csv(OUTPUT / "monthly.csv", index=False, float_format="%.10f")
    market.to_csv(OUTPUT / "market_amount_recomputed.csv", index=False,
                  float_format="%.10f")
    bars.to_csv(OUTPUT / "selected_minute_bar_audit.csv", index=False)
    folds.to_csv(OUTPUT / "folds.csv", index=False)
    print(json.dumps({"source_sha256": source_hash, "test_days": len(daily),
                      "retrained_top1": len(picks), "trades": int(daily["动作"].eq("交易").sum()),
                      "entry_zero_volume": int(bars.loc[bars.role.eq("entry"),
                                                        "minute_volume_shares"].eq(0).sum()),
                      "exit_zero_volume": int(bars.loc[bars.role.eq("exit"),
                                                       "minute_volume_shares"].eq(0).sum()),
                      "buy_exceeds_entire_minute_volume": int(daily[
                          "买入股数占14:40分钟成交量"].gt(1).sum()),
                      "ending_cash": float(daily["卖出后资金（元）"].iloc[-1]),
                      "monthly": monthly.to_dict("records")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
