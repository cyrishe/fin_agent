from __future__ import annotations

import pytest

from src.backtest import BacktestError
from src.services.backtest_run_service import BacktestRunService


class _BuyAndHold:
    def __init__(self):
        self.calls = []

    def run(self, payload):
        self.calls.append(dict(payload))
        return {"strategy": {"name": "买入并持有"}, "summary": {"total_return": 0.1}}


def test_backtest_run_defaults_to_equal_weight_when_no_weight_is_supplied():
    buy_and_hold = _BuyAndHold()
    result = BacktestRunService(buy_and_hold_service=buy_and_hold).run(
        {
            "holdings": [{"stock": "贵州茅台"}, {"stock": "五粮液"}],
            "start_date": "2025-01-01",
            "end_date": "2025-06-30",
        }
    )

    assert result["ok"] is True
    assert result["backtest_type"] == "fixed_basket"
    assert buy_and_hold.calls[0]["stocks"] == ["贵州茅台", "五粮液"]
    assert "weights" not in buy_and_hold.calls[0]


def test_backtest_run_requires_all_or_no_weights():
    with pytest.raises(BacktestError) as caught:
        BacktestRunService(buy_and_hold_service=_BuyAndHold()).run(
            {
                "holdings": [
                    {"stock": "贵州茅台", "weight": 0.6},
                    {"stock": "五粮液"},
                ],
                "start_date": "2025-01-01",
                "end_date": "2025-06-30",
            }
        )

    assert caught.value.code == "incomplete_weights"
