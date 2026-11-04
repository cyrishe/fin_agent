import pandas as pd

from scripts.audit_automl_sell_minute_mode import SLOTS, choose_modal_slots


def test_each_qualifying_stock_votes_once_and_ties_choose_first_slot():
    rows = []
    for symbol, best, tied, target in (
            ("000001", "close_31", "close_32", .02),
            ("000002", "close_32", None, .03),
            ("000003", "close_40", None, .04),
            ("000004", "open_0931", None, .005)):
        row = {"signal_date": "day1", "next_date": "day2",
               "symbol6": symbol, "name": symbol, "entry_1440": 100,
               "second_high_return": target,
               **{slot: 100 for slot in SLOTS}}
        row[best] = 105
        if tied:
            row[tied] = 105
        rows.append(row)
    guide, qualifying = choose_modal_slots(pd.DataFrame(rows))
    assert len(qualifying) == 3
    assert qualifying.set_index("symbol6").loc["000001", "best_slot"] == "close_31"
    assert qualifying.set_index("symbol6").loc["000001", "tied_best_slots"] == 2
    assert guide.iloc[0].chosen_slot == "close_31"
    assert guide.iloc[0].tied_modal_slots == "close_31,close_32,close_40"
    assert guide.iloc[0].max_votes == 1
