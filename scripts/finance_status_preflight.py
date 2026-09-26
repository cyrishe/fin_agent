"""Print missing monitor prerequisites and proposed indexes; never execute DDL."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
from src.finance_api.data_status import MonitorDatabase, datasets


def main():
    db = MonitorDatabase()
    try:
        with db.conn.cursor() as c:
            c.execute("SHOW TABLES LIKE 'aiia_trade_calendar'")
            if not c.fetchone():
                print("-- REQUIRED: synchronize aiia_trade_calendar, market_code=CN_A, including current dates.")
            for spec in datasets():
                if spec.database != "kingdomai":
                    print(f"-- {spec.id}: uses {spec.database}, inspect that provider's database separately.")
                    continue
                if spec.id == "stock.report_metric":
                    continue
                c.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (spec.table,))
                columns = {r[0] for r in c.fetchall()}
                if spec.date_field not in columns:
                    print(f"-- Missing table/field: {spec.id}: {spec.table}.{spec.date_field}")
                    continue
                c.execute("SELECT COLUMN_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND SEQ_IN_INDEX=1", (spec.table,))
                if spec.date_field not in {r[0] for r in c.fetchall()}:
                    print(f"-- {spec.id}: review storage/maintenance window before applying")
                    print(f"CREATE INDEX `idx_monitor_{spec.date_field}` ON `{spec.table}` (`{spec.date_field}`);")
    finally:
        db.close_db()


if __name__ == "__main__":
    main()
