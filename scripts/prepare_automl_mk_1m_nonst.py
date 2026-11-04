"""Audit historical ST cohorts and prepare the unchanged seven-factor non-ST sample."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from scripts.automl_st_status import attach_st_status, load_st_intervals
from scripts.audit_automl_mk_1m_entry_limit import mark_entry_limit
from scripts.benchmark_automl_1440_inference import db_connection


REFERENCE = Path(
    "docs/stock_automl_runs/20261010_four_class_similar_days/prepared_candidates.csv.gz")


def annotate(rows: pd.DataFrame, intervals: pd.DataFrame) -> pd.DataFrame:
    result = attach_st_status(rows, intervals)
    result["historical_status_known"] = (
        result.st_type.notna()
        & (result.ann_date.le(result.signal_date)
           | (result.st_type.isin(("N", "R")) & result.ann_date.isna())))
    result["historical_st"] = result.st_type.isin(("S", "Y"))
    result["signal_date"] = result.signal_date.dt.strftime("%Y-%m-%d")
    return result


def cohort_summary(rows: pd.DataFrame) -> dict:
    frame = rows.copy()
    frame["label_ge3"] = frame.second_high.div(frame.signal_price).sub(1).ge(.03)
    frame["sale_ge3"] = frame.close_40.div(frame.signal_price).sub(1).ge(.03)
    frame["labeled"] = frame.second_high.notna()
    result = {}
    for month, group in frame.groupby(frame.signal_date.str[:7]):
        result[month] = {"rows": len(group),
                         "historical_st": int(group.historical_st.sum()),
                         "unknown_status": int((~group.historical_status_known).sum()),
                         "cohorts": {}}
        for flag, cohort in group.groupby("historical_st"):
            labeled = cohort[cohort.labeled]
            sold = cohort[cohort.close_40.notna()]
            result[month]["cohorts"]["ST" if flag else "non_ST"] = {
                "rows": len(cohort), "labeled": len(labeled),
                "label_ge3": int(labeled.label_ge3.sum()),
                "label_ge3_rate": float(labeled.label_ge3.mean()) if len(labeled) else None,
                "sale_known": len(sold),
                "sale_ge3": int(sold.sale_ge3.sum()),
                "sale_ge3_rate": float(sold.sale_ge3.mean()) if len(sold) else None,
            }
    return result


def period_entry_split(rows: pd.DataFrame, start: str, end: str) -> dict:
    checked = mark_entry_limit(rows.rename(
        columns={"t_reference_preclose": "preclose"}))
    checked = checked[checked.signal_date.ge(start) & checked.signal_date.lt(end) &
                      checked.second_high.notna()]
    groups = {
        "ST_at_limit": checked[checked.historical_st & checked.entry_at_limit],
        "ST_not_at_limit": checked[checked.historical_st & ~checked.entry_at_limit],
        "non_ST": checked[~checked.historical_st],
    }
    return {name: {
        "labeled": len(group),
        "label_ge3": int((group.second_high / group.signal_price - 1).ge(.03).sum()),
        "sale_0940_ge3": int((group.close_40 / group.signal_price - 1).ge(.03).sum()),
    } for name, group in groups.items()}


def jan_jun_entry_split(rows: pd.DataFrame) -> dict:
    return period_entry_split(rows, "2026-01-01", "2026-07-01")


def run(source: Path, output: Path, report: Path,
        reference: Path = REFERENCE) -> dict:
    rows = pd.read_csv(source, dtype={"symbol6": str}, low_memory=False)
    prior = pd.read_csv(reference, dtype={"symbol6": str}, low_memory=False)
    with db_connection(Path(".env")) as db:
        intervals = load_st_intervals(db)
    current = annotate(rows, intervals)
    comparison = annotate(prior, intervals)
    if not current.historical_status_known.all():
        raise ValueError("Cannot form non-ST training cohort with unknown historical status")
    nonst = current[~current.historical_st].drop(
        columns=["st_type", "ann_date", "historical_status_known", "historical_st"])
    output.parent.mkdir(parents=True, exist_ok=True)
    nonst.to_csv(output, index=False, compression="gzip")
    report.parent.mkdir(parents=True, exist_ok=True)
    summary = {"jan_jul": cohort_summary(current),
               "aug_sep_reference": cohort_summary(comparison),
               "jan_jun_entry_split": jan_jun_entry_split(current),
               "july_before_rule_change_entry_split": period_entry_split(
                   current, "2026-07-01", "2026-07-06"),
               "july_after_rule_change_entry_split": period_entry_split(
                   current, "2026-07-06", "2026-08-01"),
               "jan_jul_rows": len(current), "jan_jul_nonst_rows": len(nonst),
               "aug_sep_rows": len(comparison),
               "source": str(source), "reference": str(reference)}
    report.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--reference", type=Path, default=REFERENCE)
    a = p.parse_args()
    print(json.dumps(run(a.source, a.output, a.report, a.reference),
                     ensure_ascii=False, indent=2))
