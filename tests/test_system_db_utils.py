import pytest

from src.utils import system_db_utils


class FakeConnection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_system_db_utils_uses_only_system_db_url(monkeypatch) -> None:
    captured = {}
    connection = FakeConnection()

    def connect(**kwargs):
        captured.update(kwargs)
        return connection

    monkeypatch.setenv(
        "SYSTEM_DB_URL",
        "mysql+pymysql://system_user:encoded%40password@127.0.0.8:3307/aiia_system?charset=utf8mb4",
    )
    monkeypatch.setenv(
        "BUSINESS_DB_URL",
        "mysql+pymysql://business_user:wrong@127.0.0.9:3306/kingdomai",
    )
    monkeypatch.setattr(system_db_utils.pymysql, "connect", connect)

    db = system_db_utils.SystemDbUtils()

    assert captured["host"] == "127.0.0.8"
    assert captured["port"] == 3307
    assert captured["user"] == "system_user"
    assert captured["password"] == "encoded@password"
    assert captured["database"] == "aiia_system"
    db.close_db()
    assert connection.closed is True


def test_system_db_utils_fails_without_system_url(monkeypatch) -> None:
    monkeypatch.delenv("SYSTEM_DB_URL", raising=False)
    monkeypatch.setenv(
        "BUSINESS_DB_URL",
        "mysql+pymysql://business_user:password@127.0.0.9:3306/kingdomai",
    )

    with pytest.raises(RuntimeError, match="SYSTEM_DB_URL is required"):
        system_db_utils.system_db_connection_kwargs()
