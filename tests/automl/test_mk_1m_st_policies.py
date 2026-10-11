from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

import pandas as pd

import scripts.experiment_automl_mk_1m_st_policies as subject
from scripts.experiment_automl_mk_1m_st_policies import (
    POLICIES, compact_tables, make_cohorts, retained_by_hash,
)


def test_st_cohorts_use_only_signal_day_state():
    rows = pd.DataFrame({
        "signal_date": ["2026-02-03"] * 3,
        "symbol6": ["600001", "600002", "600003"],
        "t_reference_preclose": [10., 10., 10.],
        "signal_price": [10.5, 10.4, 10.4],
        "second_high": [10.9, None, 10.5],
        "close_40": [10.9, None, 10.5],
    })
    intervals = pd.DataFrame({
        "symbol6": ["600001", "600002", "600003"],
        "st_type": ["Y", "Y", "N"],
        "begin_date": pd.to_datetime(["2026-01-01"] * 3),
        "end_date": pd.to_datetime(["2026-03-01"] * 3),
        "ann_date": pd.to_datetime(["2026-01-01", "2026-01-01", None]),
    })
    cohorts, _ = make_cohorts(rows, intervals)
    assert cohorts[POLICIES[0]].symbol6.tolist() == ["600003"]
    assert cohorts[POLICIES[1]].symbol6.tolist() == ["600002", "600003"]
    assert "600001" in cohorts[POLICIES[2]].symbol6.tolist()
    assert "600003" in cohorts[POLICIES[2]].symbol6.tolist()

    changed_outcomes = rows.assign(second_high=[None, 100., None],
                                   close_40=[None, 100., None])
    revised, _ = make_cohorts(changed_outcomes, intervals)
    for policy in POLICIES:
        assert cohorts[policy].symbol6.tolist() == revised[policy].symbol6.tolist()
    assert retained_by_hash("2026-02-03", "600002") == retained_by_hash(
        "2026-02-03", "600002")


def test_month_and_day_tables_keep_target_separate_from_sale_return():
    picks = pd.DataFrame({
        "policy": [POLICIES[0], POLICIES[0]],
        "signal_date": ["2026-02-03"] * 2,
        "selection_rank": [1, 2],
        "actual_class": ["ge3", "0to1"],
        "actual_0940_return": [-0.01, 0.0],
        "suspension_zero": [False, True],
    })
    monthly, daily = compact_tables(picks, ["2026-02-03"])
    row = daily[daily.方案.eq(POLICIES[0])].iloc[0]
    assert row["Top1结果"] == "达标"
    assert row["Top1 09:40收益"] == "-1.00%"
    assert row["Top2结果"] == "中性"
    assert row["Top2 09:40收益"] == "+0.00%"
    month = monthly[monthly.方案.eq(POLICIES[0])].iloc[0]
    assert month["Top1结果"] == "达标1/可接受0/中性0/错误0"


def test_suspension_sale_zero_does_not_fabricate_training_class(monkeypatch):
    picks = pd.DataFrame({
        "next_date": ["2026-02-04", "2026-02-04"],
        "symbol6": ["600001", "600002"],
        "actual_0940_return": [float("nan"), float("nan")],
        "actual_class": ["UNKNOWN", "UNKNOWN"],
    })
    monkeypatch.setattr(subject, "db_connection", lambda _: nullcontext(object()))
    monkeypatch.setattr(subject, "query", lambda *_: pd.DataFrame({
        "next_date": ["2026-02-04"], "symbol6": ["600001"], "volume": [0],
    }))
    settled = subject.settle_suspensions(picks, Path(".env"))
    assert settled.actual_0940_return.iloc[0] == 0
    assert pd.isna(settled.actual_0940_return.iloc[1])
    assert settled.actual_class.tolist() == ["UNKNOWN", "UNKNOWN"]
    assert settled.suspension_zero.tolist() == [True, False]
