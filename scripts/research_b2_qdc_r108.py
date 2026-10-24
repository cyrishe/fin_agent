"""Chronological B2 V2 extension to 2013-16, using the archived R90 pipeline.

Daily model and four-bar rules are refitted each month on historical windows.
Tushare official daily/basic fields restore preclose and turnover absent in QDC.
QDC is used separately for the source-sensitivity execution replay.
"""
from __future__ import annotations

import argparse
import gc
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.build_automl_limit_rebound_features import prepare_daily
from scripts import experiment_automl_limit_rebound_d0_open_r5 as r5
from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.experiment_automl_limit_rebound_d0_open_r5 import build_panel
from scripts.experiment_limit_rebound_short_mixed_r12 import add_features, enrich_outcomes
from scripts.experiment_limit_rebound_segments_r18 import segment_one
from scripts.replay_limit_rebound_cash_r23 import dump, sha

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/stock_automl/b2_qdc_r108_20261024"
RAW = OUT / "tushare_daily_pairs"
R90_PATH = ROOT / "docs/stock_automl_runs/20261023_b2_2018_2021_r90/code/run.py"
spec = importlib.util.spec_from_file_location("archived_b2_r90", R90_PATH)
assert spec and spec.loader
r90 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r90)
r90.ROOT = OUT
r90.MINUTE = Path("/Volumes/ext/tushare_market_data")
r90.MODELS = [str(m) for m in pd.period_range("2013-09", "2016-12", freq="M")]
r90.TEST = [str(m) for m in pd.period_range("2014-05", "2016-12", freq="M")]


def specification() -> dict:
    value = dict(
        experiment="R108 B2 V2 2013-16 chronological extension",
        parent="R90 frozen code and R107 QDC execution + Q75 + news",
        first_daily_forecast=r90.MODELS[0], first_full_test=r90.TEST[0],
        last_full_test=r90.TEST[-1], daily_train_months=6,
        daily_valid_months=2, shape_train_months=6, shape_valid_months=2,
        daily_source="Tushare official daily and daily_basic; 2013-01 through 2017-01",
        minute_source="Tushare minute for historical rule fitting; QDC minute for execution",
        historical_limit_band="2013-16 main board and ChiNext both 10%, matching archived R90 pre-2020 panel",
        reverse_transport="Q75 fitted on 2022-24; before 2022 is retrospective only",
        r90_code_sha256=sha(R90_PATH),
        sample_note="Training rows and monthly models retained under outputs/stock_automl/b2_qdc_r108_20261024",
    )
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "specification.json"
    if path.exists():
        assert json.loads(path.read_text()) == value
    else:
        dump(path, value)
    return value


def load_daily() -> tuple[pd.DataFrame, pd.DataFrame]:
    days = pd.read_csv(OUT / "trade_cal.csv", dtype=str)
    expected = sorted(days.loc[days.is_open.eq("1"), "cal_date"])
    for extension in sorted(OUT.glob("trade_cal_2017*.csv")):
        extra = pd.read_csv(extension, dtype=str)
        expected += sorted(extra.loc[extra.is_open.eq("1"), "cal_date"])
    frames = {}
    for name in ("daily", "daily_basic"):
        paths = sorted(RAW.glob(f"{name}_????????_????????.csv.gz"))
        assert paths, name
        frame = pd.concat([pd.read_csv(path, dtype={"ts_code": str, "trade_date": str})
                           for path in paths], ignore_index=True)
        repeated = frame[frame.duplicated(["ts_code", "trade_date"], keep=False)]
        if len(repeated):
            assert repeated.groupby(["ts_code", "trade_date"], dropna=False).nunique(
                dropna=False).le(1).all().all(), name
            frame = frame.drop_duplicates(["ts_code", "trade_date"])
        assert set(expected) == set(frame.trade_date), (name, len(expected), frame.trade_date.nunique())
        frames[name] = frame
    price = frames["daily"]
    basic = frames["daily_basic"]
    data = price.merge(basic[["ts_code", "trade_date", "turnover_rate", "total_share"]],
                       on=["ts_code", "trade_date"], how="left", validate="one_to_one")
    assert data.turnover_rate.notna().mean() > .97
    data["trade_date"] = pd.to_datetime(data.trade_date, format="%Y%m%d").dt.strftime("%Y-%m-%d")
    data["stk_code"] = data.ts_code
    data["preclose"] = data.pre_close
    data["volume"] = data.vol * 100.0
    data["amount"] = data.amount * 1000.0
    data["turn_ratio"] = data.turnover_rate
    data["is_limit_price"] = np.isclose(data.close,
                                         np.floor(data.preclose * 110 + .5000001) / 100,
                                         atol=.001, rtol=0).astype(int)
    calendar = sorted(data.trade_date.unique())
    assert calendar[0] == "2013-01-04"
    return data, basic


