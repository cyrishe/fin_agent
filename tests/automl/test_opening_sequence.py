import pandas as pd

from scripts.experiment_automl_opening_sequence import (
    BAR_CHANNELS,
    _market_morning,
    relative_sequence_array,
    sequence_array,
)


def test_six_bar_array_keeps_the_original_time_order():
    row = {f"{channel}_{minute}": minute * 10 + index
           for minute in range(1, 7)
           for index, channel in enumerate(BAR_CHANNELS)}
    sequence = sequence_array(pd.DataFrame([row]))
    assert sequence.shape == (1, 6, 6)
    assert sequence[0, 0].tolist() == [10, 11, 12, 13, 14, 15]
    assert sequence[0, 5].tolist() == [60, 61, 62, 63, 64, 65]
    relative = relative_sequence_array(sequence)
    assert relative[0, 0, 0] == 0
    assert relative[0, 5, 0] == 50
    assert round(float(relative[0, :, 4].mean()), 6) == 0


def test_market_morning_excludes_bars_after_0936():
    codes = [f"6{index:05d}.SH" for index in range(4000)]
    rows = []
    for time, close, amount in (("09:31:00", 10, 100),
                                ("09:36:00", 10.1, 100),
                                ("09:40:00", 1, 1_000_000)):
        rows.extend({"time": f"2026-02-03 {time}", "symbol": code,
                     "open": 10, "close": close, "turnover": amount}
                    for code in codes)
    result = _market_morning(pd.DataFrame(rows), "2026-02-03")
    assert result["market_0936_stock_count"] == 4000
    assert round(result["market_0936_mean_return_pct"], 6) == 1
    assert result["market_0936_up_share"] == 1
