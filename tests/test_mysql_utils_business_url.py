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