def status_cache(daily: pd.DataFrame, calendar: list[str]) -> dict:
    """Resolve historical ST intervals; conservatively omit uncovered old tickers."""
    board = daily[daily.up][["symbol", "trade_date", "date_i"]]
    first = board[["symbol", "trade_date"]].rename(columns={"trade_date": "asof_date"})
    following = board[board.date_i.add(1).lt(len(calendar))][["symbol", "date_i"]].copy()
    following["asof_date"] = following.date_i.add(1).map(dict(enumerate(calendar)))
    keys = pd.concat([first, following[["symbol", "asof_date"]]], ignore_index=True).drop_duplicates()
    with db_connection() as conn:
        history = query(conn, "SELECT stk_code,st_type,begin_date,end_date FROM kcrp_stock_st")
        conn.rollback()
    history["symbol"] = history.stk_code.str[:6]
    intervals = {symbol: list(zip(group.begin_date.astype(str), group.end_date.astype(str),
                                  group.st_type)) for symbol, group in history.groupby("symbol")}
    types = []
    for symbol, day in keys.itertuples(index=False):
        matches = [kind for begin, end, kind in intervals.get(symbol, ()) if begin <= day < end]
        if len(matches) > 1:
            raise ValueError(f"overlapping ST intervals {symbol} {day} {matches}")
        types.append(matches[0] if matches else "UNKNOWN")
    keys["st_type"] = types
    keys["is_st"] = keys.st_type.isin(("S", "Y", "UNKNOWN")).astype(int)
    unknown = keys[keys.st_type.eq("UNKNOWN")]
    unknown.to_csv(OUT / "unknown_historical_st.csv", index=False)
    keys.to_csv(OUT / "historical_st.csv.gz", index=False, compression="gzip")
    receipt = dict(status_keys=len(keys), unknown_stock_days=len(unknown),
                   unknown_symbols=unknown.symbol.nunique(),
                   unknown_2013_2016=unknown.groupby(unknown.asof_date.str[:4]).size().to_dict())
    dump(OUT / "historical_st_coverage.json", receipt)
    print("historical ST", receipt, flush=True)
    return receipt


def panel() -> None:
    specification()
    # The archived 2016-19 panel used the then-applicable 10% ChiNext band.
    # Current R5's static 20% helper would silently discard 2013-16 ChiNext.
    r5.normal_limit_rate = lambda symbol: .10 if symbol.startswith(("0", "6", "300")) else np.nan
    raw, basic = load_daily()
    source_rows = len(raw)
    source_days = raw.trade_date.nunique()
    cols = ["trade_date", "stk_code", "preclose", "open", "high", "low", "close",
            "volume", "amount", "turn_ratio", "is_limit_price"]
    for month, group in raw.groupby(raw.trade_date.str[:7]):
        path = OUT / "price_months" / f"price_{month}.csv.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            group[cols].to_csv(path, index=False, compression="gzip")
    basic.to_pickle(OUT / "tushare_basic_all.pkl")
    daily = prepare_daily(raw[cols])
    del raw, basic
    gc.collect()
    calendar = sorted(daily.trade_date.unique())
    daily["date_i"] = daily.trade_date.map({d: i for i, d in enumerate(calendar)})
    status_cache(daily, calendar)
    q, quality = build_panel(daily, OUT, score_all_d0=True, d0_start="2013-01-01")
    q = q[q.d0_date.lt("2017-01-01")].copy()
    print("basic panel", len(q), quality, flush=True)
    q = add_features(q, daily)
    q = enrich_outcomes(q, daily)
    q["first_i"] = q.first_limit_date.map({d: i for i, d in enumerate(calendar)})
    targets = {symbol: group for symbol, group in q.groupby("symbol", sort=False)}
    z = []
    for count, (symbol, group) in enumerate(daily.groupby("symbol", sort=False), 1):
        if symbol not in targets:
            continue
        positions = {day: i for i, day in enumerate(group.trade_date)}
        for row in targets[symbol].itertuples():
            feat, meta, _ = segment_one(group, positions[row.first_limit_date],
                                        positions[row.d0_date])
            assert meta["raw_board_days"] == row.board_streak_days
            z.append(dict(symbol=symbol, d0_date=row.d0_date, **feat))
        if count % 1000 == 0:
            print("segments", count, len(z), flush=True)
    q = q.merge(pd.DataFrame(z), on=["symbol", "d0_date"], validate="one_to_one")
    q.to_pickle(OUT / "panel.pkl")
    dump(OUT / "calendar.json", calendar)
    dump(OUT / "panel_manifest.json", dict(rows=len(q), daily_rows=source_rows,
                                              daily_days=source_days, quality=quality,
                                              panel_sha256=sha(OUT / "panel.pkl"),
                                              calendar_sha256=sha(OUT / "calendar.json")))
    print("panel ready", len(q), flush=True)


