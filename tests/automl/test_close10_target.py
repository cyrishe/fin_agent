import pandas as pd

from scripts.experiment_automl_close10_target import cash_simulation


def test_first_minute_close_and_next_minute_fill_are_separate_prices():
    selected = pd.DataFrame({
        "signal_date": ["2026-09-22", "2026-09-22"],
        "rank": [1, 2], "symbol6": ["000001", "000002"],
        "entry_1440": [10, 10],
    })
    for minute in range(31, 41):
        selected[f"close_{minute:02}"] = [10.0, 10.0]
    selected.loc[0, "close_31"] = 10.2
    selected.loc[0, "close_32"] = 9.8
    same_minute, same_trades = cash_simulation(selected, True)
    next_minute, later_trades = cash_simulation(selected, True, delay_first_fill=True)
    assert same_minute["ending_cash"] == 101200.0
    assert next_minute["ending_cash"] == 98800.0
    assert same_trades[0]["exit_minute"] == "09:31"
    assert later_trades[0]["exit_minute"] == "09:32"
