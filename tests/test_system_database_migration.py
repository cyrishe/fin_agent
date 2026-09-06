import pytest

from scripts.migrate_system_database import prepare_destination


class Cursor:
    def __init__(self, exists=False, tables=0):
        self.exists, self.tables = exists, tables
        self.calls = []

    def execute(self, sql, args=None):
        self.calls.append((sql, args))

    def fetchone(self):
        if "COUNT(*)" in self.calls[-1][0]:
            return (self.tables,)
        return ("aiia_system",) if self.exists else None


@pytest.mark.parametrize("exists", [False, True])
def test_prepare_new_or_empty_system_schema(exists):
    cursor = Cursor(exists=exists)
    prepare_destination(cursor)
    assert cursor.calls[-1][0] == "USE aiia_system"
    creates = [sql for sql, _ in cursor.calls if sql.startswith("CREATE DATABASE")]
    assert len(creates) == (0 if exists else 1)
    assert not any("stock_agent" in sql or "kingdomai" in sql for sql, _ in cursor.calls)


def test_prepare_refuses_existing_destination_tables():
    cursor = Cursor(exists=True, tables=21)
    with pytest.raises(RuntimeError, match="refusing overwrite"):
        prepare_destination(cursor)
    assert not any(sql.startswith(("CREATE", "DROP", "DELETE", "TRUNCATE", "USE")) for sql, _ in cursor.calls)
