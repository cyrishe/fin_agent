"""Run B2 after close and replay completed early minutes into simulation records."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
import time
from zoneinfo import ZoneInfo

import pandas as pd

from scripts.automl_b2 import STRATEGY_ID, TITLE, EXIT_RULE, definition, digest, evaluate_morning, historical_states, build_features
from scripts.automl_b2_data import calendar, refresh_daily, status_source, prepare_month, model_bundle, score, api_minutes, runtime
from scripts.readable_strategy_execution import record_run, identity
from scripts.strategy_execution_store import record_selection
from scripts.benchmark_automl_1440_inference import db_connection, query, fetch_full_market
from scripts.experiment_automl_limit_rebound_d0_open_r5 import cap_price
from scripts.experiment_limit_rebound_short_mixed_r12 import dump, sha
from scripts.run_automl_1440_strategy import write_connection, init_db


SHANGHAI = ZoneInfo("Asia/Shanghai")
IDENTITY_KEY = (identity(STRATEGY_ID)["strategy_name"], identity(STRATEGY_ID)["model_version"])


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, allow_nan=False)


def register(args):
    init_db(args.env_file)
    d = definition(); hashed = digest(d)
    with write_connection(args.env_file) as db:
        with db.cursor() as c:
            c.execute("SELECT definition_sha256 FROM automl_strategy_definition WHERE user_id=%s AND strategy_id=%s FOR UPDATE", (args.user_id, STRATEGY_ID))
            old = c.fetchone()
            if old and old["definition_sha256"] != hashed:
                raise ValueError("B2定义已冻结，改变公式必须使用新版本ID")
            if not old:
                c.execute("""INSERT INTO automl_strategy_definition (user_id,strategy_id,title,summary,
                    training_method,selection_formula,execution_formula,formula_json,definition_sha256)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (args.user_id, STRATEGY_ID, d["title"], d["summary"],
                    d["training_method"], d["selection_formula"], d["execution_formula"], json_text(d["formula"]), hashed))
        db.commit()
    return {"strategy_id": STRATEGY_ID, "definition_sha256": hashed}


def save_close(args, result):
    with write_connection(args.env_file) as db:
        try:
            with db.cursor() as c:
                record_run(c, user_id=args.user_id, strategy_id=STRATEGY_ID, strategy_name=TITLE,
                    signal_date=result["signal_date"], signal_at=result["signal_date"] + " 15:00:00",
                    candidate_count=result["candidate_count"], selection_count=len(result["picks"]),
                    model_sha256=result["model_sha256"], environment=result["environment"])
                for p in result["picks"]:
                    record_selection(c, user_id=args.user_id, **identity(STRATEGY_ID),
                        signal_date=result["signal_date"], selection_rank=p["selection_rank"],
                        stock_code=p["symbol6"], stock_name=p["name"], actionable=False,
                        signal_at=result["signal_date"] + " 15:00:00", signal_price=p["signal_price"],
                        details={**p, "legacy_strategy_id": STRATEGY_ID, "exit_rule": EXIT_RULE})
            db.commit()
        except Exception:
            db.rollback(); raise


