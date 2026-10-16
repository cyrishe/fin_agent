"""Match stock risk-warning status to a historical signal date."""
from __future__ import annotations

import pandas as pd


# In the audited 2026 candidates, historical names for N/R do not contain ST;
# S/Y names all do. The source DDL does not define these codes. Keep unknown
# statuses out of this research cohort until independently identified.
ELIGIBLE_TYPES = ("N", "R")


def load_st_intervals(db) -> pd.DataFrame:
    with db.cursor() as cursor:
        cursor.execute("""SELECT LEFT(stk_code,6) AS symbol6,st_type,
                   begin_date,end_date,ann_date FROM kcrp_stock_st""")
        intervals = pd.DataFrame(cursor.fetchall())
    intervals["symbol6"] = intervals.symbol6.astype(str).str.zfill(6)
    intervals["begin_date"] = pd.to_datetime(intervals.begin_date)
    # The source uses 2999-12-31 for an open interval; pandas cannot hold it.
    intervals["end_date"] = pd.to_datetime(
        intervals.end_date.astype(str).replace("2999-12-31", "2100-01-01"))
    intervals["ann_date"] = pd.to_datetime(intervals.ann_date)
    return intervals


def attach_st_status(rows: pd.DataFrame, intervals: pd.DataFrame) -> pd.DataFrame:
    """Use the effective interval [begin_date,end_date) for each stock-day."""
    source = rows.copy()
    source["signal_date"] = pd.to_datetime(source.signal_date)
    source["symbol6"] = source.symbol6.astype(str).str.zfill(6)
    source["candidate_id"] = range(len(source))
    matched = source[["candidate_id", "signal_date", "symbol6"]].merge(
        intervals, on="symbol6", how="left")
    matched = matched[matched.signal_date.ge(matched.begin_date) &
                      matched.signal_date.lt(matched.end_date)]
    if matched.candidate_id.duplicated().any():
        raise ValueError("Overlapping historical ST periods for a candidate")
    result = source.merge(matched[["candidate_id", "st_type", "ann_date"]],
                          on="candidate_id", how="left", validate="one_to_one")
    return result.drop(columns="candidate_id")
