"""Audit/apply additive indexes for actual finance query and monitor access paths.

Dry run by default. Only the dedicated new kingdomai is accepted. DDL and plans
are backed up before changes; online-only DDL fails instead of copying tables.
This is an operator migration, never executed automatically by the application.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def has_prefix(indexes, columns):
    groups = {}
    for row in indexes:
        groups.setdefault(row["Key_name"], []).append(row)
    for parts in groups.values():
        prefix = sorted(parts, key=lambda row: row["Seq_in_index"])[:len(columns)]
        if (len(prefix) == len(columns)
                and all(row["Seq_in_index"] == i + 1 and row["Column_name"] == column
                        and not row.get("Sub_part") and row.get("Visible", "YES") == "YES"
                        for i, (row, column) in enumerate(zip(prefix, columns)))):
            return True
    return False


def candidates():
    from src.finance_api.data_status import datasets
    from src.experiments.staged_data_protocol.phase2.quote_provider import QUOTE_SOURCES
    from src.experiments.staged_data_protocol.phase2.moneyflow_provider import MONEYFLOW_SOURCES
    from src.experiments.staged_data_protocol.phase2.pricevalue_provider import PRICEVALUE_SOURCES
    from src.experiments.staged_data_protocol.phase2.stock_corporate_provider import STOCK_CORPORATE_VIEWS
    paths = {}
    for sources in (QUOTE_SOURCES, MONEYFLOW_SOURCES, PRICEVALUE_SOURCES):
        for source in sources.values():
            code = source.fields["code"].split(".")[-1]
            paths[(source.table, (code, "trade_date"))] = "证券代码范围内的历史行情/资金/估值"
    paths[("kcrp_stock_margintrade", ("stk_code", "trade_date"))] = "单证券融资融券历史"
    # These are real code/date predicates in the corporate UNION and financial
    # provider, not inferred from arbitrary date-named columns.
    for table, day in [("income", "report_period"), ("salessegment", "report_period"),
                       ("top5opincome", "report_period"), ("profitnotice", "report_period"),
                       ("pledge_and_frozen", "end_date"), ("add_issue", "ann_date")]:
        paths[(f"kcrp_stock_{table}", ("stk_code", day))] = "公司披露/财务按股票查询"
    for view in STOCK_CORPORATE_VIEWS.values():
        for source in view.sources:
            expression = source.fields.get(view.default_date_field, "")
            if expression.startswith("q.") and expression[2:].isidentifier():
                paths[(source.table, (expression[2:],))] = "公司披露跨证券按日期查询/三个月优先读取"
    monitor = {}
    for spec in datasets():
        if spec.database != "kingdomai" or spec.realtime or spec.id == "stock.report_metric":
            continue
        monitor[(spec.table, (spec.date_field,))] = "状态页 MAX(日期) 与近六日数量统计"
    return paths, monitor


def explain(cursor, sql, params=()):
    cursor.execute("EXPLAIN " + sql, params)
    return cursor.fetchall()


def run(args):
    from dotenv import load_dotenv
    import pymysql
    from scripts.sync_shareholder_table import connection
    load_dotenv(args.env_file)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Refuse overwriting previous migration evidence.
    with args.output.open("x") as handle:
        handle.write("{}\n")
    report = {"target": "47.94.1.2:3312/kingdomai", "apply": args.apply,
              "started_at": time.strftime("%Y-%m-%d %H:%M:%S"), "tables": {}, "actions": []}

    def save():
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n")

    with connection("PLATFORM_DB_URL", ("47.94.1.2", 3312, "kingdomai"), "kingdomai") as db:
        db.autocommit(True)
        db._read_timeout = 1800  # index build; not a query timeout or retry
        with db.cursor(pymysql.cursors.DictCursor) as c:
            c.execute("SELECT VERSION() AS version")
            report["mysql"] = c.fetchone()["version"]
            c.execute("SET SESSION lock_wait_timeout=5")
            c.execute("SET SESSION MAX_EXECUTION_TIME=15000")
            paths, monitor = candidates()
            for table in sorted({key[0] for key in (*paths, *monitor)}):
                if args.tables and table not in args.tables:
                    continue
                c.execute(f"SHOW CREATE TABLE `{table}`")
                ddl = c.fetchone()["Create Table"]
                c.execute(f"SHOW INDEX FROM `{table}`")
                indexes = c.fetchall()
                c.execute(f"SHOW COLUMNS FROM `{table}`")
                columns = {r["Field"]: r["Type"] for r in c.fetchall()}
                c.execute("SELECT TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (table,))
                size = c.fetchone()["TABLE_ROWS"] or 0
                report["tables"][table] = {"ddl_before": ddl, "indexes_before": indexes, "estimated_rows": size}
                additions = []
                for (name, fields), reason in {**paths, **monitor}.items():
                    if name != table:
                        continue
                    entry = {"table": table, "columns": fields, "reason": reason}
                    if any(field not in columns for field in fields):
                        entry["note"] = "源表缺少该查询/监控列，未修改表结构。"
                    elif has_prefix(indexes, fields):
                        entry["note"] = "已存在等价的可见前导索引，直接复用。"
                    elif size < 100000 and table != "reports":
                        entry["note"] = "当前为小表，先不增加额外索引写入成本。"
                    else:
                        entry["note"] = "该实际访问路径缺少索引，纳入本次新增清单。"
                        entry["name"] = "idx_fa_" + "_".join(fields)
                        if any(row["Key_name"] == entry["name"] for row in indexes):
                            raise RuntimeError(f"Conflicting index name on {table}")
                        additions.append(entry)
                    if all(field in columns for field in fields):
                        day = fields[-1]
                        if len(fields) == 1:
                            sql, params = (f"SELECT * FROM `{table}` ORDER BY `{day}` DESC LIMIT 20"
                                           if (name, fields) not in monitor else f"SELECT MAX(`{day}`) FROM `{table}`"), ()
                        else:
                            c.execute(f"SELECT `{fields[0]}` AS code FROM `{table}` LIMIT 1")
                            sample = c.fetchone()
                            sql = f"SELECT * FROM `{table}` WHERE `{fields[0]}`=%s ORDER BY `{day}` DESC LIMIT 20"
                            params = (sample["code"],) if sample else ("",)
                        entry.update(sql=sql, params=params, plan_before=explain(c, sql, params))
                    report["actions"].append(entry)
                save()  # all DDL/metadata durable before ALTER for this table
                if args.apply and additions:
                    if "ENGINE=InnoDB" not in ddl:
                        raise RuntimeError(f"Online InnoDB migration required: {table}")
                    terms = [f"ADD INDEX `{e['name']}` ({', '.join('`'+v+'`' for v in e['columns'])})" for e in additions]
                    statement = f"ALTER TABLE `{table}` " + ", ".join(terms) + ", ALGORITHM=INPLACE, LOCK=NONE"
                    print(json.dumps({"building": table, "indexes": [e["columns"] for e in additions]}, ensure_ascii=False), flush=True)
                    start = time.monotonic()
                    try:
                        c.execute(statement)
                    except Exception as exc:
                        report["error"] = {"table": table, "type": type(exc).__name__, "message": str(exc)}
                        save()
                        raise
                    c.execute(f"SHOW INDEX FROM `{table}`")
                    current = c.fetchall()
                    for entry in additions:
                        if not has_prefix(current, entry["columns"]):
                            raise RuntimeError(f"Index not verified: {table}")
                        entry.update(applied=True, table_ddl_seconds=round(time.monotonic()-start, 3),
                                     plan_after=explain(c, entry["sql"], entry["params"]))
                    report["tables"][table]["indexes_after"] = current
                    save()
                    print(json.dumps({"done": table, "seconds": additions[0]["table_ddl_seconds"]}), flush=True)
                elif not args.apply:
                    print(json.dumps({"table": table, "proposed": [e["columns"] for e in additions]}, ensure_ascii=False), flush=True)
    report["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tables", nargs="*")
    parser.add_argument("--apply", action="store_true")
    run(parser.parse_args())
