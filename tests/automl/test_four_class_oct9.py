import pandas as pd
import pytest

from scripts.evaluate_automl_four_class_oct9 import add_outcomes, select_current


def test_second_high_uses_second_bar_high_and_keeps_minute_closes():
    scored = pd.DataFrame([{"symbol6": "000001", "entry_1440": 100}])
    bars = pd.DataFrame({
        "symbol6": ["000001"]*10,
        "minute": [f"09:{minute:02}" for minute in range(31, 41)],
        "high_price": [102, 103, 101, 100, 100, 100, 100, 100, 100, 100],
        "latest_price": [99, 100, 98, 97, 97, 97, 97, 97, 97, 97],
    })
    result = add_outcomes(scored, bars).iloc[0]
    assert result.second_high == 102
    assert result.second_high_return == pytest.approx(.02)
    assert result["class"] == "1to3"
    assert result.close_31 == 99
    assert result.close_40 == 97


def test_october_selection_uses_saved_probability_classes():
    scored = pd.DataFrame([
        ("unweighted", "000001", "1to3", .20, .30, .19),
        ("unweighted", "000002", "ge3", .20, .10, .31),
        ("unweighted", "000003", "ge3", .20, .10, .29),
    ], columns=["model", "symbol6", "predicted_class",
                "p_lt0", "p_1to3", "p_ge3"])
    scored["signal_date"] = "2026-10-08"
    chosen = select_current(scored)
    fallback = chosen[chosen.selection_rule.eq("class_priority_fallback")]
    assert fallback.symbol6.tolist() == ["000002", "000003"]
    assert fallback.selection_rank.tolist() == [1, 2]
