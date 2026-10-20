"""R50: monthly B2 daily six-month fit ending three months before test.

The four-K layer keeps the original six-train/two-validation rule selection.
Stage-one daily forecasts are causal for each historical shape-training month.
No production or database writes. Restartable by stage/subcommand.
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import platform
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn

from scripts import experiment_b2_validation_r31 as r31
from scripts import experiment_limit_rebound_four_minutes_r24 as four
from scripts import experiment_limit_rebound_shape_r19 as shape
from scripts.experiment_automl_limit_rebound_st_size_r4 import balanced_weights
from scripts.experiment_b2_monthly_hybrid_sample_r49 import R34, R35, score_month
from scripts.experiment_limit_rebound_monthly_r14 import block
from scripts.experiment_limit_rebound_segments_r18 import FEATURES
from scripts.experiment_limit_rebound_short_mixed_r12 import make_model, xmatrix
from scripts.experiment_limit_rebound_two_stage_r15 import classify, rank
from scripts.export_limit_rebound_short_mixed_r12 import filtered_local
from scripts.import_tushare_minutes_2024 import convert
from scripts.mk_minute_archive import COMPACT_ROOT, COMPACT_ROOT_2025, day_file
from scripts.replay_limit_rebound_cash_r23 import dump, sha

OUT = Path("outputs/stock_automl/b2_daily_gap3_sample_r50_20261020")
DOC = Path("docs/stock_automl_runs/20261020_b2_daily_gap3_sample_r50")
R49 = Path("outputs/stock_automl/b2_monthly_hybrid_sample_r49_20261020")
R18 = Path("outputs/stock_automl/limit_rebound_segments_r18_20261015")
STATUS = Path("outputs/stock_automl/limit_rebound_shape_r19_20261015/historical_status.csv.gz")
RAW_TUSHARE = Path("/Volumes/ext/tushare_market_data")
MONTHS = ["2024-04", "2024-08", "2024-10", "2025-04", "2025-06", "2026-09"]
HISTORY = sorted({str(h) for month in MONTHS
                  for h in pd.period_range(*r31.window(month, 2)["train"], freq="M")})
TRAIN_MONTHS = sorted(set(HISTORY) | set(MONTHS))
KEY = ["symbol", "d0_date"]


def window(month: str) -> dict:
    return r31.window(month, 3)


def specification() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    value = dict(experiment="R50 monthly B2 daily gap-three sample", months=MONTHS,
                 historical_forecast_months=HISTORY, stage_one_model_months=TRAIN_MONTHS,
                 daily_model="B2 fixed RF51 240 trees depth7 leaf50 max_features0.8 seed20261013 balanced weights sklearn1.7.1",
                 daily_window="fit M-9..M-4 six calendar months; M-3..M-1 score/observe only; predict all D0 in M; no daily validation model selection",
                 shape_window="original six causal historical forecast months M-8..M-3 and two target-model validation months M-2..M-1; original 79 rules and selection guards",
                 execution="original B2 D0 Top3, D1 first four bars, chronological lock, body>-8%, same-stock next qualified minute open through09:40, D2 close, fee0.1% each side",
                 account="separate 1m D0-month cohorts ending after final D2, no shadow gate; same as R49",
                 selection="Six months fixed from R49 baseline high/low list before R50 test outcomes were read",
                 panel_sha256=sha(R34 / "panel.pkl"), calendar_sha256=sha(R34 / "calendar.json"),
                 prior_enriched_sha256={"one_month": sha(R34 / "enriched.pkl"),
                                        "two_month": sha(R35 / "enriched.pkl")},
                 status_sha256=sha(STATUS), python=platform.python_version(),
                 sklearn=sklearn.__version__, pandas=pd.__version__, numpy=np.__version__,
                 production_changed=False)
    path = OUT / "specification.json"
    if path.exists():
        assert json.loads(path.read_text()) == value
    else:
        dump(path, value)
    return value


def score_path(dv: int, month: str, part: str) -> Path:
    assert dv == 3
    return OUT / "daily3" / month / f"segments_{part}_scores.pkl"


def configure() -> None:
    r31.OUT, r31.BASE = OUT, R34
    r31.MONTHS, r31.VARIANTS = MONTHS, {"D3S2": (3, 2)}
    r31.source = score_path
    r31.spec = specification
    r31.extension.daily_high_low = daily_high_low
    r31.extension.with_status = with_status
    r31.enrich_day = enrich_day


def train_one(month: str, panel: pd.DataFrame, calendar: list[str]) -> str:
    root = OUT / "daily3" / month
    root.mkdir(parents=True, exist_ok=True)
    if (root / "metrics.json").exists():
        return month
    w = window(month)
    tr = block(panel, calendar, *w["train"])
    va = block(panel, calendar, *w["valid"], purge_events=False)
    te = panel[panel.d0_date.str.startswith(month)]
    assert not set(zip(tr.symbol, tr.first_limit_date)) & set(zip(va.symbol, va.first_limit_date))
    fit = tr[tr.activated & tr.observed_3].copy()
    model = make_model("rf")
    model.steps[-1][1].n_jobs = 1
    model.fit(xmatrix(fit, FEATURES["segments"]), fit.label_3,
              randomforestclassifier__sample_weight=balanced_weights(fit.label_3))
    fit.to_pickle(root / "training_samples.pkl")
    bundle = dict(model=model, features=FEATURES["segments"], month=month, window=w,
                  sklearn=sklearn.__version__, training_keys=fit[KEY + ["first_limit_date"]].to_dict("records"),
                  specification_sha256=sha(OUT / "specification.json"))
    joblib.dump(bundle, root / "segments.joblib", compress=3)
    restored = joblib.load(root / "segments.joblib")["model"]
    np.testing.assert_array_equal(model.predict_proba(xmatrix(fit.iloc[:50], FEATURES["segments"])),
                                  restored.predict_proba(xmatrix(fit.iloc[:50], FEATURES["segments"])))
    metrics = {}
    for part, frame in (("train", tr), ("valid", va), ("forecast", te)):
        scored = rank(frame, restored.predict_proba(xmatrix(frame, FEATURES["segments"]))[:, 1])
        scored[KEY + ["score", "d0_rank"]].to_pickle(score_path(3, month, part))
        metrics[part] = classify(scored)
    dump(root / "metrics.json", dict(window=w, fit_rows=len(fit), model_sha256=sha(root / "segments.joblib"),
                                      sample_sha256=sha(root / "training_samples.pkl"), metrics=metrics))
    return month


def train() -> None:
    assert sklearn.__version__ == "1.7.1", sklearn.__version__
    configure()
    specification()
    panel = pd.read_pickle(R34 / "panel.pkl")
    calendar = json.loads((R34 / "calendar.json").read_text())
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(train_one, month, panel, calendar) for month in TRAIN_MONTHS]
        for future in as_completed(futures):
            print("trained", future.result(), flush=True)


def daily_high_low(demand: pd.DataFrame) -> pd.DataFrame:
    early = json.loads((R34 / "daily_sources.json").read_text())["months"]
    late = json.loads((R18 / "source_manifest.json").read_text())["raw_sources"]
    months = sorted(set(demand.d1_date.dropna().str[:7]))
    wanted = set(demand.symbol)
    frames = []
    for month in months:
        item = next((x for x in early + late if f"price_{month}.csv.gz" in x["path"]), None)
        assert item is not None, month
        path = Path(item["path"])
        assert sha(path) == item["sha256"]
        a = pd.read_csv(path, dtype={"stk_code": str, "trade_date": str},
                        usecols=["stk_code", "trade_date", "high", "low"])
        a["symbol"] = a.stk_code.str[:6]
        frames.append(a[a.symbol.isin(wanted)][["symbol", "trade_date", "high", "low"]])
    quotes = pd.concat(frames, ignore_index=True).drop_duplicates(["symbol", "trade_date"], keep="last")
    return quotes.rename(columns={"trade_date": "d1_date", "high": "d1_high", "low": "d1_low"})


def with_status(demand: pd.DataFrame) -> pd.DataFrame:
    history = pd.read_csv(STATUS, dtype=str)
    history["symbol"] = history.stk_code.str[:6]
    intervals = {sym: list(zip(g.begin_date, g.end_date, g.st_type)) for sym, g in history.groupby("symbol")}
    for which in (0, 1):
        found = []
        for row in demand.itertuples():
            day = getattr(row, f"d{which}_date")
            if pd.isna(day):
                found.append("unknown")
                continue
            matches = [state for begin, end, state in intervals.get(row.symbol, ()) if begin <= day < end]
            assert len(matches) == 1, (row.symbol, day, matches)
            found.append(matches[0])
        demand[f"state{which}"] = found
    demand["ordinary"] = demand.state0.isin(["N", "R"]) & demand.state1.isin(["N", "R"])
    return demand


def old_evidence() -> tuple[dict, dict]:
    facts = pd.concat([pd.read_pickle(R34 / "minute_facts.pkl"),
                       pd.read_pickle(R35 / "minute_facts.pkl")], ignore_index=True)
    facts = facts.drop_duplicates(["symbol", "d1_date"]).set_index(["symbol", "d1_date"])
    assert facts.index.is_unique
    facts_by_key = facts.to_dict(orient="index")
    first = pd.concat([pd.read_pickle(R34 / "first10.pkl"),
                       pd.read_pickle(R35 / "first10.pkl")], ignore_index=True)
    first["symbol"] = first.symbol.str[:6]
    duplicates = first[first.duplicated(KEY + ["time"], keep=False)]
    for col in ("open", "high", "low", "close", "volume"):
        spread = duplicates.groupby(KEY + ["time"])[col].agg(lambda x: float(x.max() - x.min()))
        assert spread.le(1e-10).all(), col
    first = first.drop_duplicates(KEY + ["time"])
    bars = {(sym, d0): group.sort_values("time") for (sym, d0), group in first.groupby(KEY)}
    return facts_by_key, bars


_FACTS: dict | None = None
_BARS: dict | None = None
_FRESH_SOURCES: dict[str, str] = {}


def fresh_bars(day: str, wanted: set[str]) -> tuple[dict[str, pd.DataFrame], str]:
    if day[:4] in {"2023", "2024"}:
        path = RAW_TUSHARE / day[:4] / f"{day.replace('-', '')}.csv.gz"
        if not path.exists():
            return {}, str(path)
        selected = {sym: [] for sym in wanted}
        with gzip.open(path, "rb") as source:
            header = source.readline()
            assert header.startswith(b"ts_code,trade_time,")
            for line in source:
                sym = line[:6].decode("ascii", errors="ignore")
                if sym in selected:
                    selected[sym].append(line)
        groups = {}
        for sym, rows in selected.items():
            if rows:
                try:
                    raw = pd.read_csv(io.BytesIO(header + b"".join(rows)),
                                      dtype={"ts_code": str, "trade_time": str})
                    groups[sym] = convert(raw, day).assign(symbol=sym).sort_values("time")
                except ValueError:
                    pass
    else:
        root = COMPACT_ROOT_2025 if day.startswith("2025") else COMPACT_ROOT
        try:
            path = day_file(root, day)
            raw = filtered_local(path, wanted)
        except FileNotFoundError:
            return {}, str(root / day)
        groups = {sym[:6]: g.assign(symbol=sym[:6]).sort_values("time")
                  for sym, g in raw.groupby("symbol")} if len(raw) else {}
    _FRESH_SOURCES[str(path)] = sha(path)
    return groups, str(path)


def enrich_day(day: str, demand: pd.DataFrame):
    assert _FACTS is not None and _BARS is not None
    records, extracts, missing = [], [], []
    for row in demand.itertuples():
        key = (row.symbol, day)
        fact = _FACTS.get(key)
        if fact is None or (fact["audit_ok"] and
                                       (row.symbol, row.d0_date) not in _BARS):
            missing.append(row)
            continue
        z = dict(symbol=row.symbol, d1_date=day, **fact)
        records.append(z)
        if z["audit_ok"]:
            extracts.append(_BARS[(row.symbol, row.d0_date)])
    if missing:
        groups, source = fresh_bars(day, {row.symbol for row in missing})
        for row in missing:
            bars = groups.get(row.symbol, pd.DataFrame())
            z = dict(symbol=row.symbol, d1_date=day, minute_source=source,
                     **shape.facts(bars, row))
            if z["audit_ok"]:
                z.update(four.first_features(bars, row.d1_preclose, row.d1_cap, 4))
                z.update(four.entry_facts(bars, row.d1_cap, 4))
                extracts.append(bars.iloc[:10].assign(d0_date=row.d0_date))
            records.append(z)
    return records, extracts


def prepare() -> None:
    global _FACTS, _BARS
    configure()
    assert all(score_path(3, month, "forecast").exists() for month in TRAIN_MONTHS)
    _FACTS, _BARS = old_evidence()
    r31.prepare()
    dump(OUT / "fresh_minute_sources.json", _FRESH_SOURCES)
    f = pd.read_pickle(OUT / "enriched.pkl")
    old = pd.concat([pd.read_pickle(R34 / "enriched.pkl"),
                     pd.read_pickle(R35 / "enriched.pkl")], ignore_index=True).drop_duplicates(KEY)
    shared = f.drop_duplicates(KEY).merge(old[KEY + ["ordinary", "state0", "state1", "audit_ok"]],
                                          on=KEY, suffixes=("_new", "_old"))
    for field in ("ordinary", "state0", "state1", "audit_ok"):
        assert shared[f"{field}_new"].fillna("missing").eq(shared[f"{field}_old"].fillna("missing")).all(), field
    dump(OUT / "coverage_control.json", dict(shared_rows=len(shared), total_unique=len(f.drop_duplicates(KEY)),
                                                shared_status_and_audit_exact=True))


def select() -> None:
    configure()
    assert (OUT / "enriched.pkl").exists()
    r31.select_rules()


def evaluate() -> None:
    rules = json.loads((OUT / "rules_frozen.json").read_text())
    f = pd.read_pickle(OUT / "enriched.pkl")
    first = pd.read_pickle(OUT / "first10.pkl")
    first["symbol"] = first.symbol.str[:6]
    bars = {(sym, day): g.sort_values("time") for (sym, day), g in first.groupby(KEY)}
    calendar = json.loads((R34 / "calendar.json").read_text())
    one = pd.read_csv(R49 / "monthly_comparison.csv")
    old = pd.read_pickle(R35 / "signals.pkl")
    old_rules = {x["month"]: x["selected"]
                 for x in json.loads((R35 / "rules_frozen.json").read_text())}
    picks, accounts, overlap = [], [], []
    for record in rules:
        month = record["month"]
        q = f[f.model_month.eq(month) & f.part.eq("test")]
        strategies = (("日线6+3形态重选", record["selected"]),
                      ("日线6+3原月规则", old_rules[month]))
        for variant, rule in strategies:
            p = four.delay_same_stock(four.chosen(q, rule), bars)
            p["variant"], p["rule_id"] = variant, rule["id"]
            summary, ledger = score_month(p, month, calendar)
            accounts.append(dict(variant=variant, **summary))
            picks.append(p)
            ledger.to_pickle(OUT / f"ledger_{month}_{variant}.pkl")
        reference = old[old.d0_date.str.startswith(month)]
        selected = picks[-2]
        hkeys = set(zip(selected.symbol, selected.d0_date))
        bkeys = set(zip(reference.symbol, reference.d0_date))
        overlap.append(dict(month=month, common=len(hkeys & bkeys), new_only=len(hkeys-bkeys),
                            original_only=len(bkeys-hkeys), reselected_rule=record["selected"]["id"],
                            original_rule=old_rules[month]["id"]))
    new = pd.DataFrame(accounts)
    baseline = one[one.month.isin(MONTHS)].copy()
    baseline = baseline.assign(variant=baseline.variant.map({"原B2两层6+2": "原B2 6+2", "日线6+1形态6+2": "日线6+1形态6+2"}))
    table = pd.concat([baseline, new], ignore_index=True)
    table.to_csv(OUT / "monthly_comparison.csv", index=False, encoding="utf-8-sig")
    panel = pd.read_pickle(R34 / "panel.pkl")
    model_metrics = []
    for month in MONTHS:
        sources = (("原B2 6+2", (R35 / "daily2" / month / "segments_forecast_scores.pkl")
                    if month < "2025-01" else (R18 / month / "segments_forecast_scores.pkl")),
                   ("日线6+1形态6+2", R34 / "daily1" / month / "segments_forecast_scores.pkl"),
                   ("日线6+3", score_path(3, month, "forecast")))
        for variant, path in sources:
            scored = pd.read_pickle(path).merge(panel, on=KEY, validate="one_to_one")
            metrics = classify(scored)
            model_metrics.append(dict(month=month, variant=variant,
                                      all_top1=metrics["all"]["top1"]["rate"],
                                      all_top3=metrics["all"]["top3"]["rate"],
                                      open_top1=metrics["open"]["top1"]["rate"],
                                      open_top3=metrics["open"]["top3"]["rate"],
                                      score_sha256=sha(path)))
    pd.DataFrame(model_metrics).to_csv(OUT / "daily_model_metrics.csv", index=False, encoding="utf-8-sig")
    pd.concat(picks, ignore_index=True).to_pickle(OUT / "signals.pkl")
    dump(OUT / "candidate_overlap.json", overlap)
    dump(OUT / "verification.json", dict(all_shape_rules_frozen_before_test=True,
                                          new_model_count=len(TRAIN_MONTHS),
                                          baseline_source_sha256=sha(R49 / "monthly_comparison.csv"),
                                          source_model_hashes={m: sha(OUT / "daily3" / m / "segments.joblib")
                                                               for m in TRAIN_MONTHS}))
    print(table[["month", "variant", "trades", "hit_rate", "mean_d2_net", "return_", "max_drawdown"]]
          .sort_values(["month", "variant"]).to_string(index=False), flush=True)


def audit() -> None:
    assert sklearn.__version__ == "1.7.1"
    panel = pd.read_pickle(R34 / "panel.pkl")
    calendar = json.loads((R34 / "calendar.json").read_text())
    reloads = []
    for month in TRAIN_MONTHS:
        root = OUT / "daily3" / month
        bundle = joblib.load(root / "segments.joblib")
        assert bundle["window"] == window(month)
        expected = block(panel, calendar, *window(month)["train"])
        expected = expected[expected.activated & expected.observed_3]
        fit = pd.read_pickle(root / "training_samples.pkl")
        pd.testing.assert_frame_equal(fit[KEY].reset_index(drop=True),
                                      expected[KEY].reset_index(drop=True))
        target = panel[panel.d0_date.str.startswith(month)]
        scored = rank(target, bundle["model"].predict_proba(xmatrix(target, FEATURES["segments"]))[:, 1])
        saved = pd.read_pickle(score_path(3, month, "forecast"))
        joined = scored[KEY + ["score", "d0_rank"]].merge(saved, on=KEY, validate="one_to_one",
            suffixes=("_new", "_saved"))
        diff = float((joined.score_new - joined.score_saved).abs().max())
        assert diff < 1e-12 and joined.d0_rank_new.eq(joined.d0_rank_saved).all()
        reloads.append(dict(month=month, window=window(month), fit_rows=len(fit),
                            scored_rows=len(joined), max_score_diff=diff,
                            model_path=str(root / "segments.joblib"),
                            model_sha256=sha(root / "segments.joblib"),
                            training_sample_path=str(root / "training_samples.pkl"),
                            training_sample_sha256=sha(root / "training_samples.pkl"),
                            validation_score_sha256=sha(score_path(3, month, "valid")),
                            forecast_score_sha256=sha(score_path(3, month, "forecast"))))
    signals = pd.read_pickle(OUT / "signals.pkl")
    for path in OUT.glob("ledger_*.pkl"):
        ledger = pd.read_pickle(path)
        assert ledger.exit_date.notna().all()
        assert ledger.sell.eq(ledger.d2_close).all()
        np.testing.assert_allclose(ledger.return_, ledger.sell / ledger.buy * .999 / 1.001 - 1,
                                   atol=1e-12, rtol=0)
    assert not signals.d0_rank.gt(3).any()
    dump(OUT / "model_reload_verification.json", reloads)
    dump(OUT / "audit.json", dict(model_count=len(reloads), exact_forecast_scores=True,
                                  exact_training_keys=True, d2_close_and_fees_exact=True,
                                  all_models_reloaded=len(reloads) == len(TRAIN_MONTHS),
                                  minute_coverage=json.loads((OUT / "coverage.json").read_text()),
                                  prior_overlap=json.loads((OUT / "coverage_control.json").read_text())))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("train", "prepare", "select", "evaluate", "audit", "all"), default="all", nargs="?")
    args = parser.parse_args()
    stages = ("train", "prepare", "select", "evaluate", "audit") if args.stage == "all" else (args.stage,)
    for stage in stages:
        globals()[stage]()


if __name__ == "__main__":
    main()
