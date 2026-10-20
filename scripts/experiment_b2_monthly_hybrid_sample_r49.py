"""R49 exploratory monthly B2 daily 6+1 / four-K 6+2 paired sample.

Reuse the archived R34 daily 6+1 scores/minute facts and R35 original B2
daily 6+2 evidence. Freeze each month's shape rule before reading its test rows.
No production database or online strategy writes.
"""
from __future__ import annotations

import json
from pathlib import Path

import joblib
import pandas as pd

from scripts import experiment_b2_validation_r31 as r31
from scripts import experiment_limit_rebound_four_minutes_r24 as four
from scripts import experiment_limit_rebound_shape_r19 as shape
from scripts.experiment_limit_rebound_segments_r18 import FEATURES
from scripts.experiment_limit_rebound_short_mixed_r12 import xmatrix
from scripts.experiment_limit_rebound_two_stage_r15 import rank
from scripts.experiment_limit_rebound_monthly_r14 import block
from scripts.replay_limit_rebound_cash_r23 import dump, replay, sha

R34 = Path("outputs/stock_automl/b2_full_2024_r34_20261019")
R35 = Path("outputs/stock_automl/b2_two_month_2024_r35_20261019")
R31 = Path("outputs/stock_automl/b2_validation_r31_20261019")
OUT = Path("outputs/stock_automl/b2_monthly_hybrid_sample_r49_20261020")
MONTHS = ["2024-04", "2024-08", "2024-10", "2024-11",
          "2025-03", "2025-04", "2025-06", "2025-12",
          "2026-06", "2026-09"]
KEY = ["symbol", "d0_date"]


def segment(frame: pd.DataFrame, target: str, part: str) -> pd.DataFrame:
    return frame[frame.model_month.eq(target) & frame.part.eq(part)].copy()


def month_frames(one: pd.DataFrame, two: pd.DataFrame, panel: pd.DataFrame,
                 calendar: list[str], static: pd.DataFrame, month: str):
    previous = str(pd.Period(month, freq="M") - 1)
    first = str(pd.Period(month, freq="M") - 2)
    train = segment(one, previous, "train")
    # R31's two-month validation includes first-month D0s whose D4 label
    # matures in the following validation month. R34's one-month valid part
    # necessarily omits these, so score that complete first-month pool again.
    first_pool = panel[panel.d0_date.str.startswith(first)]
    model = joblib.load(R34 / "daily1" / previous / "segments.joblib")["model"]
    scored = rank(first_pool, model.predict_proba(xmatrix(first_pool, FEATURES["segments"]))[:, 1])
    allowed = block(panel, calendar, first, previous, purge_events=False)[KEY]
    first_ranked = scored[scored.d0_rank.eq(1)][KEY + ["score", "d0_rank"]].merge(
        allowed, on=KEY, validate="one_to_one")
    first_valid = first_ranked.merge(static, on=KEY, validate="one_to_one")
    first_valid["variant"], first_valid["model_month"], first_valid["part"] = "D1S2", month, "valid"
    valid = pd.concat([first_valid, segment(one, month, "valid")], ignore_index=True)
    test = segment(one, month, "test")
    original = {part: segment(two, month, part) for part in ("train", "valid", "test")}
    w = r31.window(month, 2)
    assert sorted(train.d0_date.str[:7].unique()) == [str(p) for p in pd.period_range(*w["train"], freq="M")]
    assert sorted(valid.d0_date.str[:7].unique()) == [str(p) for p in pd.period_range(*w["valid"], freq="M")]
    assert test.d0_date.str[:7].eq(month).all()
    assert not train.duplicated(KEY).any() and not valid.duplicated(KEY).any()
    assert not test.duplicated(KEY).any() and test.d0_rank.le(3).all()
    return {"train": train, "valid": valid, "test": test}, original


def choose(frames: dict, month: str) -> dict:
    calendar = json.loads((R34 / "calendar.json").read_text())
    w = r31.window(month, 2)
    training = []
    for part in ("train", "valid"):
        frame = frames[part]
        cutoff = str(pd.Period(w[part][1], freq="M") + 1) + "-01"
        assert all(calendar[int(i) + 4] < cutoff for i in frame.date_i)
        training.append(frame[frame.complete].copy())
    return four.choose_rule(*training, month, shape.rules())


