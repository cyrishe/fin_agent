import pytest

from scripts.migrate_system_database import prepare_destination, renamed_ddl, target_table_name


class Cursor:
    def __init__(self, tables=(), constraints=(), database="stock_agent"):
        self.tables, self.constraints, self.database = tables, constraints, database
        self.calls = []

    def execute(self, sql, args=None):
        self.calls.append((sql, args))

    def fetchone(self):
        return (self.database,)

    def fetchall(self):
        values = self.constraints if "CONSTRAINT_NAME" in self.calls[-1][0] else self.tables
        return [(value,) for value in values]


def test_prepare_shared_schema_preserves_unrelated_tables():
    cursor = Cursor(tables=["aiia_simple_bi_users", "leader_risk_state", "hot_event_state"])
    prepare_destination(cursor, ["aiia_user", "leader_risk_state"])
    assert all(sql.startswith("SELECT") for sql, _ in cursor.calls)


@pytest.mark.parametrize("table", ["aiia_user", "aiia_legacy_leader_risk_state", "aiia_request_usage", "aiia_scheduled_task_run"])
def test_prepare_refuses_existing_destination_tables(table):
    cursor = Cursor(tables=[table])
    with pytest.raises(RuntimeError, match="refusing overwrite"):
        prepare_destination(cursor, ["aiia_user", "leader_risk_state"])
    assert not any(sql.startswith(("CREATE", "DROP", "DELETE", "TRUNCATE", "USE")) for sql, _ in cursor.calls)


def test_prepare_rejects_wrong_database_or_foreign_key_collision():
    with pytest.raises(RuntimeError, match="stock_agent schema"):
        prepare_destination(Cursor(database="kingdomai"), ["aiia_user"])
    with pytest.raises(RuntimeError, match="foreign-key"):
        prepare_destination(Cursor(constraints=["fk_user"]), ["aiia_user"], ["fk_user"])


def test_legacy_name_mapping_changes_only_create_table_name():
    ddl = "CREATE TABLE `leader_risk_state` (`id` int PRIMARY KEY) COMMENT='leader_risk_state history'"
    migrated = renamed_ddl(ddl, "leader_risk_state")
    assert migrated.startswith("CREATE TABLE `aiia_legacy_leader_risk_state`")
    assert migrated.endswith("COMMENT='leader_risk_state history'")
    assert target_table_name("aiia_user") == "aiia_user"
    with pytest.raises(RuntimeError, match="Unreviewed"):
        target_table_name("unrelated_table")
