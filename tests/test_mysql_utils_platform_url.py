from src.utils import mysql_utils


class FakeConnection:
    def close(self):
        return None


def test_mysql_utils_uses_platform_db_url_when_legacy_password_is_missing(monkeypatch):
    captured = {}

    def connect(**kwargs):
        captured.update(kwargs)
        return FakeConnection()

    monkeypatch.delenv("STOCK_AGENT_DB_PASSWORD", raising=False)
    monkeypatch.setenv(
        "PLATFORM_DB_URL",
        "mysql+pymysql://platform_user:platform_password@db.example:3307/stock_agent?charset=utf8mb4",
    )
    monkeypatch.setattr(mysql_utils.pymysql, "connect", connect)

    db = mysql_utils.MySQLUtils()

    assert captured["host"] == "db.example"
    assert captured["port"] == 3307
    assert captured["user"] == "platform_user"
    assert captured["password"] == "platform_password"
    assert captured["database"] == "stock_agent"
    db.close_db()