def repair_market_scope() -> None:
    """Apply archived market breadth universe without recalculating stock segments."""
    original = pd.read_pickle(OUT / "panel.pkl")
    raw, basic = load_daily()
    cols = ["trade_date", "stk_code", "preclose", "open", "high", "low", "close",
            "volume", "amount", "turn_ratio", "is_limit_price"]
    daily = prepare_daily(raw[cols])
    del raw, basic
    gc.collect()
    calendar = json.loads((OUT / "calendar.json").read_text())
    assert sorted(daily.trade_date.unique()) == calendar
    market = daily.groupby("trade_date").agg(
        market_limit_count=("up", "sum"),
        market_up_fraction=("ret", lambda value: float(value.gt(0).mean())),
        market_median_return=("ret", "median"))
    market["market_prev_limit_count"] = market.market_limit_count.shift(1)
    daily["date_i"] = daily.trade_date.map({day: i for i, day in enumerate(calendar)})
    grouped = daily.groupby("symbol", sort=False)
    prior = grouped.up.shift().eq(True) & daily.date_i.sub(grouped.date_i.shift()).eq(1)
    daily["prior_up"] = prior
    daily["continued"] = prior & daily.up
    reseal = daily.groupby("trade_date").agg(prior=("prior_up", "sum"),
                                               continued=("continued", "sum"))
    market["market_reseal_fraction"] = reseal.continued.div(reseal.prior.replace(0, np.nan))
    panel = original[original.symbol.str.startswith(("0", "6", "300"))].copy()
    for column in market.columns:
        panel[column] = panel.d0_date.map(market[column])
    panel["amount_board_rank"] = panel.groupby("d0_date").d0_amount_100m.rank(pct=True)
    panel["turnover_board_rank"] = panel.groupby("d0_date").d0_turnover_pct.rank(pct=True)
    assert len(panel) < len(original)
    original.to_pickle(OUT / "panel_pre_scope_repair.pkl")
    panel.to_pickle(OUT / "panel.pkl")
    receipt = dict(before=len(original), after=len(panel), removed_codes=sorted(
        set(original.symbol) - set(panel.symbol)), market_scope="R90 prepare_daily: 0/3/6/9",
        first_panel_sha256=sha(OUT / "panel_pre_scope_repair.pkl"),
        repaired_panel_sha256=sha(OUT / "panel.pkl"))
    dump(OUT / "market_scope_repair.json", receipt)
    manifest = json.loads((OUT / "panel_manifest.json").read_text())
    manifest.update(rows=len(panel), panel_sha256=receipt["repaired_panel_sha256"],
                    market_scope_repair=receipt)
    dump(OUT / "panel_manifest.json", manifest)
    print(receipt, flush=True)


