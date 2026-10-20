"""R51: full monthly B2 daily gap-three comparison with original B2.

Reuses byte-identical R50 models where available; both strategies share the
same D0 calendar, four-K execution, D2 exit, fees and account simulator.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

from scripts import experiment_b2_daily_gap3_sample_r50 as sample
from scripts import experiment_limit_rebound_four_minutes_r24 as four
from scripts.replay_limit_rebound_cash_r23 import dump, replay, sha

OUT = Path("outputs/stock_automl/b2_daily_gap3_full_r51_20261020")
PARENT = Path("outputs/stock_automl/b2_daily_gap3_sample_r50_20261020")
R35 = sample.R35
R34 = sample.R34
MONTHS = [str(x) for x in pd.period_range("2024-04", "2026-09", freq="M")]
HISTORY = sorted({str(h) for month in MONTHS
                  for h in pd.period_range(*sample.r31.window(month, 2)["train"], freq="M")})
TRAIN_MONTHS = sorted(set(HISTORY) | set(MONTHS))
KEY = ["symbol", "d0_date"]


def specification() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    value = dict(experiment="R51 full monthly daily gap-three versus original B2",
                 first_test=MONTHS[0], last_test=MONTHS[-1], test_months=MONTHS,
                 daily_forecast_months=TRAIN_MONTHS,
                 change="Only D0 RF51 monthly six-month training shifts from M-8..M-3 to M-9..M-4; daily validation is not model-selected",
                 second_stage="Original B2 six-train/two-valid four-K 79-rule selection, rebuilt from each alternative's causal D0 Top1 forecasts",
                 execution="Original Top3, four completed bars, chronological lock, body>-8%, same-stock next qualifying minute open by09:40, D2 close, fees 0.1% each side",
                 comparison="Original B2 R35 signals, independent monthly and continuous accounts with identical start and D0 dates; no shadow gate in main comparison",
                 earliest_full_test="2024-04 because 2023-08 historical daily forecast needs 2022-11..2023-04 model fit and R34 panel starts 2022-11",
                 prior_sample_spec_sha256=sha(PARENT / "specification.json"),
                 panel_sha256=sha(R34 / "panel.pkl"), calendar_sha256=sha(R34 / "calendar.json"),
                 original_signals_sha256=sha(R35 / "signals.pkl"),
                 production_changed=False, exploratory_history=True)
    path = OUT / "specification.json"
    if path.exists():
        assert json.loads(path.read_text()) == value
    else:
        dump(path, value)
    return value


def configure() -> None:
    sample.OUT = OUT
    sample.MONTHS, sample.HISTORY, sample.TRAIN_MONTHS = MONTHS, HISTORY, TRAIN_MONTHS
    sample.specification = specification
    sample.configure()


def train() -> None:
    configure()
    specification()
    reuse = []
    for month in TRAIN_MONTHS:
        source = PARENT / "daily3" / month
        if not (source / "metrics.json").exists():
            continue
        target = OUT / "daily3" / month
        target.mkdir(parents=True, exist_ok=True)
        for path in source.iterdir():
            dest = target / path.name
            if not dest.exists():
                shutil.copy2(path, dest)
            assert sha(path) == sha(dest), path
        reuse.append(dict(month=month, model_sha256=sha(source / "segments.joblib"),
                          original_spec_sha256=sha(PARENT / "specification.json")))
    dump(OUT / "reused_models.json", reuse)
    sample.train()


def prepare() -> None:
    configure()
    sample.prepare()


def select() -> None:
    configure()
    sample.select()


def _last_d2(frame: pd.DataFrame, calendar: list[str]) -> str:
    if len(frame) == 0:
        raise ValueError("No signals in selected cohort")
    return max(calendar[calendar.index(day) + 1] for day in frame.d1_date)


def evaluate() -> None:
    rules = json.loads((OUT / "rules_frozen.json").read_text())
    f = pd.read_pickle(OUT / "enriched.pkl")
    first = pd.read_pickle(OUT / "first10.pkl")
    first["symbol"] = first.symbol.str[:6]
    bars = {(sym, d0): group.sort_values("time") for (sym, d0), group in first.groupby(KEY)}
    picks = []
    for rule in rules:
        month = rule["month"]
        q = f[f.model_month.eq(month) & f.part.eq("test")]
        a = four.delay_same_stock(four.chosen(q, rule["selected"]), bars)
        a["variant"], a["rule_id"] = "日线错开三月", rule["selected"]["id"]
        picks.append(a)
    new = pd.concat(picks, ignore_index=True)
    old = pd.read_pickle(R35 / "signals.pkl")
    old = old[old.d0_date.str[:7].isin(MONTHS)].copy()
    assert not new.d1_date.duplicated().any() and not old.d1_date.duplicated().any()
    original = pd.read_pickle(R35 / "signals.pkl")
    cols = KEY + ["d1_date", "entry_time", "entry", "d2_close"]
    pd.testing.assert_frame_equal(old[cols].sort_values(KEY).reset_index(drop=True),
                                  original[original.d0_date.str[:7].isin(MONTHS)][cols]
                                  .sort_values(KEY).reset_index(drop=True), check_dtype=False)
    new.to_pickle(OUT / "signals.pkl")
    old.to_pickle(OUT / "original_signals.pkl")
    original_rules = {row["month"]: row["selected"]["id"] for row in
                      json.loads((R35 / "rules_frozen.json").read_text())}
    overlap_rows = []
    for month in MONTHS:
        old_keys = set(zip(old.loc[old.d0_date.str.startswith(month), "symbol"],
                           old.loc[old.d0_date.str.startswith(month), "d0_date"]))
        new_keys = set(zip(new.loc[new.d0_date.str.startswith(month), "symbol"],
                           new.loc[new.d0_date.str.startswith(month), "d0_date"]))
        new_rule = next(row["selected"]["id"] for row in rules if row["month"] == month)
        overlap_rows.append(dict(month=month, original_rule=original_rules[month],
                                 alternative_rule=new_rule,
                                 same_rule=original_rules[month] == new_rule,
                                 original_signals=len(old_keys), alternative_signals=len(new_keys),
                                 common_signals=len(old_keys & new_keys),
                                 original_only=len(old_keys - new_keys),
                                 alternative_only=len(new_keys - old_keys)))
    pd.DataFrame(overlap_rows).to_csv(OUT / "monthly_signal_overlap.csv", index=False,
                                      encoding="utf-8-sig")
    calendar = json.loads((R34 / "calendar.json").read_text())
    scopes = [("共同全期", "2024-04", "2026-09"),
              ("2024可比", "2024-04", "2024-12"),
              ("2025全年", "2025-01", "2025-12"),
              ("2026截至9月", "2026-01", "2026-09"),
              ("2025至2026", "2025-01", "2026-09"),
              ("2025五月后", "2025-05", "2026-09")]
    accounts, monthly, navs, ledgers, exclusions = [], [], [], [], []
    for scope, first_month, last_month in scopes:
        start = first_month + "-01"
        pairs = []
        for variant, signals in (("原B2", old), ("日线错开三月", new)):
            q = signals[signals.d0_date.str[:7].between(first_month, last_month)].copy()
            pairs.append((variant, q))
        end = max(_last_d2(q, calendar) for _, q in pairs)
        for variant, q in pairs:
            result, month_rows, nav, ledger, excluded = replay(q, calendar, start=start, end=end,
                                                                fee_rate=.001)
            assert result["pending"] == 0 and not excluded
            accounts.append(dict(scope=scope, variant=variant, d0_start=first_month,
                                 d0_end=last_month, **result))
            monthly.extend(dict(scope=scope, variant=variant, **row) for row in month_rows)
            navs.append(nav.assign(scope=scope, variant=variant))
            ledgers.append(ledger.assign(scope=scope, variant=variant))
            exclusions.extend(dict(scope=scope, variant=variant, **row) for row in excluded)
            print(scope, variant, result["entries"], result["total_return"],
                  result["max_drawdown"], flush=True)
    dump(OUT / "accounts.json", accounts)
    dump(OUT / "monthly.json", monthly)
    dump(OUT / "exclusions.json", exclusions)
    pd.concat(navs, ignore_index=True).to_pickle(OUT / "nav.pkl")
    pd.concat(ledgers, ignore_index=True).to_pickle(OUT / "ledger.pkl")
    pd.DataFrame(accounts).to_csv(OUT / "accounts.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(monthly).to_csv(OUT / "monthly.csv", index=False, encoding="utf-8-sig")
    selected = json.loads((OUT / "rules_frozen.json").read_text())
    dump(OUT / "verification.json", dict(original_signal_rows=len(old),
                                          original_source_sha256=sha(R35 / "signals.pkl"),
                                          alternative_signals=len(new),
                                          selected_rules=len(selected),
                                          same_scope_and_account_for_both=True,
                                          no_matured_signal_exclusions=True))


def audit() -> None:
    configure()
    sample.audit()
    accounts = pd.read_csv(OUT / "accounts.csv")
    ledger = pd.read_pickle(OUT / "ledger.pkl")
    for row in accounts.itertuples():
        subset = ledger[ledger.scope.eq(row.scope) & ledger.variant.eq(row.variant)]
        assert len(subset) == row.entries
        assert subset.exit_date.notna().all()
        assert subset.sell.eq(subset.d2_close).all()
        np.testing.assert_allclose(subset.return_,
                                   subset.sell / subset.buy * .999 / 1.001 - 1,
                                   atol=1e-12, rtol=0)
    dump(OUT / "full_audit.json", dict(model_reload_sha256=sha(OUT / "model_reload_verification.json"),
                                        account_rows=len(accounts), monthly_rows=len(pd.read_csv(OUT / "monthly.csv")),
                                        all_scope_ledgers_exact=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("train", "prepare", "select", "evaluate", "audit", "all"),
                        nargs="?", default="all")
    args = parser.parse_args()
    stages = ("train", "prepare", "select", "evaluate", "audit") if args.stage == "all" else (args.stage,)
    for stage in stages:
        globals()[stage]()


if __name__ == "__main__":
    main()
