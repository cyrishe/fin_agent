"""Historical opportunity universes and bounded, non-truncating source reads."""
from contextlib import contextmanager
from types import SimpleNamespace

import pandas as pd
import pytest

from src.quant_research.automl import data


def specification(**changes):
    values = dict(start="2024-01-01", end="2025-12-31", symbols=(), max_symbols=None,
                  seed=42, industries=(), include_minute=False)
    values.update(changes)
    return SimpleNamespace(**values)


def test_default_universe_includes_interval_ipos_and_delisted_companies(monkeypatch):
    statements = []
    def query(conn, sql, args):
        statements.append((sql, args))
        return [{"stk_code": symbol} for symbol in ["600001.SH", "000001.SZ", "688001.SH", "INDEX"]]
    monkeypatch.setattr(data, "query", query)
    assert data.select_symbols(None, {}, specification()) == ["000001.SZ", "600001.SH", "688001.SH"]
    sql, args = statements[0]
    assert args == ("2025-12-31", "2024-01-01")
    assert "b.list_date<=%s" in sql and "b.delist_date>=%s" in sql
    assert "b.delist_date IS NULL" in sql


def test_explicit_symbols_take_priority_over_trial_company_limit(monkeypatch):
    def forbidden(*args):
        raise AssertionError("explicit symbols should not query or sample the universe")
    monkeypatch.setattr(data, "query", forbidden)
    assert data.select_symbols(None, {}, specification(symbols=("600001.sh", "000001.SZ", "600001.SH"),
                                                       max_symbols=1)) == ["000001.SZ", "600001.SH"]


def test_explicit_trial_limit_is_seeded_and_backwards_compatible(monkeypatch):
    monkeypatch.setattr(data, "query", lambda *args: [{"stk_code": f"60000{i}.SH"} for i in range(8)])
    chosen = data.select_symbols(None, {}, specification(max_symbols=3))
    assert len(chosen) == 3
    assert chosen == data.select_symbols(None, {}, specification(max_symbols=3))
    assert len(data.select_symbols(None, {}, specification())) == 8


def test_industry_selection_uses_overlapping_history_not_start_membership(monkeypatch):
    calls = []
    monkeypatch.setattr(data, "query", lambda conn, sql, args: calls.append((sql, args)) or [])
    data.select_symbols(None, {"kcrp_stock_industry": {}}, specification(industries=("电子",)))
    sql, args = calls[0]
    assert args == ("2025-12-31", "2024-01-01", "2025-12-31", "2024-01-01", "电子")
    assert "i.begin_date<=%s" in sql and "i.end_date>%s" in sql
    with pytest.raises(ValueError, match="historical industry"):
        data.select_symbols(None, {}, specification(industries=("电子",)))


def test_source_batches_cover_disjoint_dates_and_symbols_with_fixed_fetch_budget(monkeypatch):
    calls = []
    def bounded(conn, sql, args, limit):
        calls.append((sql, args, limit))
        return pd.DataFrame({"batch": [len(calls)]})
    monkeypatch.setattr(data, "_bounded", bounded)
    symbols = [f"600{i:03d}.SH" for i in range(65)]
    frames = list(data._source_batches(None, "stk_code", "kcrp_stock_price", symbols,
                                       "2024-01-01", "2025-12-31"))
    assert len(frames) == 4
    assert all(limit == 50000 for _, _, limit in calls)
    assert [args[-2:] for _, args, _ in calls] == [
        ("2024-01-01", "2025-01-01"), ("2025-01-01", "2026-01-01"),
        ("2024-01-01", "2025-01-01"), ("2025-01-01", "2026-01-01"),
    ]
    assert list(calls[0][1][:-2]) == symbols[:64]
    assert list(calls[2][1][:-2]) == symbols[64:]
    assert all("trade_date>=%s AND trade_date<%s" in sql for sql, _, _ in calls)


