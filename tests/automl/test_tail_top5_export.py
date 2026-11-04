import json

from scripts.export_automl_tail_top5 import load_top_five


def test_load_top_five_requires_each_daily_rank(tmp_path):
    rows = [{"signal_date": "2026-09-08", "symbol6": f"00000{rank}",
             "rank_binary": rank} for rank in range(1, 7)]
    path = tmp_path / "scores.json"
    path.write_text(json.dumps({"all": rows}))

    selected = load_top_five(path)

    assert selected.rank_binary.tolist() == [1, 2, 3, 4, 5]
    assert selected.symbol6.tolist() == ["000001", "000002", "000003", "000004", "000005"]