def first10_map():
    first = pd.concat([pd.read_pickle(R34 / "first10.pkl"),
                       pd.read_pickle(R35 / "first10.pkl")], ignore_index=True)
    first["symbol"] = first.symbol.str[:6]
    duplicated = first[first.duplicated(KEY + ["time"], keep=False)]
    for field in ("open", "high", "low", "close", "volume"):
        spread = duplicated.groupby(KEY + ["time"])[field].agg(lambda x: float(x.max() - x.min()))
        assert spread.le(1e-10).all(), field
    first = first.drop_duplicates(KEY + ["time"])
    return {(symbol, day): group.sort_values("time")
            for (symbol, day), group in first.groupby(KEY)}


def pick(frame: pd.DataFrame, rule: dict, bars: dict) -> pd.DataFrame:
    rows = four.chosen(frame, rule["selected"])
    return four.delay_same_stock(rows, bars)


def score_month(signals: pd.DataFrame, month: str, calendar: list[str]) -> tuple[dict, pd.DataFrame]:
    if len(signals):
        last = max(calendar[calendar.index(d) + 1] for d in signals.d1_date)
    else:
        last = max(d for d in calendar if d.startswith(month))
    result, _, _, ledger, excluded = replay(signals, calendar, start=f"{month}-01", end=last, fee_rate=.001)
    assert result["pending"] == 0 and not excluded
    known = signals[signals.observed_3]
    record = dict(month=month, signals=len(signals), trades=result["entries"],
                  hits=int(known.label_3.sum()), hit_rate=float(known.label_3.mean()) if len(known) else None,
                  mean_d2_net=float(ledger.return_.mean()) if len(ledger) else None,
                  loss_over_5=float(ledger.return_.lt(-.05).mean()) if len(ledger) else None,
                  return_=result["total_return"], max_drawdown=result["max_drawdown"],
                  sharpe=result["sharpe"], fees=result["total_fees"],
                  window_end=last)
    return record, ledger


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    spec = dict(name="R49 exploratory monthly daily6+1 shape6+2 paired sample",
                months=MONTHS, selection="predeclared original B2 high/low months before reading hybrid test results",
                daily="R34 RF51 monthly fit 6 months, validate one month; validation does not choose daily model",
                shape="original 79 four-K rules, 6 months causal historical top1 training, 2 months validation",
                sources={str(p): sha(p) for p in [R34 / "enriched.pkl", R34 / "first10.pkl",
                                                   R35 / "enriched.pkl", R35 / "first10.pkl",
                                                   R35 / "signals.pkl", R35 / "rules_frozen.json",
                                                   R34 / "panel.pkl", R34 / "calendar.json"]},
                daily_models={m: sha(R34 / "daily1" / m / "segments.joblib")
                              for m in sorted({x for month in MONTHS
                                               for x in (month, str(pd.Period(month, freq="M") - 1))})},
                execution="D0 Top3; D1 four bars; 09:35-09:40 next qualifying open; D2 close; 0.1% each side",
                account="Each target D0 month is an independent 1m account cohort, half cash when flat, all free cash if carrying; finish last D2; no shadow gate because sampled months are non-contiguous",
                production_changed=False)
    dump(OUT / "specification.json", spec)
    one = pd.read_pickle(R34 / "enriched.pkl")
    two = pd.read_pickle(R35 / "enriched.pkl")
    panel = pd.read_pickle(R34 / "panel.pkl")
    calendar = json.loads((R34 / "calendar.json").read_text())
    static = pd.concat([one, two], ignore_index=True).drop_duplicates(KEY)
    static = static.drop(columns=["score", "d0_rank", "variant", "model_month", "part"])
    bars = first10_map()
    old_rules = {r["month"]: r for r in json.loads((R35 / "rules_frozen.json").read_text())}
    old_signals = pd.read_pickle(R35 / "signals.pkl")
    r31_rules = {r["month"]: r for r in json.loads((R31 / "rules_frozen.json").read_text())
                    if r["variant"] == "D1S2"}
    r31_signals = pd.read_pickle(R31 / "signals.pkl")
    prepared = {}
    rules = []
    for month in MONTHS:
        hybrid, original = month_frames(one, two, panel, calendar, static, month)
        hr = choose(hybrid, month)
        br = choose(original, month)
        for field in ("selected", "train_base", "valid_base", "train_selected", "valid_selected"):
            assert json.loads(json.dumps(br[field])) == old_rules[month][field], (month, field)
        if month in r31_rules:
            for field in ("selected", "train_base", "valid_base", "train_selected", "valid_selected"):
                assert json.loads(json.dumps(hr[field])) == r31_rules[month][field], (month, field)
        prepared[month] = (hybrid, original, hr, br)
        rules.extend([dict(variant=tag, **rule) for tag, rule in (("日线6+1形态6+2", hr), ("原B2两层6+2", br))])
        print("frozen", month, hr["selected"]["id"], br["selected"]["id"], flush=True)
    # Rules are frozen and saved before any held-out test rows enter the selector.
    dump(OUT / "rules_frozen.json", rules)
    monthly, signal_sets, ledgers, comparisons = [], [], [], []
    for month in MONTHS:
        hybrid, original, hr, br = prepared[month]
        selected = {}
        for variant, frame, rule in (("日线6+1形态6+2", hybrid, hr), ("原B2两层6+2", original, br)):
            signals = pick(frame["test"], rule, bars)
            signals["variant"] = variant
            signals["rule_id"] = rule["selected"]["id"]
            summary, ledger = score_month(signals, month, calendar)
            monthly.append(dict(variant=variant, **summary))
            signal_sets.append(signals)
            ledgers.append(ledger.assign(variant=variant, month=month))
            selected[variant] = signals
        b = selected["原B2两层6+2"]
        reference = old_signals[old_signals.d0_date.str.startswith(month)]
        cols = KEY + ["d1_date", "entry_time", "entry", "d2_close"]
        pd.testing.assert_frame_equal(b[cols].sort_values(KEY).reset_index(drop=True),
                                      reference[cols].sort_values(KEY).reset_index(drop=True), check_dtype=False)
        if month in r31_rules:
            h = selected["日线6+1形态6+2"]
            ref = r31_signals[r31_signals.variant.eq("D1S2") & r31_signals.d0_date.str.startswith(month)]
            pd.testing.assert_frame_equal(h[cols].sort_values(KEY).reset_index(drop=True),
                                          ref[cols].sort_values(KEY).reset_index(drop=True), check_dtype=False)
        hk = set(zip(selected["日线6+1形态6+2"].symbol, selected["日线6+1形态6+2"].d0_date))
        bk = set(zip(b.symbol, b.d0_date))
        comparisons.append(dict(month=month, common_trades=len(hk & bk), hybrid_only=len(hk-bk), baseline_only=len(bk-hk),
                                same_rule=hr["selected"]["id"] == br["selected"]["id"],
                                hybrid_rule=hr["selected"]["id"], baseline_rule=br["selected"]["id"]))
    result = pd.DataFrame(monthly)
    result.to_csv(OUT / "monthly_comparison.csv", index=False, encoding="utf-8-sig")
    pd.concat(signal_sets, ignore_index=True).to_pickle(OUT / "signals.pkl")
    pd.concat(ledgers, ignore_index=True).to_pickle(OUT / "ledger.pkl")
    dump(OUT / "candidate_overlap.json", comparisons)
    dump(OUT / "verification.json", dict(original_rules_exact=len(MONTHS), original_signals_exact=len(MONTHS),
          r31_hybrid_rules_and_signals_exact=sum(m in r31_rules for m in MONTHS),
          original_month_scores_sha256=sha(R35 / "enriched.pkl"),
          hybrid_month_scores_sha256=sha(R34 / "enriched.pkl")))
    print(result[["month", "variant", "trades", "hit_rate", "mean_d2_net", "return_", "max_drawdown"]].to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
