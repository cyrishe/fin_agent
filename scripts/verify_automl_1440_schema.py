"""Transactional protocol check for the two kingdomai strategy tables."""
from __future__ import annotations

import uuid
from pathlib import Path

import pymysql

from scripts.run_automl_1440_strategy import write_connection


def main() -> None:
    user = "automl_test_" + uuid.uuid4().hex[:12]
    with write_connection(Path(".env")) as db:
        try:
            with db.cursor() as cursor:
                cursor.execute("""INSERT INTO automl_strategy_definition
                    (user_id,strategy_id,title,summary,training_method,
                     selection_formula,execution_formula,formula_json,definition_sha256)
                    VALUES (%s,'protocol_test','test','test','test','test','test',%s,%s)""",
                    (user, '{"compatible_extra":true}', "0" * 64))
                insert = """INSERT INTO automl_strategy_selection
                    (user_id,strategy_id,signal_date,selection_rank,stock_code,stock_name,
                     signal_price,signal_return,predicted_class,p_lt0,p_0to1,p_1to3,p_ge3,
                     amount_prior20_valid_days,turnover_gate_pass,actionable,candidate_count,
                     training_rows,training_dates_json,features_json,context_json,quote_source)
                    VALUES (%s,%s,'2026-10-09',%s,%s,'test',10,0.04,'ge3',
                            0.1,0.2,0.3,0.4,20,1,%s,200,6000,%s,%s,%s,'test')"""
                for rank, code, context in ((1, "000001", '{"future_optional":7}'),
                                            (2, "000002", '{}')):
                    cursor.execute(insert, (user, "protocol_test", rank, code,
                                            int(rank == 1), '["2026-10-08"]',
                                            '{"signal_return":0.04}', context))
                cursor.execute("SELECT COUNT(*) AS n FROM automl_strategy_selection "
                               "WHERE user_id=%s", (user,))
                assert cursor.fetchone()["n"] == 2
                try:
                    cursor.execute(insert, (user, "protocol_test", 1, "000003", 1,
                                            '["2026-10-08"]', '{}', '{}'))
                except pymysql.IntegrityError as exc:
                    assert exc.args[0] == 1062  # duplicate rank for same user/strategy/day
                else:
                    raise AssertionError("Duplicate rank was accepted")
                try:
                    cursor.execute(insert, (user + "_orphan", "protocol_test", 1,
                                            "000003", 1, '["2026-10-08"]', '{}', '{}'))
                except pymysql.IntegrityError as exc:
                    assert exc.args[0] == 1452  # missing owned strategy
                else:
                    raise AssertionError("Orphan selection was accepted")
        finally:
            db.rollback()
        with db.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS n FROM automl_strategy_definition "
                           "WHERE user_id=%s", (user,))
            assert cursor.fetchone()["n"] == 0
    print("normal, compatible JSON, duplicate-rank rejection, foreign-key rejection: OK")


if __name__ == "__main__":
    main()