def test_source_query_rejects_overflow_instead_of_silently_truncating(monkeypatch):
    seen = []
    def query(conn, sql, args):
        seen.append((sql, args))
        return [{"symbol": "600001.SH"}] * 3
    monkeypatch.setattr(data, "query", query)
    with pytest.raises(ValueError, match="no silent truncation"):
        data._bounded(None, "SELECT stk_code FROM kcrp_stock_price", (), limit=2)
    assert seen == [("SELECT stk_code FROM kcrp_stock_price LIMIT %s", (3,))]


def configure_loader(monkeypatch, symbols, catalog=None):
    connections = []
    @contextmanager
    def connection(env_name):
        connections.append(env_name)
        yield object()
    monkeypatch.setattr(data, "kingdom_connection", connection)
    monkeypatch.setattr(data, "inventory", lambda conn: catalog or {})
    monkeypatch.setattr(data, "select_symbols", lambda *args: symbols)
    monkeypatch.setattr(data, "_market_calendar", lambda *args: ["2024-01-02", "2024-01-03"])
    return connections


def daily_rows(symbol="600001.SH"):
    return pd.DataFrame({"symbol": [symbol, symbol], "date": ["2024-01-02", "2024-01-03"],
                         "open": ["10", "11"], "close": ["10.5", "11.5"]})


def test_loader_accepts_single_company_and_discloses_explicit_scope(monkeypatch):
    connections = configure_loader(monkeypatch, ["600001.SH"])
    monkeypatch.setattr(data, "_source_batches", lambda *args, **kwargs: iter([daily_rows()]))
    daily, events, source = data.load_kingdom(specification(symbols=("600001.SH",), max_symbols=1),
                                             env_name="READ_ONLY_MARKET_URL")
    assert len(daily) == 2 and daily.open.tolist() == [10, 11]
    assert not events
    assert connections == ["READ_ONLY_MARKET_URL"]
    assert source["universe_selection"]["method"] == "explicit_symbols"
    assert source["universe_selection"]["selected_count"] == 1


def test_loader_discloses_company_sampling_instead_of_claiming_full_market(monkeypatch):
    configure_loader(monkeypatch, ["600001.SH"])
    monkeypatch.setattr(data, "_source_batches", lambda *args, **kwargs: iter([daily_rows()]))
    _, _, source = data.load_kingdom(specification(max_symbols=16))
    assert source["universe_selection"]["method"] == "seeded_company_sample"
    assert source["universe_selection"]["requested_limit"] == 16
    assert source["universe_selection"]["seed"] == 42
    assert any("不是全市场" in warning for warning in source["warnings"])


def test_loader_industry_alignment_keeps_effective_intervals_and_open_end(monkeypatch):
    configure_loader(monkeypatch, ["600001.SH"], {"kcrp_stock_industry": {}})
    monkeypatch.setattr(data, "_source_batches", lambda *args, **kwargs: iter([daily_rows()]))
    membership = pd.DataFrame({"symbol": ["600001.SH", "600001.SH"],
        "begin_date": ["2020-01-01", "2024-01-03"], "end_date": ["2024-01-03", None],
        "industry_name": ["电子", "通信"]})
    monkeypatch.setattr(data, "_bounded", lambda *args, **kwargs: membership)
    daily, _, _ = data.load_kingdom(specification())
    assert daily.industry.tolist() == ["电子", "通信"]


def test_empty_historical_universe_is_an_explicit_error(monkeypatch):
    configure_loader(monkeypatch, [])
    with pytest.raises(ValueError, match="no companies"):
        data.load_kingdom(specification())


