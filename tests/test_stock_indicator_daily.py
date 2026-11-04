from contextlib import contextmanager
from datetime import date, datetime
import gzip
import json
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from src.services.stock_indicator_daily_job import compute_security, run_daily
from src.services.stock_indicator_store import apply_schema, publish_daily_batch, save_input_object
from src.services.technical_indicator_calculator import STANDARD_TECHNICAL_FEATURES, calculate_standard_technical_features


def source_rows(count=300):
    rows = []
    for i, stamp in enumerate(pd.bdate_range("2024-01-01", periods=count)):
        close = 100 + i * .1 + np.sin(i)
        rows.append(dict(trade_date=stamp.date(), close=close, high=close + 2, low=close - 2, volume=1000 + i))
    return rows


def test_evidence_replays_calculation_and_identical_inputs_reuse_files(tmp_path):
    rows = source_rows()
    output, evidence = compute_security(rows, code="600519.SH", trade_date=rows[-1]["trade_date"], artifact_root=tmp_path)
    restored = []
    for ref in evidence["chunks"]:
        restored.extend(json.loads(gzip.decompress((tmp_path / ref).read_bytes()))["rows"])
    frame = pd.DataFrame(restored, columns=["trade_date", "close", "high", "low", "volume"])
    frame.index = pd.DatetimeIndex(pd.to_datetime(frame.pop("trade_date")))
    expected = calculate_standard_technical_features(frame).iloc[-1]
    for name in STANDARD_TECHNICAL_FEATURES:
        assert output[name] == pytest.approx(expected[name])
    files = list(tmp_path.rglob("*.gz"))
    same, same_evidence = compute_security(rows, code="600519.SH", trade_date=rows[-1]["trade_date"], artifact_root=tmp_path)
    assert output == same and evidence == same_evidence
    assert list(tmp_path.rglob("*.gz")) == files
    # A history correction changes the fingerprint, including when end time is unchanged.
    rows[10]["close"] += .1
    changed, updated = compute_security(rows, code="600519.SH", trade_date=rows[-1]["trade_date"], artifact_root=tmp_path)
    assert changed["input_fingerprint"] != output["input_fingerprint"]
    assert updated["chunks"][1] == evidence["chunks"][1]


def test_short_history_nulls_and_source_zero_prices_stay_unknown(tmp_path):
    rows = source_rows(8)
    rows[1]["close"] = 0
    output, evidence = compute_security(rows, code="600519.SH", trade_date=rows[-1]["trade_date"], artifact_root=tmp_path)
    assert output["macd_hist"] is None and output["ma60"] is None
    assert output["ma5"] is not None
    saved = json.loads(gzip.decompress((tmp_path / evidence["chunks"][0]).read_bytes()))
    assert saved["rows"][1][1] is None
    rows[-1]["close"] = 0
    with pytest.raises(ValueError, match="publication-date"):
        compute_security(rows, code="600519.SH", trade_date=rows[-1]["trade_date"], artifact_root=tmp_path)
    with pytest.raises(ValueError, match="no input"):
        compute_security([], code="600519.SH", trade_date=date.today(), artifact_root=tmp_path)


def test_objects_reject_non_json_numbers_and_publish_dates_are_closed(tmp_path):
    with pytest.raises(ValueError):
        save_input_object(tmp_path, {"value": float("nan")})
    factory = Mock()
    with pytest.raises(ValueError, match="completed EOD"):
        run_daily(trade_date=date(2026, 9, 28), artifact_root=tmp_path,
                  now=datetime(2026, 9, 28, 15, tzinfo=ZoneInfo("Asia/Shanghai")), connection_factory=factory)
    factory.assert_not_called()


class Cursor:
    def __init__(self, connection):
        self.connection = connection
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, sql, args=()): self.connection.sql.append((sql, args))
    def fetchone(self): return self.connection.existing
    def executemany(self, sql, rows):
        if self.connection.fail:
            raise RuntimeError("simulated row write failure")
        self.connection.rows.extend(rows)


class Connection:
    def __init__(self, *, existing=None, fail=False):
        self.existing, self.fail = existing, fail
        self.sql, self.rows = [], []
        self.commits, self.rollbacks = 0, 0
    def cursor(self): return Cursor(self)
    def begin(self): pass
    def commit(self): self.commits += 1
    def rollback(self): self.rollbacks += 1


def batch():
    return dict(batch_id="a" * 64, universe_key="fixture", trade_date=date(2026, 9, 28),
                formula_revision="technical_daily_v1", price_basis="hfq", source_name="fixture",
                source_watermark=None, input_manifest_ref="objects/fixture.json.gz", requested_count=1)


def output_row():
    return dict(stk_code="600519.SH", input_start_time=datetime(2026, 1, 1),
                input_end_time=datetime(2026, 9, 28), input_fingerprint="b" * 64, bar_count=180,
                **{name: None for name in STANDARD_TECHNICAL_FEATURES})


def test_publication_is_one_transaction_and_retry_never_mutates_published_rows():
    connection = Connection()
    got = publish_daily_batch(connection, batch=batch(), rows=[output_row()])
    assert not got["reused"] and connection.commits == 1
    assert "published_at=UTC_TIMESTAMP" in connection.sql[-1][0]
    old = Connection(existing={"published_at": datetime(2026, 9, 28), "written_count": 1})
    assert publish_daily_batch(old, batch=batch(), rows=[output_row()])["reused"]
    assert len(old.sql) == 1 and old.rows == [] and old.commits == 0


def test_partial_row_failure_rolls_back_without_publishing():
    connection = Connection(fail=True)
    with pytest.raises(RuntimeError):
        publish_daily_batch(connection, batch=batch(), rows=[output_row()])
    assert connection.commits == 0 and connection.rollbacks == 1
    assert not any("SET written_count=" in sql for sql, _ in connection.sql)
    with pytest.raises(ValueError):
        publish_daily_batch(Connection(), batch=batch(), rows=[])


def test_schema_target_mismatch_never_executes_ddl():
    connection = Connection(existing={"db": "kingdomai", "server_uuid": "wrong"})
    with pytest.raises(ValueError, match="verified kingdomai"):
        apply_schema(connection, expected_server_uuid="expected")
    assert len(connection.sql) == 1
