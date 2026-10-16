import pytest

from src.experiments.skill_handoff import coverage, endpoint_change, evidence_packet, freeze_results, price_range, used_definitions
from src.services.session_variable_store_service import SessionVariableStoreService


def saved(tmp_path, rows):
    store = SessionVariableStoreService(data_root=tmp_path)
    entry = store.register_tool_result(session_id="allowed", tool_name="test",
                                      result={"data": rows}, task="requested 45 days")
    ref = {"result_ref": entry["data_ref"], "result_name": "r1", "api": "stock.technical.query"}
    return store, ref


def test_requested_window_never_becomes_observed_coverage(tmp_path):
    store, ref = saved(tmp_path, [{"code": "a", "trade_date": "2026-09-28", "rsi14": 46.6}])
    packet = evidence_packet(freeze_results(store, session_id="allowed", result_refs=[ref]))[0]
    assert packet["actual_row_count"] == 1
    assert packet["observed_dates"]["trade_date"] == {
        "first": "2026-09-28", "last": "2026-09-28", "distinct_values": 1}


def test_foreign_reference_rejected_before_read(tmp_path):
    store, ref = saved(tmp_path, [{"value": 1}])
    with pytest.raises(ValueError, match="current conversation"):
        freeze_results(store, session_id="someone-else", result_refs=[ref])


def test_empty_legacy_and_missing_cells_stay_lossless(tmp_path):
    for rows in [[], [{"a": 1, "b": None}, {"a": 2}]]:
        store, ref = saved(tmp_path, rows)
        packet = evidence_packet(freeze_results(store, session_id="allowed", result_refs=[ref]))[0]
        recovered = [dict(zip(packet["columns"], row)) for row in packet["rows"]]
        for i, j in packet.get("absent_cells", []):
            del recovered[i][packet["columns"][j]]
        assert recovered == rows
        assert packet["provider_evidence"] == {}
        assert packet["observed_dates"] == {}


def test_snapshot_and_trade_dates_not_collapsed():
    result = coverage([{"trade_date": "2026-09-28", "snapshot_time": "2026-09-29 15:30:00"}])
    assert len(result) == 2


def test_deterministic_window_math_sorts_and_excludes_outside_rows():
    rows = [{"code": "a", "date": d, "close": c, "high": c + 1, "low": c - 1}
            for d, c in [("2026-09-28", 12), ("2026-08-01", 99), ("2026-08-28", 10)]]
    args = dict(date_field="date", start="2026-08-28", end="2026-09-28")
    change = endpoint_change(rows, value_field="close", **args)
    assert change["ratio"] == pytest.approx(.2)
    assert change["start"] == "2026-08-28"
    assert price_range(rows, **args)["ratio"] == pytest.approx(13 / 9 - 1)
    rows[-1]["code"] = "b"
    with pytest.raises(ValueError, match="one security"):
        endpoint_change(rows, value_field="close", **args)


def test_snapshot_cannot_be_used_as_time_series():
    with pytest.raises(ValueError, match="two unique"):
        endpoint_change([{"date": "2026-09-28", "close": 1}], date_field="date",
                        value_field="close", start="2026-08-28", end="2026-09-28")


def test_partial_materialization_is_not_declared_complete(tmp_path, monkeypatch):
    store, ref = saved(tmp_path, [{"value": 1}])
    monkeypatch.setattr(store, "materialize_data_ref", lambda **kw:
                        {"data_type": "table", "rows": [{"value": 1}], "row_count": 45})
    with pytest.raises(ValueError, match="partial"):
        freeze_results(store, session_id="allowed", result_refs=[ref])


def test_provider_basis_and_warning_are_preserved(tmp_path):
    store = SessionVariableStoreService(data_root=tmp_path)
    entry = store.register_tool_result(session_id="allowed", tool_name="finance_data_query", result={
        "result": {"data": {"rows": [{"trade_date": "2026-09-28", "ma20": 100}],
                             "row_count": 1, "evidence": {"price_basis": "hfq", "formula_revision": "v1"}}}})
    ref = {"result_ref": entry["data_ref"], "api": "stock.technical.query", "warnings": ["test gap"]}
    packet = evidence_packet(freeze_results(store, session_id="allowed", result_refs=[ref]))[0]
    assert packet["provider_evidence"] == {"price_basis": "hfq", "formula_revision": "v1"}
    assert packet["warnings"] == ["test gap"]


def test_repeated_api_preserves_definitions_for_all_used_modes():
    catalog = {"subjects": {"stock": {"quote": {"fields": {
        "adjclose": "adjusted daily", "snapshot_time": "latest timestamp", "unused": "ignored"}}}}}
    frozen = [{"api": "stock.quote.query", "rows": [{"adjclose": 100}]},
              {"api": "stock.quote.query", "rows": [{"snapshot_time": "2026-09-29"}]}]
    assert used_definitions(frozen, catalog)["stock.quote.query"]["fields"] == {
        "adjclose": "adjusted daily", "snapshot_time": "latest timestamp"}
