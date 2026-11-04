"""B2 daily snapshots, monthly fitting, and source minute adapters."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import requests
import sklearn

from scripts.automl_b2 import FEATURE_ORDER, build_features, historical_states
from scripts.benchmark_automl_1440_inference import query
from scripts.build_automl_limit_rebound_features import prepare_daily
from scripts.experiment_limit_rebound_short_mixed_r12 import make_model, xmatrix, dump, sha
from scripts.experiment_limit_rebound_two_stage_r15 import window, rank
from scripts.experiment_limit_rebound_monthly_r14 import block
from scripts.experiment_automl_limit_rebound_st_size_r4 import balanced_weights
from scripts.experiment_limit_rebound_four_minutes_r24 import first_features, entry_facts, choose_rule
from scripts.experiment_limit_rebound_shape_r19 import facts, rules


PRICE_COLS = "trade_date,stk_code,preclose,open,high,low,close,volume,amount,turn_ratio,is_limit_price"
MINUTE_URL = "http://jzyzwup.upoem1.com/json/hq_marketdata/kLineData"


def runtime() -> dict:
    import scipy
    return {"sklearn": sklearn.__version__, "numpy": np.__version__, "pandas": pd.__version__, "scipy": scipy.__version__}


def calendar(db) -> list[str]:
    f = query(db, "SELECT calendar_date FROM aiia_trade_calendar WHERE market_code='CN_A' AND is_trade_day=1 ORDER BY calendar_date")
    return f.calendar_date.astype(str).tolist()


def refresh_daily(db, root: Path, through: str) -> pd.DataFrame:
    """Append completed source days; never silently rewrite a frozen old snapshot."""
    path = root / "live_daily.joblib"
    raw = joblib.load(path if path.exists() else root / "daily.joblib")
    latest = str(raw.trade_date.max())
    if latest < through:
        new = query(db, f"SELECT {PRICE_COLS} FROM kcrp_stock_price WHERE trade_date>%s AND trade_date<=%s ORDER BY trade_date,stk_code", (latest, through))
        if new.empty or str(new.trade_date.max()) != through:
            raise ValueError("目标日日K尚未到齐，保持既有模型和名单")
        new["trade_date"] = new.trade_date.astype(str)
        # A whole-market model cannot score from a partially loaded daily batch.
        previous = raw[raw.trade_date.eq(latest)].stk_code.nunique()
        if new.groupby("trade_date").stk_code.nunique().lt(previous * .98).any():
            raise ValueError("日K全市场覆盖不足，等待源表完成")
        raw = pd.concat([raw, new], ignore_index=True)
        temporary = path.with_suffix(".next")
        joblib.dump(raw, temporary, compress=3); temporary.replace(path)
    daily = prepare_daily(raw[raw.trade_date.le(through)])
    daily["date_i"] = daily.trade_date.map({d: i for i, d in enumerate(sorted(daily.trade_date.unique()))})
    return daily


def status_source(db, root: Path) -> pd.DataFrame:
    f = query(db, "SELECT stk_code,st_type,begin_date,end_date FROM kcrp_stock_st")
    f.to_csv(root / "status_latest.csv.gz", index=False, compression="gzip")
    return f


def model_bundle(root: Path, month: str) -> tuple[dict, dict, dict]:
    p = root / "months" / month
    meta = json.loads((p / "manifest.json").read_text())
    for name, digest in meta["files"].items():
        if sha(p / name) != digest:
            raise ValueError(f"冻结资产哈希不符: {month}/{name}")
    bundle = joblib.load(p / "model.joblib")
    if bundle["sklearn"] != sklearn.__version__ or sklearn.__version__ != "1.7.1":
        raise ValueError("B2计算环境必须使用sklearn1.7.1")
    if bundle["features"] != FEATURE_ORDER:
        raise ValueError("B2因子顺序改变")
    return bundle, json.loads((p / "rule.json").read_text()), meta


def score(bundle: dict, panel: pd.DataFrame) -> pd.DataFrame:
    if panel.empty:
        return panel.assign(score=pd.Series(dtype=float), d0_rank=pd.Series(dtype=int))
    return rank(panel, bundle["model"].predict_proba(xmatrix(panel, bundle["features"]))[:, 1])


def api_minutes(symbol: str, day: str) -> tuple[pd.DataFrame, dict]:
    market = 1 if symbol.startswith("6") else 7 if symbol.startswith(("9", "4", "8")) else 0
    request = {"stReq": {"stHeader": {"shtMarket": market}, "sCode": symbol,
                         "eLineType": 1, "shtStartxh": 0, "shtWantNum": 260}}
    begin = datetime.now().isoformat()
    response = requests.post(MINUTE_URL, json=request, timeout=(3, 5))
    response.raise_for_status()
    raw = response.json().get("stRsp", {}).get("vAnalyData", [])
    rows = []
    for item in raw:
        stamp = item["sttDateTime"]
        text = datetime.strptime(str(stamp["iDate"]), "%Y%m%d").strftime("%Y-%m-%d")
        if text != day:
            continue
        end = datetime.fromisoformat(day) + timedelta(minutes=int(stamp["shtTime"]))
        rows.append({"time": end, "open": float(item["fOpen"]), "high": float(item["fHigh"]),
                     "low": float(item["fLow"]), "close": float(item["fClose"]),
                     "volume": float(item.get("lVolume") or 0) * 100})
    f = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
    return f, {"source": MINUTE_URL, "requested_at": begin, "received_at": datetime.now().isoformat(),
               "source_response_sha256": __import__("hashlib").sha256(response.content).hexdigest()}


def history_minutes(db, root: Path, symbol: str, day: str) -> pd.DataFrame:
    path = root / "minute_history" / f"{day}_{symbol}.pkl"
    if path.exists():
        return pd.read_pickle(path)
    f = query(db, """SELECT bar_end_time AS time,open_price AS open,high_price AS high,
        low_price AS low,latest_price AS close,volume FROM aiia_stock_realtime_minute_snapshot_full
        WHERE trade_date=%s AND stk_code=%s AND kline_type='1m' AND period_minutes=1
        AND is_finalized=1 AND is_fallback=0 AND source_snapshot_time=bar_end_time ORDER BY bar_end_time""", (day, symbol))
    if len(f):
        for col in ("open", "high", "low", "close", "volume"):
            f[col] = pd.to_numeric(f[col])
        f["volume"] *= 100
    if len(f) == 240:
        path.parent.mkdir(exist_ok=True); f.to_pickle(path)
    return f


def shape_training(db, root: Path, frame: pd.DataFrame, daily: pd.DataFrame,
                   intervals: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    f = frame[frame.d0_rank.eq(1)].copy()
    if f.empty:
        raise ValueError("形态选择没有Top1历史样本")
    quotes = daily.set_index(["symbol", "trade_date"])
    states = historical_states(pd.concat([
        f[["symbol", "d0_date"]].rename(columns={"d0_date": "asof_date"}),
        f[["symbol", "d1_date"]].rename(columns={"d1_date": "asof_date"})]), intervals).set_index(["symbol", "asof_date"])
    rows = []; evidence = []
    exclusion_file = root / "minute_history/exclusions.json"
    original_exclusions = json.loads(exclusion_file.read_text()) if exclusion_file.exists() else {}
    for r in f.to_dict("records"):
        key = (r["symbol"], r["d1_date"])
        ordinary = all(states.loc[(r["symbol"], r[f"d{k}_date"]), "st_type"] in ("N", "R") for k in (0, 1))
        if not ordinary:
            continue
        exclusion = original_exclusions.get(f"{key[1]}_{key[0]}")
        if exclusion:
            evidence.append({"symbol": key[0], "day": key[1], "audit_ok": False,
                             "reason": exclusion, "source": "frozen_R20_exclusion"})
            continue
        q = quotes.loc[key]; r.update(d1_high=float(q.high), d1_low=float(q.low))
        b = history_minutes(db, root, *key)
        if b.empty and r["d1_volume"] > 0:
            raise ValueError(f"月度形态训练缺分钟源: {key}")
        z = facts(b, pd.Series(r))
        evidence.append({"symbol": key[0], "day": key[1], "audit_ok": z["audit_ok"], "reason": z["reason"]})
        if not z["audit_ok"]:
            continue
        r.update(first_features(b, r["d1_preclose"], r["d1_cap"], 4))
        r.update(entry_facts(b, r["d1_cap"], 4))
        if not r["fill"] or not r["observed_3"]:
            continue
        good = r["d1_close"] > 0
        for k in (2, 3, 4):
            good = good and r[f"d{k}_close"] > 0 and abs(r[f"d{k}_preclose"] - r[f"d{k-1}_close"]) <= .011
            r[f"ret{k}"] = r[f"d{k}_close"] / r["entry"] - 1 if good else np.nan
        r["strategy"] = r["ret2"] if r["d2_up"] else r["ret3"] if r["d3_up"] else r["ret4"]
        if np.isfinite(r["ret4"]):
            r.update(eligible=True, trade=True, complete=True); rows.append(r)
    return pd.DataFrame(rows), evidence


def prepare_month(db, root: Path, month: str, daily: pd.DataFrame, intervals: pd.DataFrame) -> dict:
    """New calendar month only; frozen months are loaded, never refitted."""
    final = root / "months" / month
    if final.exists():
        return model_bundle(root, month)[2]
    if daily.trade_date.max() < str(pd.Period(month) - 1) + "-01":
        raise ValueError("新月训练/验证源尚未结束")
    work = root / "building" / month
    work.mkdir(parents=True, exist_ok=True)
    w = window(month); cal = sorted(daily.trade_date.unique())
    # Rebuild only the required scoring span, with daily history before it retained.
    p = build_features(daily, intervals, work / "features", w["train"][0] + "-01")
    tr = block(p, cal, *w["train"]); va = block(p, cal, *w["valid"], purge_events=False)
    fitted = tr[tr.activated & tr.observed_3]
    if len(fitted) < 100 or fitted.label_3.nunique() != 2:
        raise ValueError("新月日线训练样本不足")
    model = make_model("rf"); model.steps[-1][1].n_jobs = 1
    weights = balanced_weights(fitted.label_3)
    model.fit(xmatrix(fitted, FEATURE_ORDER), fitted.label_3, randomforestclassifier__sample_weight=weights)
    bundle = {"model": model, "features": FEATURE_ORDER, "variant": "segments", "month": month,
              "sklearn": sklearn.__version__, "training_keys": fitted[["symbol", "d0_date", "first_limit_date"]].to_dict("records")}
    joblib.dump(bundle, work / "model.joblib", compress=3)
    fitted.assign(sample_weight=weights).to_pickle(work / "training.pkl")
    va = score(bundle, va); va.to_pickle(work / "validation_scores.pkl")
    oof = []
    for historical_month in pd.period_range(*w["train"], freq="M"):
        old, _, _ = model_bundle(root, str(historical_month))
        # D0 ranks precede training-event purging. Purging a higher-ranked
        # event must not promote the next stock into the historical Top1.
        oof.append(score(old, p[p.d0_date.str.startswith(str(historical_month))]))
    oof = pd.concat(oof, ignore_index=True).merge(
        tr[["symbol", "d0_date"]], on=["symbol", "d0_date"], validate="one_to_one")
    oof.to_pickle(work / "oof_scores.pkl")
    shape_tr, audit_tr = shape_training(db, root, oof, daily, intervals)
    shape_va, audit_va = shape_training(db, root, va, daily, intervals)
    if len(shape_tr) < 15 or len(shape_va) < 6:
        raise ValueError("新月形态选择数据不足，不能静默沿用旧月份")
    frozen = choose_rule(shape_tr, shape_va, month, rules())
    shape_tr.to_pickle(work / "shape_train.pkl"); shape_va.to_pickle(work / "shape_valid.pkl")
    dump(work / "rule.json", frozen)
    dump(work / "minute_audit.json", {"train": audit_tr, "valid": audit_va})
    names = ["model.joblib", "training.pkl", "validation_scores.pkl", "oof_scores.pkl", "rule.json", "shape_train.pkl", "shape_valid.pkl", "minute_audit.json"]
    meta = {"month": month, "window": w, "runtime": runtime(), "source_through": str(daily.trade_date.max()),
            "training_rows": len(fitted), "created_at": datetime.now().isoformat(),
            "files": {name: sha(work / name) for name in names}}
    dump(work / "manifest.json", meta)
    # Persistence and numeric round trip are required before making the month visible.
    restored = joblib.load(work / "model.joblib")
    np.testing.assert_array_equal(model.predict_proba(xmatrix(va, FEATURE_ORDER)), restored["model"].predict_proba(xmatrix(va, FEATURE_ORDER)))
    work.rename(final)
    return meta
