import gzip
import json

import pandas as pd

from scripts import evaluate_automl_four_class_july15m as july


def test_cached_closes_match_each_signal_to_its_following_morning(tmp_path, monkeypatch):
    monkeypatch.setattr(july, "CACHE", tmp_path)
    bars = [
        {"day": "2026-07-01 15:00:00", "close": "10.1"},
        {"day": "2026-07-02 09:45:00", "close": "10.5", "high": "10.8"},
        {"day": "2026-07-02 10:00:00", "close": "10.4"},
        {"day": "2026-07-02 10:15:00", "close": "10.3"},
        {"day": "2026-07-02 15:00:00", "close": "11.1"},
        {"day": "2026-07-03 09:45:00", "close": "11.5", "high": "11.8"},
        {"day": "2026-07-03 10:00:00", "close": "11.4"},
        {"day": "2026-07-03 10:15:00", "close": "11.3"},
    ]
    with gzip.open(tmp_path / "000001.json.gz", "wt") as file:
        json.dump(bars, file)
    rows = pd.DataFrame([
        {"signal_date": "2026-07-01", "next_date": "2026-07-02", "symbol6": "000001"},
        {"signal_date": "2026-07-02", "next_date": "2026-07-03", "symbol6": "000001"},
    ])
    result = july.cached_closes(rows).sort_values("signal_date")
    assert result.close_1500.tolist() == [10.1, 11.1]
    assert result.close_0945.tolist() == [10.5, 11.5]
    assert result.close_1000.tolist() == [10.4, 11.4]
    assert result.close_1015.tolist() == [10.3, 11.3]
    assert result.high_0945.tolist() == [10.8, 11.8]
