"""Protocol checks for immutable B2 V2 identity and prior-month decision."""
from __future__ import annotations

import pandas as pd
import pytest

from scripts.automl_b2 import definition as b2_definition, digest
from scripts.automl_b2_v2 import (
    BASE_DEFINITION_SHA256, DISPLAY_NAME, FROZEN_DEFINITION_SHA256,
    STRATEGY_ID, definition, month_decision,
)
from scripts.readable_strategy_execution import identity
from scripts.register_automl_b2_v2 import payload, register_definition


def test_v2_is_distinct_version_of_exact_frozen_b2():
    assert digest(b2_definition()) == BASE_DEFINITION_SHA256
    frozen = definition()
    assert frozen["title"] == DISPLAY_NAME
    assert frozen["formula"]["base_definition_sha256"] == BASE_DEFINITION_SHA256
    assert frozen["formula"]["base_formula"] == b2_definition()["formula"]
    assert identity("B2")["model_version"] == "v1.0"
    assert identity(STRATEGY_ID) == {
        "strategy_name": "涨停冷静", "model_version": "v2.0", "version_desc": "月度停摆"
    }
    assert payload()["definition_sha256"] == digest(frozen) == FROZEN_DEFINITION_SHA256


def test_month_gate_uses_only_prior_month_and_sum_strictly_below_zero():
    # Prior August anchor followed by ten September trading days.
    days = ["2026-08-31"] + pd.bdate_range("2026-09-01", periods=10).strftime("%Y-%m-%d").tolist()
    values = [100] + [100] * 5 + [101, 101, 101, 101, 99]
    frame = pd.DataFrame({"nav": values}, index=pd.Index(days, name="date"))
    result = month_decision("2026-10", 16, frame)
    assert result["prior_last5_return"] == pytest.approx(99 / 100 - 1)
    assert result["prior_last10_return"] == pytest.approx(99 / 100 - 1)
    assert result["pause"] is True
    # The target month's later NAV must not affect the decision.
    future = pd.DataFrame({"nav": [999]}, index=pd.Index(["2026-10-08"], name="date"))
    assert month_decision("2026-10", 16, pd.concat([frame, future])) == result
    # A zero sum is allowed, while a known zero-candidate prior month pauses.
    flat = frame.copy(); flat.loc["2026-09-14", "nav"] = 100
    assert month_decision("2026-10", 16, flat)["pause"] is False
    assert month_decision("2026-10", 0, flat)["pause"] is True


def test_missing_history_is_not_zero_candidates_and_invalid_nav_rejects():
    short = pd.DataFrame({"nav": [100, 99]},
                         index=pd.Index(["2026-09-01", "2026-09-02"], name="date"))
    result = month_decision("2026-10", None, short)
    assert result["pause"] is False
    assert result["prior_last5_return"] is None
    with pytest.raises(ValueError, match="候选数"):
        month_decision("2026-10", -1, short)
    with pytest.raises(ValueError, match="净值日期"):
        month_decision("2026-10", 1, pd.concat([short, short]))
    with pytest.raises(ValueError, match="净值必须"):
        month_decision("2026-10", 1, short.assign(nav=[100, 0]))


class Cursor:
    def __init__(self, existing=None):
        self.existing = existing
        self.calls = []

    def execute(self, sql, args):
        self.calls.append((sql, args))

    def fetchone(self):
        return self.existing


def test_v2_definition_registration_new_idempotent_and_conflict():
    frozen = payload()
    new = Cursor()
    assert register_definition(new, "imac", frozen) == "inserted"
    assert len(new.calls) == 2
    assert new.calls[1][1][1] == "B2_V2"
    same = Cursor({"definition_sha256": frozen["definition_sha256"]})
    assert register_definition(same, "imac", frozen) == "already_registered"
    assert len(same.calls) == 1
    conflict = Cursor({"definition_sha256": "0" * 64})
    with pytest.raises(ValueError, match="哈希不同"):
        register_definition(conflict, "imac", frozen)
    assert len(conflict.calls) == 1
