"""Keep historical ST candidates only when the 14:40 price is below its cap."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from scripts.automl_st_status import load_st_intervals
from scripts.audit_automl_mk_1m_entry_limit import mark_entry_limit
from scripts.benchmark_automl_1440_inference import db_connection
from scripts.prepare_automl_mk_1m_nonst import REFERENCE, annotate


def prepare(rows: pd.DataFrame, intervals: pd.DataFrame,
            reference: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    marked = mark_entry_limit(annotate(rows, intervals).rename(
        columns={"t_reference_preclose": "preclose"}))
    if not marked.entry_status_known.all():
        raise ValueError("Historical ST status or signal-day preclose is missing")
    kept = marked[marked.entry_eligible].copy()
    monthly = {}
    for month, group in kept.groupby(kept.signal_date.str[:7]):
        monthly[month] = {"rows": len(group),
                          "st_rows": int(group.historical_st.sum()),
                          "st_share": float(group.historical_st.mean())}
    ref = annotate(reference, intervals)
    def outcomes(frame: pd.DataFrame) -> dict:
        labeled = frame[frame.second_high.notna()]
        sale = frame[frame.close_40.notna()]
        return {"rows": len(frame), "labeled": len(labeled),
                "target_ge3": int((labeled.second_high /
                                   labeled.signal_price - 1).ge(.03).sum()),
                "sale_known": len(sale),
                "sale_0940_ge3": int((sale.close_40 /
                                      sale.signal_price - 1).ge(.03).sum())}
    report = {
        "raw_rows": len(rows), "raw_st_rows": int(marked.historical_st.sum()),
        "removed_at_limit_rows": int(marked.entry_at_limit.sum()),
        "removed_at_limit_st_rows": int((marked.entry_at_limit &
                                          marked.historical_st).sum()),
        "retained_rows": len(kept),
        "retained_st_rows": int(kept.historical_st.sum()),
        "retained_st_share": float(kept.historical_st.mean()),
        "aug_sep_reference_rows": len(ref),
        "aug_sep_reference_st_rows": int(ref.historical_st.sum()),
        "aug_sep_reference_st_share": float(ref.historical_st.mean()),
        "retained_st_outcomes": outcomes(kept[kept.historical_st]),
        "retained_nonst_outcomes": outcomes(kept[~kept.historical_st]),
        "aug_sep_st_outcomes": outcomes(ref[ref.historical_st]),
        "monthly": monthly,
    }
    kept = kept.drop(columns=["st_type", "ann_date", "historical_status_known",
                              "historical_st", "preclose", "limit_pct",
                              "limit_price", "entry_at_limit",
                              "entry_status_known", "entry_eligible"])
    return kept, report


def run(source: Path, output: Path, report_path: Path,
        reference: Path = REFERENCE) -> dict:
    rows = pd.read_csv(source, dtype={"symbol6": str}, low_memory=False)
    ref = pd.read_csv(reference, dtype={"symbol6": str}, low_memory=False)
    with db_connection(Path(".env")) as db:
        intervals = load_st_intervals(db)
    kept, report = prepare(rows, intervals, ref)
    output.parent.mkdir(parents=True, exist_ok=True)
    kept.to_csv(output, index=False, compression="gzip")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--reference", type=Path, default=REFERENCE)
    a = p.parse_args()
    print(json.dumps(run(a.source, a.output, a.report, a.reference),
                     ensure_ascii=False, indent=2))
