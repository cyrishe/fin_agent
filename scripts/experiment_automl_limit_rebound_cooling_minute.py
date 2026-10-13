"""Matched-row ablation of cooling-day minute paths versus daily-only features."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.build_automl_limit_rebound_features import KEY
from scripts.experiment_automl_limit_rebound_2y import BASE
from scripts.experiment_automl_limit_rebound_model import evaluate, event_keys, make_model
from scripts.mk_minute_archive import day_file


SOURCE = Path("outputs/stock_automl/limit_rebound_features_r1")
MINUTE_ROOT = Path("outputs/stock_automl/mk_1m_compact")
OUT = Path("outputs/stock_automl/limit_rebound_cooling_minute_r1")
MINUTE_FEATURES = {
    "cool_morning_return": "10:30 分钟收盘/首根分钟开盘-1",
    "cool_afternoon_return": "15:00 分钟收盘/11:30 分钟收盘-1",
    "cool_last30_return": "最后 30 分钟收盘/14:30 分钟收盘-1",
    "cool_first60_amount_fraction": "首 60 分钟成交额/全日分钟成交额",
    "cool_last30_amount_fraction": "最后 30 分钟成交额/全日分钟成交额",
    "cool_close_vs_vwap": "收盘价/(全日分钟成交额/成交量)-1",
    "cool_low_before_high": "全日首次最低价分钟早于首次最高价分钟",
    "cool_high_minute": "全日首次最高价分钟序号，1-240",
    "cool_low_minute": "全日首次最低价分钟序号，1-240",
    "cool_above_open_fraction": "240 根分钟收盘价高于首分钟开盘价的比例",
}


def extract_cooling_minute(bars: pd.DataFrame, daily_close: float) -> dict | None:
    required = {"time", "open", "high", "low", "close", "volume", "turnover"}
    if len(bars) != 240 or not required.issubset(bars):
        return None
    bars = bars.sort_values("time")
    clock = pd.to_datetime(bars.time).dt.strftime("%H:%M:%S")
    if (clock.nunique() != 240 or clock.iloc[0] != "09:31:00" or
            clock.iloc[-1] != "15:00:00"):
        return None
    numbers = bars[["open", "high", "low", "close", "volume", "turnover"]].apply(
        pd.to_numeric, errors="coerce")
    if (numbers.isna().any().any() or numbers[["open", "high", "low", "close"]].le(0).any().any()
            or numbers[["volume", "turnover"]].lt(0).any().any()
            or numbers.volume.sum() <= 0 or numbers.turnover.sum() <= 0
            or not np.isclose(numbers.close.iloc[-1], daily_close, atol=.005, rtol=0)):
        return None
    close = numbers.close.to_numpy()
    low = numbers.low.to_numpy()
    high = numbers.high.to_numpy()
    amount = numbers.turnover.to_numpy()
    vwap = amount.sum() / numbers.volume.sum()
    return {
        "cool_morning_return": float(close[59] / numbers.open.iloc[0] - 1),
        "cool_afternoon_return": float(close[-1] / close[119] - 1),
        "cool_last30_return": float(close[-1] / close[209] - 1),
        "cool_first60_amount_fraction": float(amount[:60].sum() / amount.sum()),
        "cool_last30_amount_fraction": float(amount[-30:].sum() / amount.sum()),
        "cool_close_vs_vwap": float(close[-1] / vwap - 1),
        "cool_low_before_high": int(np.argmin(low) < np.argmax(high)),
        "cool_high_minute": int(np.argmax(high) + 1),
        "cool_low_minute": int(np.argmin(low) + 1),
        "cool_above_open_fraction": float(np.mean(close > numbers.open.iloc[0])),
    }


def scan_minutes(panel: pd.DataFrame, daily: pd.DataFrame,
                 minute_root: Path = MINUTE_ROOT) -> pd.DataFrame:
    candidate = panel[["asof_date", "symbol"]].drop_duplicates()
    by_day = candidate.groupby("asof_date").symbol.apply(set).to_dict()
    daily = daily.copy()
    daily["trade_date"] = pd.to_datetime(daily.trade_date).dt.strftime("%Y-%m-%d")
    daily["symbol"] = daily.stk_code.astype(str).str[:6]
    closes = daily[daily.trade_date.isin(by_day)].set_index(["trade_date", "symbol"]).close.to_dict()
    collected = []
    for index, (day, symbols) in enumerate(sorted(by_day.items()), 1):
        try:
            path = day_file(minute_root, day)
        except FileNotFoundError:
            continue
        parts = []
        for chunk in pd.read_csv(path, dtype={"symbol": str}, chunksize=200_000):
            part = chunk[chunk.symbol.str[:6].isin(symbols)]
            if not part.empty:
                parts.append(part)
        if parts:
            for stock, bars in pd.concat(parts).groupby("symbol"):
                symbol = str(stock)[:6]
                cap = closes.get((day, symbol))
                if cap is None:
                    continue
                values = extract_cooling_minute(bars, float(cap))
                if values is not None:
                    collected.append({"asof_date": day, "symbol": symbol, **values})
        if index % 20 == 0:
            print(f"minute days scanned {index}/{len(by_day)}; complete {len(collected)}", flush=True)
    return pd.DataFrame(collected)


def run(source: Path = SOURCE, minute_root: Path = MINUTE_ROOT,
        out: Path = OUT) -> dict:
    features = pd.read_csv(source / "features.csv.gz", dtype={"symbol": str})
    labels = pd.read_csv(source / "labels.csv.gz", dtype={"symbol": str})
    panel = features.merge(labels, on=KEY, validate="one_to_one")
    panel = panel[panel.asof_date.between("2026-01-05", "2026-07-31")].copy()
    out.mkdir(parents=True, exist_ok=True)
    cache = out / "cooling_minute_features.csv.gz"
    if cache.exists():
        minutes = pd.read_csv(cache, dtype={"symbol": str})
    else:
        daily = pd.read_csv(source / "daily_source.csv.gz", dtype={"stk_code": str},
                            usecols=["trade_date", "stk_code", "close"])
        minutes = scan_minutes(panel, daily, minute_root)
        minutes.to_csv(cache, index=False, compression="gzip")
    panel = panel.merge(minutes, on=["asof_date", "symbol"], how="inner", validate="one_to_one")
    if panel.empty:
        raise ValueError("No complete cooling-minute decision rows")
    results = {}
    for target, observed in (("label_next_limit_up", "label_next_observed"),
                             ("label_next3_any_limit_up", "label_next3_observed")):
        eligible = panel[panel[observed].eq(True)].copy()
        eligible[target] = eligible[target].astype(int)
        train_end = "2026-04-29" if target == "label_next_limit_up" else "2026-04-27"
        train = eligible[eligible.asof_date.le(train_end)].copy()
        test = eligible[eligible.asof_date.between("2026-05-06", "2026-07-31")].copy()
        train = train[~pd.MultiIndex.from_frame(train[["symbol", "first_limit_date"]]).isin(event_keys(test))]
        model_results = {}
        for name, cols in (("daily", BASE), ("daily_plus_cooling_minutes", BASE + tuple(MINUTE_FEATURES))):
            model = make_model("shallow_boost")
            model.fit(train[list(cols)], train[target])
            score = model.predict_proba(test[list(cols)])[:, 1]
            model_results[name] = evaluate(test, score, target)
        results[target] = {"train_n": len(train), "test_n": len(test), "models": model_results}
    summary = {"purpose": "exploratory same-row cooling-minute ablation, local 2026 archive",
               "minute_features": MINUTE_FEATURES, "complete_rows": len(panel),
               "source_window": ["2026-01-05", "2026-07-31"], "results": results}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


if __name__ == "__main__":
    run()
