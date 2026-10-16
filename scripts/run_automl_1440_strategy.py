"""Run the frozen rolling-20, seven-factor 14:40 strategy on this computer.

`prepare` fits the model from completed historical days. `run` reads an exact
14:40 database bar or, during the live 14:40 minute, the full-market quote
API. Neither command places an order. `init-db` creates the two result tables.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time
from pathlib import Path
from urllib.parse import unquote, urlparse
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import pandas as pd
import pymysql
import sklearn
import scipy
from dotenv import load_dotenv

from scripts.audit_automl_mk_1m_entry_limit import mark_entry_limit
from scripts.automl_st_status import attach_st_status, load_st_intervals
from scripts.benchmark_automl_1440_inference import (
    FEATURES, db_connection, fetch_full_market, four_of_five_increasing, query,
    select_candidates,
)
from scripts.run_automl_four_class_pipeline import (
    generate_training_features, generate_training_labels, infer, select_top,
    train_model,
)
from scripts.audit_automl_minute_api_backfill import request_bars
from scripts.readable_strategy_execution import record_decisions, record_run, settle_previous


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "deploy/sql/automl_1440_strategy.sql"
SHANGHAI = ZoneInfo("Asia/Shanghai")
STRATEGY_ID = f"rolling20_7f_hgb_sklearn{sklearn.__version__.replace('.', '')}_dbapi_amount_v2"
DEFINITION = {
    "title": "14:40 近20日七因子四分类",
    "summary": "涨幅3%–6%候选；近20个连续交易日全量样本；小型提升树四分类；Top1成交额不足则轮空，Top2仅供观察。",
    "training_method": "前20个连续交易日有成熟次晨标签的3%–6%候选；七因子；每天重训sklearn HistGradientBoostingClassifier（60轮、学习率0.05、最多7叶、最小叶100、L2=5、seed=42）；标签为次晨09:31–09:40十根最高价的第二高相对14:40信号价：<0、0–1、1–3、≥3%。次晨分钟表缺行时用同日期一分钟K接口补齐，仍不能取得则不训练该股票日。",
    "selection_formula": "先按预测类别≥3%、再按1–3%；类别内分别按P(≥3%)、P(≥1%)降序，代码升序；取前2。仅Top1用于成交额门槛，不由Top2递补。",
    "execution_formula": "Top1选股日截至14:40累计成交额≥该股此前20个交易日同口径中位数才标记可交易；否则轮空。历史回放买卖价格代理为14:40–14:41及次晨09:39–09:40各一分钟K高低均价，实时信号不预知这些价格。",
    "formula": {"candidate_return_min": 0.03, "candidate_return_max": 0.06,
                "training_days": 20, "features": list(FEATURES),
                "sklearn_version": sklearn.__version__,
                "labels": ["lt0", "0to1", "1to3", "ge3"],
                "model": "sklearn HistGradientBoostingClassifier",
                "model_params": {"max_iter": 60, "learning_rate": 0.05,
                                 "max_leaf_nodes": 7, "min_samples_leaf": 100,
                                 "l2_regularization": 5, "early_stopping": False,
                                 "random_state": 42},
                "st_rule": "only ST at limit excluded",
                "gate": "top1 amount_1440 >= same stock prior20 median; no substitution"},
}


def iso(value) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def clean_number(value):
    if value is None or pd.isna(value):
        return None
    return float(value)


def database_calendar(db, day: str) -> list[str]:
    rows = query(db, "SELECT DISTINCT trade_date FROM kcrp_stock_price "
                 "WHERE trade_date<%s ORDER BY trade_date DESC LIMIT 41", (day,))
    dates = sorted(iso(v) for v in rows.trade_date.tolist())
    if len(dates) != 41:
        raise ValueError(f"{day}: 至少需要此前41个交易日的日行情，实际{len(dates)}日")
    trade = query(db, "SELECT is_trade_day FROM aiia_trade_calendar "
                  "WHERE market_code='CN_A' AND calendar_date=%s", (day,))
    if len(trade) != 1 or int(trade.is_trade_day.iloc[0]) != 1:
        raise ValueError(f"{day}不是已登记的A股交易日")
    return dates


def daily_sources(db, dates: list[str]):
    start, end = dates[0], dates[-1]
    price = query(db, "SELECT trade_date,stk_code,close,adjclose,adjpreclose,"
                  "volume,preclose FROM kcrp_stock_price "
                  "WHERE trade_date BETWEEN %s AND %s", (start, end))
    value = query(db, "SELECT trade_date,stk_code,float_mv,float_share "
                  "FROM kcrp_stock_pricevaluate WHERE trade_date BETWEEN %s AND %s",
                  (start, end))
    for frame in (price, value):
        frame["trade_date"] = frame.trade_date.map(iso)
        frame["symbol6"] = frame.stk_code.astype(str).str[:6].str.zfill(6)
        if frame.duplicated(["trade_date", "symbol6"]).any():
            raise ValueError("日行情或流通市值数据存在重复股票日")
    return price, value


def archive_day(root: Path, day: str) -> pd.DataFrame:
    path = next((p for p in (root / f"{day}.csv.zst", root / f"{day}.csv")
                 if p.exists() and p.stat().st_size > 1000), None)
    if path is None:
        raise FileNotFoundError(f"DB缺少{day}分钟K，备用目录也没有完整文件：{root}")
    bars = pd.read_csv(path, usecols=["time", "symbol", "high", "low", "close",
                                       "volume", "turnover"], dtype={"time": str, "symbol": str})
    bars["symbol6"] = bars.symbol.str[:6]
    return bars


def archived_signal(root: Path, day: str, preclose: pd.DataFrame) -> pd.DataFrame:
    bars = archive_day(root, day)
    bar = bars[bars.time.eq(f"{day} 14:40:00")].copy()
    bar = bar.merge(preclose[["symbol6", "preclose"]], on="symbol6", how="inner",
                    validate="one_to_one")
    bar["signal_return"] = bar.close / pd.to_numeric(bar.preclose) - 1
    bar = bar[bar.signal_return.between(.03, .06, inclusive="both")]
    selected = bars[bars.symbol6.isin(bar.symbol6) &
                    bars.time.between(f"{day} 09:31:00", f"{day} 14:40:00")]
    group = selected.groupby("symbol6").agg(
        bars=("time", "nunique"), volume_hands=("volume", "sum"),
        low_so_far=("low", "min"), amount_so_far=("turnover", "sum")).reset_index()
    frame = bar[["symbol6", "close", "preclose"]].merge(group, on="symbol6")
    frame = frame[frame.bars.eq(220)].rename(columns={"close": "signal_price"})
    # MK CSV volume is shares; DB snapshot and quote API volume are hands.
    frame["volume_hands"] = frame.volume_hands / 100
    frame["name"] = frame.symbol6
    frame["signal_return"] = frame.signal_price / pd.to_numeric(frame.preclose) - 1
    return frame[["symbol6", "name", "signal_price", "preclose", "signal_return", "volume_hands",
                  "low_so_far", "amount_so_far"]]


def database_signal(db, day: str, daily: pd.DataFrame,
                    archive: Path | None = None) -> tuple[pd.DataFrame, str]:
    bars = query(db, "SELECT stk_code AS symbol6,stk_name AS name,latest_price AS signal_price,"
                 "preclose_price AS preclose,"
                 "is_finalized,is_fallback,source_snapshot_time,bar_end_time "
                 "FROM aiia_stock_realtime_minute_snapshot_full "
                 "WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1 "
                 "AND bar_end_time=%s", (day, f"{day} 14:40:00"))
    if bars.empty:
        if archive is None:
            raise ValueError(f"{day} 14:40分钟K尚未入库；可指定 --archive-dir 使用未复权归档")
        return archived_signal(archive, day, daily[daily.trade_date.eq(day)]), "archive"
    exact = (bars.is_finalized.eq(1) & bars.is_fallback.eq(0) &
             pd.to_datetime(bars.source_snapshot_time).eq(pd.to_datetime(bars.bar_end_time)))
    bars = bars[exact].copy()
    bars["symbol6"] = bars.symbol6.astype(str).str[:6].str.zfill(6)
    daily_day = daily[daily.trade_date.eq(day)][["symbol6", "preclose"]]
    if daily_day.empty and day < datetime.now(SHANGHAI).date().isoformat():
        # Historical replay may read the signal day's finalized daily row.
        # Only preclose is used; its value was already known at 14:40.
        daily_day = query(db, "SELECT LEFT(stk_code,6) AS symbol6,preclose "
                          "FROM kcrp_stock_price WHERE trade_date=%s", (day,))
    if not daily_day.empty:
        bars = bars.merge(daily_day, on="symbol6", how="left",
                          suffixes=("", "_daily"), validate="one_to_one")
        bars["preclose"] = bars.preclose.fillna(bars.preclose_daily)
        bars = bars.drop(columns="preclose_daily")
    elif bars.preclose.isna().any() and day == datetime.now(SHANGHAI).date().isoformat():
        # The minute snapshot commonly lacks preclose_price. The current
        # quote API's fClose is yesterday's reference close even after 14:40;
        # its later *current price* is never used for this DB-sourced signal.
        references, _ = fetch_full_market(sorted(bars.symbol6.unique()))
        bars = bars.merge(references[["symbol6", "preclose"]], on="symbol6",
                          how="left", suffixes=("", "_api"), validate="one_to_one")
        bars["preclose"] = bars.preclose.fillna(bars.preclose_api)
        bars = bars.drop(columns="preclose_api")
    bars["signal_price"] = pd.to_numeric(bars.signal_price)
    bars["preclose"] = pd.to_numeric(bars.preclose)
    selected = select_candidates(bars)
    if selected.empty:
        return selected, "database"
    result = []
    for start in range(0, len(selected), 300):
        codes = selected.symbol6.iloc[start:start+300].tolist()
        holders = ",".join(["%s"] * len(codes))
        summary = query(db, f"""SELECT stk_code AS symbol6,COUNT(*) AS bars,
            COUNT(DISTINCT bar_end_time) AS distinct_bars,
            SUM(volume) AS volume_hands,SUM(amount) AS amount_so_far,
            MIN(low_price) AS low_so_far,
            SUM(is_finalized=1 AND is_fallback=0 AND
                source_snapshot_time=bar_end_time) AS exact_bars
            FROM aiia_stock_realtime_minute_snapshot_full
            WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
              AND bar_end_time BETWEEN %s AND %s
              AND stk_code IN ({holders}) GROUP BY stk_code""",
            (day, f"{day} 09:31:00", f"{day} 14:40:00", *codes))
        result.append(summary)
    summary = pd.concat(result, ignore_index=True)
    summary["symbol6"] = summary.symbol6.astype(str).str[:6].str.zfill(6)
    summary = summary[summary.bars.eq(220) & summary.distinct_bars.eq(220) &
                      summary.exact_bars.eq(220)]
    selected = selected.merge(summary[["symbol6", "volume_hands", "amount_so_far",
                                       "low_so_far"]], on="symbol6", how="inner",
                              validate="one_to_one")
    return selected[["symbol6", "name", "signal_price", "preclose", "signal_return", "volume_hands",
                     "amount_so_far", "low_so_far"]], "database"


def morning_labels(db, next_day: str, codes: list[str], archive: Path | None) -> pd.DataFrame:
    if not codes:
        return pd.DataFrame(columns=["symbol6", "second_high", "close_40"])
    records = []
    for start in range(0, len(codes), 300):
        batch = codes[start:start+300]
        holders = ",".join(["%s"] * len(batch))
        bars = query(db, f"""SELECT stk_code AS symbol6,bar_end_time,high_price,
            latest_price,volume,is_finalized,is_fallback,source_snapshot_time
            FROM aiia_stock_realtime_minute_snapshot_full
            WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
              AND bar_end_time BETWEEN %s AND %s
              AND stk_code IN ({holders})""",
            (next_day, f"{next_day} 09:31:00", f"{next_day} 09:40:00", *batch))
        if bars.empty and archive is not None:
            local = archive_day(archive, next_day)
            local = local[local.symbol6.isin(batch) & local.time.between(
                f"{next_day} 09:31:00", f"{next_day} 09:40:00")]
            bars = local.rename(columns={"time": "bar_end_time", "high": "high_price",
                                         "close": "latest_price"})
            bars["is_finalized"] = 1
            bars["is_fallback"] = 0
            bars["source_snapshot_time"] = bars.bar_end_time
        if bars.empty:
            continue
        bars["symbol6"] = bars.symbol6.astype(str).str[:6].str.zfill(6)
        bars["bar_end_time"] = pd.to_datetime(bars.bar_end_time)
        for code, stock in bars.groupby("symbol6"):
            if (len(stock) != 10 or stock.bar_end_time.nunique() != 10 or
                    not stock.is_finalized.eq(1).all() or
                    not stock.is_fallback.eq(0).all() or
                    not pd.to_datetime(stock.source_snapshot_time).eq(stock.bar_end_time).all()):
                continue
            high = pd.to_numeric(stock.high_price)
            first5 = pd.to_numeric(stock.loc[stock.bar_end_time.le(
                pd.Timestamp(f"{next_day} 09:35:00")), "volume"]).sum()
            close40 = stock.loc[stock.bar_end_time.eq(pd.Timestamp(
                f"{next_day} 09:40:00")), "latest_price"]
            if high.gt(0).all() and first5 > 0 and len(close40) == 1:
                records.append({"symbol6": code, "second_high": float(np.sort(high)[-2]),
                                "close_40": float(close40.iloc[0])})
    observed = {row["symbol6"] for row in records}
    missing = [code for code in codes if code not in observed]
    age = (datetime.now(SHANGHAI).date() - date.fromisoformat(next_day)).days
    if missing and 0 <= age <= 65:
        # The write-behind minute table can miss individual historical bars
        # or the current morning. Page the provider's recent 1m API back to
        # the exact requested date; never accept a neighboring day's bars.
        target_date = int(next_day.replace("-", ""))
        required = set(range(9 * 60 + 31, 9 * 60 + 41))

        def one(code: str):
            offset = max(0, round(age * 5 / 7) * 240 - 240)
            for _ in range(12):
                try:
                    raw = request_bars(code, offset, 600)
                except Exception:
                    return None
                dates = sorted({int(r["sttDateTime"]["iDate"]) for r in raw})
                if not dates:
                    return None
                by_minute = {int(r["sttDateTime"]["shtTime"]): r for r in raw
                             if int(r["sttDateTime"]["iDate"]) == target_date}
                if required.issubset(by_minute):
                    morning = [by_minute[k] for k in sorted(required)]
                    high = [float(r["fHigh"]) for r in morning]
                    if min(high) <= 0 or sum(float(r["lVolume"]) for r in morning[:5]) <= 0:
                        return None
                    return {"symbol6": code, "second_high": sorted(high)[-2],
                            "close_40": float(morning[-1]["fClose"])}
                if target_date < dates[0] or (target_date in dates and
                                                by_minute and min(by_minute) > min(required)):
                    offset += 480 if target_date not in dates else 240
                elif target_date > dates[-1] or (target_date in dates and
                                                   by_minute and max(by_minute) < max(required)):
                    offset = max(0, offset - (480 if target_date not in dates else 240))
                else:
                    return None
            return None

        with ThreadPoolExecutor(max_workers=12) as pool:
            for row in pool.map(one, missing):
                if row is not None:
                    records.append(row)
    return pd.DataFrame(records, columns=["symbol6", "second_high", "close_40"])


def seven_factors(quotes: pd.DataFrame, day: str, prior_days: list[str],
                  daily: pd.DataFrame, valuation: pd.DataFrame) -> pd.DataFrame:
    if quotes.empty:
        return pd.DataFrame(columns=["signal_date", "next_date", "symbol6", "name",
                                     "signal_price", "amount_so_far", *FEATURES])
    codes = set(quotes.symbol6)
    # Keep one extra date when available to check the first prior day's
    # adjustment link. The frozen cohort requires exactly 20 prior dates,
    # not 21; newly listed stocks can therefore enter on their 21st day.
    history = daily[daily.trade_date.le(prior_days[-1]) &
                    daily.symbol6.isin(codes)].copy()
    groups = {code: group.sort_values("trade_date")
              for code, group in history.groupby("symbol6")}
    value = valuation[valuation.trade_date.eq(prior_days[-1]) &
                      valuation.symbol6.isin(codes)].set_index("symbol6")
    numeric = ("signal_price", "preclose", "volume_hands", "amount_so_far", "low_so_far")
    for col in numeric:
        quotes[col] = pd.to_numeric(quotes[col], errors="coerce")
    made = []
    for quote in quotes.itertuples(index=False):
        h = groups.get(quote.symbol6)
        if h is None or len(h) < 20 or h.trade_date.iloc[-20:].tolist() != prior_days[-20:]:
            continue
        h = h.tail(21)
        last20 = h.tail(20)
        numeric = last20[["close", "adjclose", "adjpreclose", "volume"]].apply(
            pd.to_numeric, errors="coerce")
        if (not np.isfinite(numeric.to_numpy()).all() or
                (numeric[["close", "adjclose"]] <= 0).any().any() or
                numeric.volume.tail(5).le(0).any()):
            continue
        previous = pd.to_numeric(h.adjclose.shift(1).tail(20), errors="coerce")
        linked = numeric.adjpreclose.to_numpy() / previous.to_numpy()
        if np.any(np.isfinite(linked) & (np.abs(linked - 1) > .01)):
            continue
        if quote.symbol6 not in value.index:
            continue
        v = value.loc[quote.symbol6]
        float_mv, float_share = float(v.float_mv), float(v.float_share)
        if (not np.isfinite(float_mv) or not np.isfinite(float_share) or
                float_mv <= 0 or float_share <= 0 or
                not all(np.isfinite(getattr(quote, col)) and getattr(quote, col) > 0
                        for col in ("signal_price", "preclose", "volume_hands", "low_so_far"))):
            continue
        ma = [numeric.adjclose.tail(n).mean() * quote.preclose /
              numeric.adjclose.iloc[-1] for n in (5, 10, 20)]
        volume5 = numeric.volume.tail(5).to_numpy()
        volume_shares = quote.volume_hands * 100
        made.append({"symbol6": quote.symbol6, "name": quote.name,
                     "signal_return": quote.signal_return,
                     "volume_ratio": volume_shares * 240 / (220 * volume5.mean()),
                     "turnover_so_far_pct": volume_shares / float_share * 100,
                     "float_mv_100m_cny": float_mv / 1e8,
                     "volume_4of5_increasing": four_of_five_increasing(volume5),
                     "ma_bull_5_10_20": int(ma[0] > ma[1] > ma[2]),
                     "all_intraday_lows_above_ma": int(quote.low_so_far > max(ma))})
    made = pd.DataFrame(made)
    if made.empty:
        return pd.DataFrame(columns=["signal_date", "next_date", "symbol6", "name",
                                     "signal_price", "amount_so_far", *FEATURES])
    result = made.merge(quotes[["symbol6", "signal_price", "amount_so_far", "preclose"]],
                        on="symbol6", validate="one_to_one")
    result["signal_date"] = day
    result["next_date"] = None
    return result


def exclude_st_at_limit(db, rows: pd.DataFrame, intervals: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return rows
    status = attach_st_status(rows, intervals)
    status["signal_date"] = status.signal_date.dt.strftime("%Y-%m-%d")
    status = mark_entry_limit(status)
    if (~status.entry_status_known).any():
        raise ValueError("历史ST状态不完整，不能确认14:40可交易性")
    return status[~(status.st_type.isin(("S", "Y")) & status.entry_at_limit)].copy()


def prepare(db, day: str, archive: Path | None, cache: Path) -> dict:
    calendar = database_calendar(db, day)
    daily, valuation = daily_sources(db, calendar)
    intervals = load_st_intervals(db)
    training_dates = calendar[-20:]
    rows = []
    for index, signal_day in enumerate(training_dates):
        history_dates = calendar[index:index+21]
        if len(history_dates) != 21 or history_dates[-1] >= signal_day:
            raise ValueError(f"{signal_day}: 前21日窗口不完整")
        quotes, source = database_signal(db, signal_day, daily, archive)
        frame = seven_factors(quotes, signal_day, history_dates, daily, valuation)
        frame = exclude_st_at_limit(db, frame, intervals)
        labels = morning_labels(db, day if index == 19 else training_dates[index+1],
                                frame.symbol6.tolist(), archive)
        frame = frame.merge(labels, on="symbol6", how="left", validate="one_to_one")
        if frame.second_high.notna().sum() == 0:
            raise ValueError(f"{signal_day}: 次晨标签全部缺失，不能跳过该训练日")
        frame["next_date"] = day if index == 19 else training_dates[index+1]
        rows.append(frame)
        print(f"训练数据 {signal_day}: 候选{len(frame)}，标签{frame.second_high.notna().sum()}，来源{source}", flush=True)
    training = pd.concat(rows, ignore_index=True)
    training = training[training.second_high.notna()].copy()
    model = train_model(generate_training_features(training),
                        generate_training_labels(training))
    cache.mkdir(parents=True, exist_ok=True)
    amount_cache(db, training_dates, cache, archive)
    training_file = cache / f"{day}_training.csv.gz"
    training[["signal_date", "next_date", "symbol6", "signal_price",
              *FEATURES, "second_high", "close_40"]].to_csv(
        training_file, index=False, float_format="%.17g",
        compression={"method": "gzip", "mtime": 0})
    model_file = cache / f"{day}_model.joblib"
    joblib.dump(model, model_file)
    meta = {"signal_date": day, "training_dates": training_dates,
            "training_rows": len(training), "feature_names": list(FEATURES),
            "sklearn_version": sklearn.__version__,
            "pipeline_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "training_sha256": hashlib.sha256(training_file.read_bytes()).hexdigest(),
            "model_sha256": hashlib.sha256(model_file.read_bytes()).hexdigest(),
            "prepared_at": datetime.now(SHANGHAI).isoformat()}
    (cache / f"{day}_model.json").write_text(json.dumps(meta, ensure_ascii=False,
                                                       indent=2) + "\n")
    return meta


def model_for_day(db, day: str, archive: Path | None, cache: Path):
    model_file = cache / f"{day}_model.joblib"
    meta_file = cache / f"{day}_model.json"
    if not model_file.exists() or not meta_file.exists():
        if day == datetime.now(SHANGHAI).date().isoformat():
            raise ValueError("今日模型尚未准备；请先在14:40前运行 prepare，定时任务会在13:50执行")
        prepare(db, day, archive, cache)
    meta = json.loads(meta_file.read_text())
    if (meta["signal_date"] != day or meta["feature_names"] != list(FEATURES) or
            meta.get("sklearn_version") != sklearn.__version__ or
            meta.get("pipeline_sha256") != hashlib.sha256(Path(__file__).read_bytes()).hexdigest() or
            meta["training_dates"] != database_calendar(db, day)[-20:] or
            hashlib.sha256((cache / f"{day}_training.csv.gz").read_bytes()).hexdigest() != meta["training_sha256"] or
            hashlib.sha256(model_file.read_bytes()).hexdigest() != meta["model_sha256"]):
        raise ValueError("缓存模型与本日训练窗口、sklearn版本或文件校验不符")
    return joblib.load(model_file), meta


def live_quotes(db, day: str, calendar: list[str]) -> tuple[pd.DataFrame, dict]:
    now = datetime.now(SHANGHAI)
    if now.date().isoformat() != day or not time(14, 40) <= now.time() < time(14, 41):
        raise ValueError("行情接口仅能在当日14:40:00–14:40:59采集精确信号")
    universe = query(db, "SELECT LEFT(stk_code,6) AS symbol6 FROM kcrp_stock_price "
                     "WHERE trade_date=%s", (calendar[-1],))
    codes = sorted(universe.symbol6.astype(str).str.zfill(6).unique())
    quotes, metadata = fetch_full_market(codes)
    started = datetime.fromisoformat(metadata["request_started_at"])
    ended = datetime.fromisoformat(metadata["request_ended_at"])
    begin = datetime.fromisoformat(f"{day}T14:40:00+08:00")
    finish = datetime.fromisoformat(f"{day}T14:41:00+08:00")
    if not (begin <= started and ended < finish and len(quotes) == len(codes)):
        raise ValueError("全市场行情未在14:40窗口内完整返回，拒绝使用非精确快照")
    return quotes, metadata


def prior_amounts(db, code: str, dates: list[str], archive: Path | None) -> list[float | None]:
    return _prior_amounts(db, code, dates, archive, None)


def amount_cache(db, dates: list[str], cache: Path, archive: Path | None) -> None:
    """Refresh once per historical date; subsequent days reuse 19 of 20."""
    for day in dates:
        path = cache / f"amounts_{day}.csv.gz"
        if path.exists():
            continue
        rows = query(db, """SELECT LEFT(stk_code,6) AS symbol6,
            COUNT(*) AS bars,COUNT(DISTINCT bar_end_time) AS distinct_bars,
            SUM(amount) AS amount_so_far,
            SUM(is_finalized=1 AND is_fallback=0 AND
                source_snapshot_time=bar_end_time) AS exact_bars
            FROM aiia_stock_realtime_minute_snapshot_full
            WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
              AND bar_end_time BETWEEN %s AND %s GROUP BY stk_code""",
            (day, f"{day} 09:31:00", f"{day} 14:40:00"))
        if rows.empty and archive is not None:
            bars = archive_day(archive, day)
            bars = bars[bars.time.between(f"{day} 09:31:00", f"{day} 14:40:00")]
            rows = bars.groupby("symbol6").agg(
                bars=("time", "size"), distinct_bars=("time", "nunique"),
                amount_so_far=("turnover", "sum")).reset_index()
            rows["exact_bars"] = rows.bars
        rows = rows[rows.bars.eq(220) & rows.distinct_bars.eq(220) &
                    rows.exact_bars.eq(220)][["symbol6", "amount_so_far"]]
        if rows.empty:
            raise ValueError(f"{day}: 个股14:40成交额缓存无法从DB或归档取得")
        rows.to_csv(path, index=False, compression="gzip")
        print(f"成交额缓存 {day}: {len(rows)} 股", flush=True)


def _prior_amounts(db, code: str, dates: list[str], archive: Path | None,
                   cache: Path | None) -> list[float | None]:
    out = []
    for day in dates:
        cached = cache / f"amounts_{day}.csv.gz" if cache is not None else None
        if cached is not None and cached.exists():
            rows = pd.read_csv(cached, dtype={"symbol6": str})
            match = rows[rows.symbol6.eq(code)]
            out.append(float(match.amount_so_far.iloc[0]) if len(match) == 1 else None)
            continue
        result = query(db, """SELECT COUNT(*) AS bars,
            COUNT(DISTINCT bar_end_time) AS distinct_bars,SUM(amount) AS amount_so_far,
            SUM(is_finalized=1 AND is_fallback=0 AND
                source_snapshot_time=bar_end_time) AS exact_bars
            FROM aiia_stock_realtime_minute_snapshot_full
            WHERE trade_date=%s AND stk_code=%s AND kline_type='1m'
              AND period_minutes=1 AND bar_end_time BETWEEN %s AND %s""",
            (day, code, f"{day} 09:31:00", f"{day} 14:40:00"))
        r = result.iloc[0]
        if r.bars == 220 and r.distinct_bars == 220 and r.exact_bars == 220:
            out.append(float(r.amount_so_far))
        elif archive is not None:
            bars = archive_day(archive, day)
            own = bars[bars.symbol6.eq(code) & bars.time.between(
                f"{day} 09:31:00", f"{day} 14:40:00")]
            out.append(float(own.turnover.sum()) if len(own) == 220 and
                       own.time.nunique() == 220 else None)
        else:
            out.append(None)
    return out


def write_connection(env_file: Path):
    load_dotenv(env_file)
    source = os.getenv("KINGDOMAI_DB_CREDENTIAL_SOURCE", "")
    raw = (os.getenv("KINGDOMAI_DB_URL") or
           (os.getenv(source) if source.isidentifier() else None))
    if not raw:
        raise ValueError("未配置KINGDOMAI_DB_URL或KINGDOMAI_DB_CREDENTIAL_SOURCE")
    parsed = urlparse(raw.replace("mysql+pymysql://", "mysql://", 1))
    host = os.getenv("KINGDOMAI_DB_HOST") or parsed.hostname
    port = int(os.getenv("KINGDOMAI_DB_PORT") or parsed.port or 3306)
    if host != "47.94.1.2" or port != 3312:
        raise ValueError("写入目标必须是当前配置的kingdomai主机和端口")
    return pymysql.connect(host=host, port=port,
                           user=unquote(os.getenv("KINGDOMAI_DB_USER") or parsed.username or ""),
                           password=unquote(os.getenv("KINGDOMAI_DB_PASSWORD") or parsed.password or ""),
                           database="kingdomai", charset="utf8mb4", autocommit=False,
                           cursorclass=pymysql.cursors.DictCursor, connect_timeout=8)


def init_db(env_file: Path) -> None:
    # Full-line SQL comments may contain punctuation, including semicolons.
    # Remove them before splitting this repository's simple CREATE statements.
    sql = "\n".join(line for line in (SCHEMA.read_text() + "\n" +
                                 (SCHEMA.parent / "strategy_execution.sql").read_text()).splitlines()
                    if not line.lstrip().startswith("--"))
    statements = [part.strip() for part in sql.split(";") if part.strip()]
    with write_connection(env_file) as db:
        with db.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
        db.commit()


def backfill_execution(env_file: Path, user_id: str, strategy_id: str,
                       as_of_date: str) -> dict:
    """Adopt saved selections once; existing price observations remain frozen."""
    with write_connection(env_file) as db:
        try:
            with db.cursor() as cursor:
                cursor.execute("""SELECT s.*,d.title AS strategy_name
                    FROM automl_strategy_selection AS s
                    JOIN automl_strategy_definition AS d
                      ON d.user_id=s.user_id AND d.strategy_id=s.strategy_id
                    WHERE s.user_id=%s AND s.strategy_id=%s AND s.signal_date<=%s
                    ORDER BY s.signal_date,s.selection_rank""",
                    (user_id, strategy_id, as_of_date))
                rows = cursor.fetchall()
                counts = Counter(row["signal_date"] for row in rows)
                for row in rows:
                    context = row["context_json"]
                    if isinstance(context, str):
                        context = json.loads(context)
                    signal_date = row["signal_date"].isoformat()
                    signal_at = row["signal_capture_at"]
                    if signal_at is not None:
                        signal_at = str(signal_at)
                    environment = {"source": "legacy_selection",
                                   "sklearn": context.get("sklearn_version"),
                                   "training_sha256": context.get("training_sha256"),
                                   "prepared_at": context.get("prepared_at"),
                                   "quote_source": row["quote_source"]}
                    record_run(
                        cursor, user_id=user_id, strategy_id=strategy_id,
                        strategy_name=row["strategy_name"], signal_date=signal_date,
                        signal_at=signal_at, candidate_count=row["candidate_count"],
                        selection_count=counts[row["signal_date"]],
                        model_sha256=context.get("model_sha256"),
                        environment=environment)
                    record_decisions(
                        cursor, user_id=user_id, strategy_id=strategy_id,
                        strategy_name=row["strategy_name"], signal_date=signal_date,
                        picks=[{"selection_rank": row["selection_rank"],
                                "symbol6": row["stock_code"], "name": row["stock_name"],
                                "actionable": row["actionable"],
                                "signal_price": row["signal_price"],
                                "predicted_class": row["predicted_class"],
                                "turnover_gate_pass": row["turnover_gate_pass"],
                                "p_lt0": row["p_lt0"], "p_0to1": row["p_0to1"],
                                "p_1to3": row["p_1to3"], "p_ge3": row["p_ge3"]}],
                        signal_at=signal_at,
                        model_sha256=context.get("model_sha256"),
                        environment=environment)
                settled = settle_previous(cursor, user_id=user_id,
                                          strategy_id=strategy_id,
                                          as_of_date=as_of_date)
            db.commit()
            return {"adopted_selection_rows": len(rows),
                    "settled_previous": settled}
        except Exception:
            db.rollback()
            raise


def save_selection(env_file: Path, user_id: str, day: str, picks: list[dict],
                   meta: dict, candidate_count: int, source: str,
                   captured_at: str | None, *, strategy_id: str = STRATEGY_ID,
                   strategy_definition: dict | None = None,
                   extra_context: dict | None = None) -> list[dict]:
    strategy_definition = strategy_definition or DEFINITION
    definition = json.dumps(strategy_definition, ensure_ascii=False, sort_keys=True)
    definition_hash = hashlib.sha256(definition.encode()).hexdigest()
    with write_connection(env_file) as db:
        try:
            with db.cursor() as cursor:
                cursor.execute("SELECT definition_sha256 FROM automl_strategy_definition "
                               "WHERE user_id=%s AND strategy_id=%s FOR UPDATE",
                               (user_id, strategy_id))
                existing = cursor.fetchone()
                if existing and existing["definition_sha256"] != definition_hash:
                    raise ValueError("策略定义已改变；请使用新的strategy_id版本")
                if not existing:
                    cursor.execute("""INSERT INTO automl_strategy_definition
                        (user_id,strategy_id,title,summary,training_method,
                         selection_formula,execution_formula,formula_json,definition_sha256)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (user_id, strategy_id, strategy_definition["title"],
                         strategy_definition["summary"], strategy_definition["training_method"],
                         strategy_definition["selection_formula"],
                         strategy_definition["execution_formula"],
                         json.dumps(strategy_definition["formula"], ensure_ascii=False),
                         definition_hash))
                cursor.execute("DELETE FROM automl_strategy_selection "
                               "WHERE user_id=%s AND strategy_id=%s AND signal_date=%s",
                               (user_id, strategy_id, day))
                for pick in picks:
                    features = {k: pick.get(k) for k in FEATURES}
                    context = {"amount_prior20_cny": pick.get("amount_prior20_cny"),
                               "st_type": pick.get("st_type"),
                               "preclose": clean_number(pick.get("preclose")),
                               "limit_price": clean_number(pick.get("limit_price")),
                               "entry_at_limit": bool(pick.get("entry_at_limit", False)),
                               "model_sha256": meta["model_sha256"],
                               "training_sha256": meta["training_sha256"],
                               "sklearn_version": meta.get("sklearn_version"),
                               "prepared_at": meta["prepared_at"]}
                    if extra_context:
                        context.update(extra_context)
                    cursor.execute("""INSERT INTO automl_strategy_selection (
                        user_id,strategy_id,signal_date,selection_rank,stock_code,stock_name,
                        signal_price,signal_return,predicted_class,p_lt0,p_0to1,p_1to3,p_ge3,
                        volume_ratio,turnover_so_far_pct,float_mv_100m_cny,
                        volume_4of5_increasing,ma_bull_5_10_20,all_intraday_lows_above_ma,
                        amount_1440_cny,amount_prior20_median_cny,amount_prior20_valid_days,
                        turnover_gate_pass,actionable,candidate_count,training_rows,
                        training_dates_json,features_json,context_json,quote_source,signal_capture_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (user_id, strategy_id, day, pick["selection_rank"], pick["symbol6"],
                         pick["name"], pick["signal_price"], pick["signal_return"],
                         pick["predicted_class"], pick["p_lt0"], pick["p_0to1"],
                         pick["p_1to3"], pick["p_ge3"], pick["volume_ratio"],
                         pick["turnover_so_far_pct"], pick["float_mv_100m_cny"],
                         pick["volume_4of5_increasing"], pick["ma_bull_5_10_20"],
                         pick["all_intraday_lows_above_ma"], pick["amount_so_far"],
                         pick["amount_prior20_median_cny"], pick["amount_prior20_valid_days"],
                         int(pick["turnover_gate_pass"]), int(pick["actionable"]),
                         candidate_count, meta["training_rows"],
                         json.dumps(meta["training_dates"]), json.dumps(features),
                         json.dumps(context, ensure_ascii=False), source, captured_at))
                environment = {
                    "python": platform.python_version(),
                    "platform": platform.platform(),
                    "sklearn": sklearn.__version__,
                    "numpy": np.__version__,
                    "scipy": scipy.__version__,
                    "pandas": pd.__version__,
                    "pipeline_sha256": meta.get("pipeline_sha256"),
                    "training_sha256": meta["training_sha256"],
                    "prepared_at": meta["prepared_at"],
                    "quote_source": source,
                }
                settled = settle_previous(cursor, user_id=user_id,
                                          strategy_id=strategy_id, as_of_date=day)
                record_run(cursor, user_id=user_id, strategy_id=strategy_id,
                           strategy_name=strategy_definition["title"],
                           signal_date=day, signal_at=captured_at,
                           candidate_count=candidate_count, selection_count=len(picks),
                           model_sha256=meta["model_sha256"],
                           environment=environment)
                record_decisions(cursor, user_id=user_id, strategy_id=strategy_id,
                                 strategy_name=strategy_definition["title"],
                                 signal_date=day, picks=picks, signal_at=captured_at,
                                 model_sha256=meta["model_sha256"],
                                 environment=environment)
            db.commit()
            return settled
        except Exception:
            db.rollback()
            raise


def score(db, day: str, model, meta: dict, archive: Path | None,
          output: Path, cache: Path) -> dict:
    calendar = database_calendar(db, day)
    now = datetime.now(SHANGHAI)
    if day == now.date().isoformat() and now.time() < time(14, 40):
        raise ValueError(f"上海时间尚未到14:40（现在{now:%H:%M:%S}），请稍后执行")
    in_live_window = (day == now.date().isoformat() and
                      time(14, 40) <= now.time() < time(14, 41))
    if in_live_window:
        # Capture the 14:40 quote before loading 41 days of daily history.
        quotes, metadata = live_quotes(db, day, calendar)
        quotes = select_candidates(quotes)
        source = "full_market_quote_api"
        captured_at = datetime.fromisoformat(metadata["request_ended_at"]).replace(
            tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    daily, valuation = daily_sources(db, calendar)
    if not in_live_window:
        bar = query(db, "SELECT COUNT(*) AS n FROM aiia_stock_realtime_minute_snapshot_full "
                    "WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1 "
                    "AND bar_end_time=%s", (day, f"{day} 14:40:00"))
        if int(bar.n.iloc[0]) > 0:
            quotes, source = database_signal(db, day, daily, archive)
            captured_at = f"{day} 14:40:00"
        elif day == now.date().isoformat():
            raise ValueError("已过14:40接口采集窗口，且数据库尚无该时点分钟K；请等待数据入库后重试")
        else:
            if archive is None:
                raise ValueError(f"{day} 14:40 DB数据不存在；需指定 --archive-dir")
            today_daily = query(db, "SELECT trade_date,stk_code,preclose "
                                "FROM kcrp_stock_price WHERE trade_date=%s", (day,))
            today_daily["symbol6"] = today_daily.stk_code.astype(str).str[:6].str.zfill(6)
            today_daily["trade_date"] = today_daily.trade_date.map(iso)
            quotes = archived_signal(archive, day, today_daily)
            source, captured_at = "archive", f"{day} 14:40:00"
    candidates = seven_factors(quotes.copy(), day, calendar[-21:], daily, valuation)
    candidates = exclude_st_at_limit(db, candidates, load_st_intervals(db))
    if candidates.empty:
        picks, full = [], candidates
    else:
        full = infer(model, candidates)
        selected = select_top(full)
        picks = selected.to_dict("records")
    for pick in picks:
        prior = _prior_amounts(db, pick["symbol6"], calendar[-20:], archive, cache)
        valid = [v for v in prior if v is not None]
        median = float(np.median(valid)) if len(valid) == 20 else None
        amount = clean_number(pick.get("amount_so_far"))
        gate = median is not None and amount is not None and amount >= median
        pick["amount_prior20_cny"] = prior
        pick["amount_prior20_median_cny"] = median
        pick["amount_prior20_valid_days"] = len(valid)
        pick["turnover_gate_pass"] = bool(gate)
        pick["actionable"] = int(pick["selection_rank"]) == 1 and gate
    output.mkdir(parents=True, exist_ok=True)
    if not full.empty:
        full.to_csv(output / f"{day}_all_predictions.csv.gz", index=False,
                    compression="gzip")
    result = {"signal_date": day, "strategy_id": STRATEGY_ID,
              "strategy_summary": DEFINITION["summary"],
              "training_dates": meta["training_dates"],
              "training_rows": meta["training_rows"],
              "candidate_count": len(candidates), "quote_source": source,
              "signal_capture_at": captured_at,
              "picks": [{k: (v.item() if isinstance(v, np.generic) else v)
                         for k, v in p.items()} for p in picks],
              "top1_actionable": bool(picks and picks[0]["actionable"]),
              "orders_placed": 0}
    (output / f"{day}_decision.json").write_text(json.dumps(result, ensure_ascii=False,
                                                         indent=2, default=str) + "\n")
    return result


def extract_tar(archive_tar: Path, needed: list[str], output: Path) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    missing = [day for day in needed if not (output / f"{day}.csv").exists()]
    if missing:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as file:
            file.write("".join(f"mk/none/{day}.csv\n" for day in missing))
            file.flush()
            subprocess.run(["tar", "-xJf", str(archive_tar), "-C", str(output),
                            "-T", file.name, "--strip-components=2"], check=True)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("init-db", "backfill-execution", "prepare", "run"),
                        nargs="?", default="run")
    parser.add_argument("--date", default=datetime.now(SHANGHAI).date().isoformat())
    parser.add_argument("--user-id", default=getpass.getuser())
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--cache", type=Path, default=ROOT / "outputs/stock_automl/live_1440/cache")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/stock_automl/live_1440")
    parser.add_argument("--archive-dir", type=Path)
    parser.add_argument("--archive-tar", type=Path)
    parser.add_argument("--write-db", action="store_true", help="Persist both ranks to kingdomai")
    args = parser.parse_args()
    day = date.fromisoformat(args.date).isoformat()
    if not args.user_id or len(args.user_id) > 128:
        parser.error("--user-id必须是1至128字符")
    if args.mode == "init-db":
        init_db(args.env_file)
        print("kingdomai策略表和选股结果表已准备")
        return 0
    if args.mode == "backfill-execution":
        if not args.write_db:
            parser.error("backfill-execution须显式指定--write-db")
        result = backfill_execution(args.env_file, args.user_id, STRATEGY_ID, day)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    now = datetime.now(SHANGHAI)
    if args.mode == "run" and day == now.date().isoformat() and now.time() < time(14, 40):
        print(f"上海时间{now:%H:%M:%S}，尚未到14:40；本次没有采集、推理或写库")
        return 0
    archive = args.archive_dir
    with db_connection(args.env_file) as db:
        trading = query(db, "SELECT is_trade_day FROM aiia_trade_calendar "
                        "WHERE market_code='CN_A' AND calendar_date=%s", (day,))
        if len(trading) == 1 and int(trading.is_trade_day.iloc[0]) == 0:
            print(f"{day}为非交易日；本次不训练、不推理、不写库")
            return 0
        if args.archive_tar:
            dates = database_calendar(db, day)
            # The supplied mk.tar.xz contains unadjusted May–July 2026 only.
            backup_dates = [d for d in dates[-41:] + [day]
                            if "2026-05-01" <= d <= "2026-07-31"]
            archive = extract_tar(args.archive_tar, backup_dates,
                                  args.output / "archive_cache")
        if args.mode == "prepare":
            result = prepare(db, day, archive, args.cache)
        else:
            model, meta = model_for_day(db, day, archive, args.cache)
            result = score(db, day, model, meta, archive, args.output, args.cache)
    if args.mode == "run" and args.write_db:
        result["settled_previous"] = save_selection(
            args.env_file, args.user_id, day, result["picks"], meta,
            result["candidate_count"], result["quote_source"],
            result["signal_capture_at"])
        result["saved_to_kingdomai"] = True
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        sys.exit(1)