def after_close(args):
    now = datetime.now(SHANGHAI)
    if args.day > now.date().isoformat() or (args.day == now.date().isoformat() and now.hour < 15):
        raise ValueError("盘后推理必须等待D0收盘")
    receipt = args.output / args.day / "close.json"
    if receipt.exists():
        result = json.loads(receipt.read_text())
        bundle, frozen, manifest = model_bundle(args.assets, args.day[:7])
        if result["model_sha256"] != manifest["files"]["model.joblib"] or result["rule_sha256"] != manifest["files"]["rule.json"]:
            raise ValueError("既有盘后快照与冻结模型不一致")
        if args.write_db:
            save_close(args, result)
        return result
    with db_connection(args.env_file) as db:
        cal = calendar(db)
        if args.day not in cal:
            return {"day": args.day, "note": "非交易日"}
        daily = refresh_daily(db, args.assets, args.day)
        intervals = status_source(db, args.assets)
        # The model month belongs to D0, including the final D0 before a new month.
        prepare_month(db, args.assets, args.day[:7], daily, intervals)
        bundle, frozen, meta = model_bundle(args.assets, args.day[:7])
        folder = args.output / args.day; folder.mkdir(parents=True, exist_ok=True)
        panel = build_features(daily, intervals, folder / "source", args.day)
        all_scores = score(bundle, panel)
        all_scores.to_pickle(folder / "all_scores.pkl")
        names = query(db, "SELECT LEFT(stk_code,6) AS symbol,stk_name FROM kcrp_stock_baseinfo")
        name_map = dict(zip(names.symbol, names.stk_name))
        states = historical_states(panel[["symbol", "d0_date"]].rename(columns={"d0_date": "asof_date"}), intervals).set_index("symbol")
        picks = []
        for r in all_scores.head(3).itertuples():
            values = {c: None if pd.isna(getattr(r, c)) else float(getattr(r, c)) for c in bundle["features"]}
            picks.append({"symbol6": r.symbol, "name": name_map.get(r.symbol, ""), "selection_rank": int(r.d0_rank),
                "score": float(r.score), "signal_price": float(r.close), "state0": states.loc[r.symbol, "st_type"],
                "rate": float(r.rate), "rule": frozen["selected"], "model_month": args.day[:7],
                "features": values, "recorded_at": now.isoformat(), "simulation_only": True})
        result = {"strategy_id": STRATEGY_ID, "signal_date": args.day, "candidate_count": len(all_scores),
            "d1_date": cal[cal.index(args.day) + 1], "model_sha256": meta["files"]["model.joblib"],
            "rule_sha256": meta["files"]["rule.json"], "picks": picks,
            "environment": {**runtime(), "definition_sha256": digest(definition()),
                "release": args.release, "monthly_manifest_sha256": sha(args.assets / "months" / args.day[:7] / "manifest.json"),
                "deployment_manifest_sha256": sha(Path("MANIFEST.json")) if Path("MANIFEST.json").exists() else None,
                "all_scores_sha256": sha(folder / "all_scores.pkl"), "simulation_only": True},
            "created_at": now.isoformat()}
        dump(receipt, result)
        db.rollback()
    if args.write_db:
        save_close(args, result)
    return result


def load_picks(args, day):
    with db_connection(args.env_file) as db:
        cal = calendar(db)
        if day not in cal:
            return None, []
        d0 = cal[cal.index(day) - 1]
        rows = query(db, """SELECT stock_code,selection_rank,details_json AS decision_json,actionable,buy_price AS entry_price
            FROM strategy_execution WHERE user_id=%s AND strategy_name=%s AND model_version=%s AND signal_date=%s ORDER BY selection_rank""", (args.user_id, *IDENTITY_KEY, d0))
        db.rollback()
    if rows.empty:
        return d0, []
    return d0, [json.loads(r.decision_json) if isinstance(r.decision_json, str) else r.decision_json for r in rows.itertuples()]


def persist_scan(args, d0, values, observed_at):
    with write_connection(args.env_file) as db:
        try:
            with db.cursor() as c:
                c.execute("""SELECT selection_rank,stock_code,actionable,buy_price AS entry_price,details_json AS decision_json FROM strategy_execution
                    WHERE user_id=%s AND strategy_name=%s AND model_version=%s AND signal_date=%s ORDER BY selection_rank FOR UPDATE""", (args.user_id, *IDENTITY_KEY, d0))
                previous = {int(r["selection_rank"]): r for r in c.fetchall()}
                if len(previous) != len(values) or set(previous) != {int(v["selection_rank"]) for v in values}:
                    raise ValueError("B2扫描与已存Top3数量不一致")
                if sum(int(v["actionable"]) for v in values) > 1:
                    raise ValueError("B2每天只能有一个actionable")
                for z in values:
                    rank = z["selection_rank"]; old = previous[rank]
                    if old["stock_code"] != z["symbol6"]:
                        raise ValueError("B2扫描股票身份改变")
                    saved = json.loads(old["decision_json"]) if isinstance(old["decision_json"], str) else old["decision_json"]
                    if saved.get("chosen") and not z.get("chosen"):
                        raise ValueError("已锁定候选发生变化，保留原记录并检查源数据")
                    if old["actionable"] and (not z["actionable"] or Decimal(str(old["entry_price"])) != Decimal(str(z["entry_price"]))):
                        raise ValueError("已确认模拟买点改变，不能覆盖")
                    # First observation times survive repeated scans and process restarts.
                    for fact, stamp in [("shape_ready", "shape_confirmed_at"), ("first_open_bar_end", "first_open_confirmed_at"),
                                        ("chosen", "choice_confirmed_at"), ("actionable", "actionable_confirmed_at")]:
                        if z.get(fact):
                            z[stamp] = saved.get(stamp) or observed_at.isoformat()
                    z.update(legacy_strategy_id=STRATEGY_ID, exit_rule=EXIT_RULE)
                    z["last_observed_at"] = observed_at.isoformat()
                    z["entry_price_basis"] = "completed_minute_open_simulation" if z["actionable"] else None
                    c.execute("""UPDATE strategy_execution SET actionable=%s,buy_at=%s,buy_price=%s,
                        buy_price_basis=%s,details_json=%s WHERE user_id=%s AND strategy_name=%s AND model_version=%s AND signal_date=%s AND selection_rank=%s""",
                        (z["actionable"], z.get("entry_at"), z.get("entry_price"), z["entry_price_basis"], json_text(z),
                         args.user_id, *IDENTITY_KEY, d0, rank))
            db.commit()
        except Exception:
            db.rollback(); raise


