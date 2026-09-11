from src.utils.mysql_utils import StockInfoDbUtils


def test_stock_info_db_uses_business_url_when_legacy_password_is_missing(monkeypatch) -> None:
    captured = {}

    class Connection:
        def close(self):
            pass

    def connect(**kwargs):
        captured.update(kwargs)
        return Connection()

    monkeypatch.delenv("KINGDOMAI_DB_PASSWORD", raising=False)
    monkeypatch.delenv("KINGDOMAI_DB_URL", raising=False)
    monkeypatch.delenv("KINGDOMAI_DB_HOST", raising=False)
    monkeypatch.delenv("KINGDOMAI_DB_PORT", raising=False)
    monkeypatch.delenv("KINGDOMAI_DB_USER", raising=False)
    monkeypatch.delenv("KINGDOMAI_DB_CREDENTIAL_SOURCE", raising=False)
    monkeypatch.setenv(
        "BUSINESS_DB_URL",
        "mysql+pymysql://api_user:encoded%20password@127.0.0.9:3307/kingdomai?charset=utf8mb4",
    )
    monkeypatch.setattr("src.utils.mysql_utils.pymysql.connect", connect)

    db = StockInfoDbUtils(database="kingdomai")

    assert db.host == "127.0.0.9"
    assert db.user == "api_user"
    assert db.password == "encoded password"
    assert db.database == "kingdomai"
    assert db.port == 3307
    assert captured["password"] == "encoded password"


def test_stock_info_db_can_reuse_named_credentials_with_target_override(monkeypatch) -> None:
    captured = {}

    class Connection:
        def close(self):
            pass

    monkeypatch.setenv("KINGDOMAI_DB_HOST", "47.94.1.2")
    monkeypatch.setenv("KINGDOMAI_DB_PORT", "3312")
    monkeypatch.setenv("KINGDOMAI_DB_CREDENTIAL_SOURCE", "PLATFORM_DB_URL")
    monkeypatch.setenv(
        "PLATFORM_DB_URL",
        "mysql+pymysql://shared_user:shared%20password@127.0.0.8:3306/stock_agent",
    )
    monkeypatch.delenv("KINGDOMAI_DB_URL", raising=False)
    monkeypatch.delenv("KINGDOMAI_DB_USER", raising=False)
    monkeypatch.delenv("KINGDOMAI_DB_PASSWORD", raising=False)
    monkeypatch.setattr(
        "src.utils.mysql_utils.pymysql.connect",
        lambda **kwargs: captured.update(kwargs) or Connection(),
    )

    db = StockInfoDbUtils(database="kingdomai")

    assert db.host == "47.94.1.2"
    assert db.port == 3312
    assert db.user == "shared_user"
    assert db.password == "shared password"
    assert db.database == "kingdomai"
    assert captured["database"] == "kingdomai"