def qdc() -> None:
    """Re-execute original Top3/rules on the new QDC source, including first-bar union."""
    from scripts import research_b2_qdc_r104 as old
    from scripts import research_b2_qdc_r105 as firstbar

    old.OUT = OUT
    candidate = pd.read_pickle(OUT / "enriched.pkl")
    candidate = candidate[candidate.part.eq("test") & candidate.d0_rank.le(3)].copy()
    candidate = candidate[candidate.d1_date.notna()].copy()
    calendar = json.loads((OUT / "calendar.json").read_text())
    index = {day: i for i, day in enumerate(calendar)}
    candidate["d2_date"] = candidate.d1_date.map(
        lambda day: calendar[index[day] + 1] if day in index and index[day] + 1 < len(calendar) else None)
    candidate = candidate[candidate.d2_date.notna()].copy()
    candidate["qdc_symbol"] = candidate.stk_code.map(old.qdc_symbol)
    assert not candidate.duplicated(["symbol", "d0_date"]).any()
    raw_daily, first10 = old.prepare_data(candidate, [2013, 2014, 2015, 2016, 2017])
    opening = firstbar.opening_rows(candidate)
    normalized, bar_audit = firstbar.merge_first_bar(first10, opening)
    normalized.to_pickle(OUT / "qdc_candidate_first10_normalized.pkl")
    bar_audit.to_csv(OUT / "first_bar_coverage.csv", index=False)
    candidate = old.attach_daily(candidate, raw_daily)
    rules = {row["month"]: row["selected"]
             for row in json.loads((OUT / "rules_frozen.json").read_text())}
    assert set(candidate.model_month.unique()) == set(r90.TEST)
    signals, decisions = old.choose(candidate, normalized, rules)
    annual, gates, ledger, shadow = old.summarize(signals, calendar, candidate,
                                                   [2014, 2015, 2016])
    candidate.to_pickle(OUT / "candidates.pkl")
    signals.to_pickle(OUT / "signals.pkl")
    decisions.to_csv(OUT / "decisions.csv", index=False)
    annual.to_csv(OUT / "v2_yearly.csv", index=False)
    gates.to_csv(OUT / "gates.csv", index=False)
    ledger.to_csv(OUT / "v2_ledger.csv", index=False)
    dump(OUT / "qdc_manifest.json", dict(candidates=len(candidate), signals=len(signals),
                                             normalized_bars=len(normalized),
                                             first30=int(bar_audit.has_0930.sum()),
                                             shadow=shadow))
    print(annual.to_string(index=False), flush=True)


def evaluate_historical() -> None:
    """R90 evaluation with an empty-month guard for historical test windows."""
    from scripts.experiment_limit_rebound_four_minutes_r24 import chosen, delay_same_stock
    r90.configure()
    rules = json.loads((OUT / "rules_frozen.json").read_text())
    frame = pd.read_pickle(OUT / "enriched.pkl")
    first = pd.read_pickle(OUT / "first10.pkl")
    first["symbol"] = first.symbol.str[:6]
    bars = {(symbol, day): part.sort_values("time")
            for (symbol, day), part in first.groupby(["symbol", "d0_date"])}
    picks, metrics = [], []
    for item in rules:
        month = item["month"]
        test = frame[frame.model_month.eq(month) & frame.part.eq("test")]
        selected = chosen(test, item["selected"])
        picked = delay_same_stock(selected, bars) if len(selected) else selected
        picked["rule_id"] = item["selected"]["id"]
        picked["rule_text"] = item["selected"]["text"].replace("五分", "四分")
        if len(picked):
            picks.append(picked)
        known = picked[picked.observed_3] if len(picked) else picked
        metrics.append(dict(month=month, n=len(known),
                            hits=int(known.label_3.sum()) if len(known) else 0,
                            hit=float(known.label_3.mean()) if len(known) else None))
        print("signal", month, len(picked), flush=True)
    signals = pd.concat(picks, ignore_index=True)
    assert not signals.d1_date.duplicated().any()
    assert signals.d0_date.str[:7].isin(r90.TEST).all()
    signals.to_pickle(OUT / "signals.pkl")
    dump(OUT / "shape_test_metrics.json", metrics)
    print("signals", len(signals), flush=True)


def run_stage(stage: str) -> None:
    if stage == "panel":
        panel()
    elif stage == "repair-market":
        repair_market_scope()
    elif stage == "qdc":
        qdc()
    elif stage == "prepare":
        # The archived R31 minute-preparation merge requires unique (symbol,D1).
        # Historical suspensions can give multiple distinct D0 rows no D1 at all;
        # none can be a valid four-bar trade or a complete shape-rule sample.
        original_assemble = r90.r.assemble
        def assemble_with_observable_d1():
            samples = original_assemble()
            missing = samples[samples.d1_date.isna()].copy()
            missing.to_csv(OUT / "missing_d1_stage_rows.csv", index=False)
            kept = samples[samples.d1_date.notna()].copy()
            kept.to_pickle(OUT / "stage_samples.pkl")
            dump(OUT / "missing_d1_exclusion.json", dict(
                stage_rows=len(missing), unique_stock_d0=len(missing.drop_duplicates(["symbol", "d0_date"])),
                by_part=missing.part.value_counts().to_dict()))
            print("excluded no D1", len(missing), flush=True)
            return kept
        r90.r.assemble = assemble_with_observable_d1
        r90.prepare()
    elif stage == "evaluate":
        evaluate_historical()
    else:
        r90.spec = specification
        getattr(r90, stage)()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["panel", "repair-market", "train", "prepare", "select", "evaluate", "qdc"])
    run_stage(parser.parse_args().stage)