def scan(args):
    today = datetime.now(SHANGHAI).date().isoformat()
    if args.day != today:
        raise ValueError("在线扫描只接受今天；历史复现使用独立verify命令，不倒写实时信号")
    d0, picks = load_picks(args, args.day)
    if not picks:
        return {"day": args.day, "signal_date": d0, "note": "没有盘后候选；不生成伪信号"}
    _, frozen_rule, _ = model_bundle(args.assets, d0[:7])
    if any(p["rule"] != frozen_rule["selected"] or p["model_month"] != d0[:7] for p in picks):
        raise ValueError("盘后名单的月份或形态规则与冻结资产不一致")
    with db_connection(args.env_file) as db:
        intervals = status_source(db, args.assets)
        keys = pd.DataFrame([{"symbol": p["symbol6"], "asof_date": args.day} for p in picks])
        states = historical_states(keys, intervals).set_index("symbol")
        db.rollback()
    # Quote endpoint is used solely for the day's known reference close, never for a simulated entry.
    quotes, quote_meta = fetch_full_market([p["symbol6"] for p in picks], batch_size=3, workers=1, timeout=5)
    q = quotes.set_index("symbol6")
    for p in picks:
        code = p["symbol6"]
        if code not in q.index:
            raise ValueError("候选昨收参考价缺失")
        pre = float(q.loc[code, "preclose"])
        if pre <= 0 or abs(pre - p["signal_price"]) > .011:
            raise ValueError("D1参考价与D0收盘不连续，保持待核对")
        p.update(preclose=pre, cap=cap_price(pre, p["rate"]), state1=states.loc[code, "st_type"],
                 ordinary=p["state0"] in ("N", "R") and states.loc[code, "st_type"] in ("N", "R"))
    folder = args.output / d0; folder.mkdir(parents=True, exist_ok=True)
    start = datetime.fromisoformat(args.day + "T09:30:00").replace(tzinfo=SHANGHAI)
    end = datetime.fromisoformat(args.day + "T09:41:30").replace(tzinfo=SHANGHAI)
    if datetime.now(SHANGHAI) < start or datetime.now(SHANGHAI) > end:
        raise ValueError("在线扫描仅在09:30—09:41:30；不把盘后回看伪装成早盘提示")
    last = None
    while datetime.now(SHANGHAI) <= end:
        got = {}; sources = {}; errors = {}
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs = {pool.submit(api_minutes, p["symbol6"], args.day): p["symbol6"] for p in picks}
            for future in as_completed(jobs):
                code = jobs[future]
                try:
                    got[code], sources[code] = future.result()
                except Exception as exc:
                    errors[code] = type(exc).__name__
        now = datetime.now(SHANGHAI)
        with (folder / "minute_observations.jsonl").open("a") as f:
            f.write(json_text({"observed_at": now.isoformat(), "sources": sources, "errors": errors,
                "bars": {s: b.to_dict("records") for s, b in got.items()}}) + "\n")
        if not errors:
            values = evaluate_morning(picks, got, args.day, now, picks[0]["rule"])
            if args.write_db:
                persist_scan(args, d0, values, now)
            last = {"signal_date": d0, "day": args.day, "observed_at": now.isoformat(), "picks": values}
            dump(folder / "morning.json", last)
        if not args.watch:
            return last or {"day": args.day, "errors": errors}
        time.sleep(args.poll_seconds)
    return last or {"day": args.day, "note": "分钟源未完整，未标actionable"}