def test_news_batches_keep_availability_zero_days_and_cross_batch_deduplication(monkeypatch):
    configure_loader(monkeypatch, ["600001.SH"], {"kcrp_news_info": {}})
    first = pd.DataFrame({"symbol": ["600001.SH"], "ann_date": ["2024-01-01"],
                          "create_time": ["2024-01-01 09:00"], "news_id": ["n1"]})
    second = pd.DataFrame({"symbol": ["600001.SH", "600001.SH"], "ann_date": ["2024-01-02", "2024-01-02"],
                           "create_time": ["2024-01-02 09:00", "2024-01-04 00:00"], "news_id": ["n1", "n2"]})
    def batches(conn, columns, table, *args, **kwargs):
        return iter([daily_rows()] if table == "kcrp_stock_price" else [first, second])
    monkeypatch.setattr(data, "_source_batches", batches)
    _, events, _ = data.load_kingdom(specification(end="2024-01-05"))
    counts = events[0].set_index("available_at").news_count_7d
    assert counts.to_dict() == {pd.Timestamp("2024-01-01"): 0, pd.Timestamp("2024-01-02"): 1,
        pd.Timestamp("2024-01-03"): 1, pd.Timestamp("2024-01-04"): 2, pd.Timestamp("2024-01-05"): 2}


def test_minute_batches_aggregate_before_retention_and_stay_on_same_connection(monkeypatch):
    connections = configure_loader(monkeypatch, ["600001.SH"], {"aiia_stock_realtime_minute_snapshot": {}})
    reads = []
    def batches(conn, columns, table, symbols, start, end, **kwargs):
        reads.append((table, symbols, kwargs))
        if table == "kcrp_stock_price":
            return iter([daily_rows()])
        return iter([pd.DataFrame({"symbol": ["600001", "600001"],
            "bar_end_time": [f"2024-01-{day} 14:59", f"2024-01-{day} 15:00"], "close": [10, 11]})
            for day in ("02", "03")])
    monkeypatch.setattr(data, "_source_batches", batches)
    _, events, _ = data.load_kingdom(specification(include_minute=True), env_name="EXPLICIT_SOURCE")
    assert connections == ["EXPLICIT_SOURCE"]
    assert reads[1][1] == ["600001"]
    assert reads[1][2]["symbol_batch_size"] == 8 and reads[1][2]["days"] == 31
    assert reads[1][2]["row_limit"] == 100000
    assert len(events[0]) == 2 and events[0].minute_bars.tolist() == [2, 2]
    assert events[0].available_at.tolist() == [pd.Timestamp("2024-01-02 15:05"), pd.Timestamp("2024-01-03 15:05")]


def test_market_calendar_is_bounded_and_not_limited_to_selected_symbols(monkeypatch):
    calls = []
    def query(conn, sql, args):
        calls.append((conn, sql, args))
        return [{"trade_date": args[0]}]
    monkeypatch.setattr(data, "query", query)
    connection = object()
    result = data._market_calendar(connection, "2024-01-01", "2025-12-31")
    assert result == ["2024-01-01", "2025-01-01"]
    assert len(calls) == 2
    assert all(conn is connection and "SELECT DISTINCT trade_date" in sql and "stk_code" not in sql
               for conn, sql, args in calls)
    assert [args for _, _, args in calls] == [("2024-01-01", "2025-01-01", 367),
                                              ("2025-01-01", "2026-01-01", 367)]


def test_single_company_prices_carry_wider_market_calendar_and_source_evidence(monkeypatch):
    configure_loader(monkeypatch, ["600001.SH"])
    calendar = ["2024-01-02", "2024-01-03", "2024-01-04"]
    monkeypatch.setattr(data, "_market_calendar", lambda *args: calendar)
    monkeypatch.setattr(data, "_source_batches", lambda *args, **kwargs: iter([daily_rows()]))
    daily, _, source = data.load_kingdom(specification(symbols=("600001.SH",)))
    assert len(daily) == 2 and len(calendar) == 3
    assert daily.attrs["market_calendar"] == calendar
    assert source["market_calendar"]["sessions"] == 3
    assert source["market_calendar"]["source_table"] == "kcrp_stock_price"
    assert source["market_calendar"]["first_session"] == "2024-01-02"
    assert source["market_calendar"]["last_session"] == "2024-01-04"