def settle(args):
    now = datetime.now(SHANGHAI)
    if args.day > now.date().isoformat() or (args.day == now.date().isoformat() and now.hour < 15):
        raise ValueError("收盘结算尚未到时")
    with write_connection(args.env_file) as db:
        try:
            cal = calendar(db)
            with db.cursor() as c:
                c.execute("""SELECT signal_date,selection_rank,stock_code,buy_price AS entry_price,details_json AS decision_json
                    FROM strategy_execution WHERE user_id=%s AND strategy_name=%s AND model_version=%s AND actionable=1
                    AND sell_price IS NULL AND JSON_UNQUOTE(JSON_EXTRACT(details_json,'$.exit_rule'))=%s ORDER BY signal_date,selection_rank FOR UPDATE""",
                    (args.user_id, *IDENTITY_KEY, EXIT_RULE))
                rows = c.fetchall(); completed = []
                for r in rows:
                    d0 = str(r["signal_date"]); d2 = cal[cal.index(d0) + 2]
                    if d2 > args.day:
                        continue
                    c.execute("SELECT close,preclose,volume FROM kcrp_stock_price WHERE trade_date=%s AND LEFT(stk_code,6)=%s", (d2, r["stock_code"]))
                    quotes = c.fetchall()
                    if len(quotes) != 1 or not quotes[0]["close"] or float(quotes[0]["volume"] or 0) <= 0:
                        continue
                    price = Decimal(str(quotes[0]["close"])); entry = Decimal(str(r["entry_price"]))
                    facts = json.loads(r["decision_json"]) if isinstance(r["decision_json"], str) else r["decision_json"]
                    # Match the research continuity requirement through D2.
                    d1 = cal[cal.index(d0) + 1]
                    c.execute("SELECT close FROM kcrp_stock_price WHERE trade_date=%s AND LEFT(stk_code,6)=%s", (d1, r["stock_code"]))
                    prior = c.fetchall()
                    if len(prior) != 1 or abs(Decimal(str(quotes[0]["preclose"])) - Decimal(str(prior[0]["close"]))) > Decimal("0.011"):
                        continue
                    gross = (price / entry - 1) * 100
                    net = (price * Decimal("0.999") / (entry * Decimal("1.001")) - 1) * 100
                    facts.update(net_return_pct=float(net), fee_per_side=.001, exit_confirmed_at=now.isoformat())
                    if args.write_db:
                        c.execute("""UPDATE strategy_execution SET sell_at=%s,sell_price=%s,sell_price_basis=%s,
                            gross_return_pct=%s,net_return_pct=%s,details_json=%s WHERE user_id=%s AND strategy_name=%s AND model_version=%s AND signal_date=%s
                            AND selection_rank=%s AND sell_price IS NULL""", (d2 + " 15:00:00", price,
                            "D2_daily_close_simulation", gross, net, json_text(facts), args.user_id, *IDENTITY_KEY, d0, r["selection_rank"]))
                    completed.append({"signal_date": d0, "rank": r["selection_rank"], "exit_date": d2,
                                      "gross_return_pct": float(gross), "net_return_pct": float(net)})
            if args.write_db:
                db.commit()
            else:
                db.rollback()
            return {"day": args.day, "settled": completed, "write_db": args.write_db}
        except Exception:
            db.rollback(); raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["register", "close", "scan", "settle"])
    p.add_argument("--env-file", type=Path, default=Path(".env"))
    p.add_argument("--assets", type=Path, default=Path("outputs/stock_automl/b2_assets_20261015"))
    p.add_argument("--output", type=Path, default=Path("outputs/stock_automl/b2_live"))
    p.add_argument("--user-id", default="imac")
    p.add_argument("--day", default=datetime.now(SHANGHAI).date().isoformat())
    p.add_argument("--write-db", action="store_true")
    p.add_argument("--watch", action="store_true")
    p.add_argument("--poll-seconds", type=float, default=3)
    p.add_argument("--release", default="B2_20261015")
    args = p.parse_args()
    if args.command == "register" and not args.write_db:
        print(json_text(definition())); return
    if args.poll_seconds < 1:
        raise ValueError("扫描间隔至少1秒")
    result = {"register": register, "close": after_close, "scan": scan, "settle": settle}[args.command](args)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str, allow_nan=False))


if __name__ == "__main__":
    main()
