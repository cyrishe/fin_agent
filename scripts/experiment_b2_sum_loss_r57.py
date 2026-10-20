"""Replay frozen B2 with a pause when the prior 5/10-day return sum is negative."""
import json
from pathlib import Path

import pandas as pd

from scripts.experiment_b2_recent_loss_resume_r55 import main
from scripts.replay_limit_rebound_cash_r23 import dump, sha

OUT = Path("docs/stock_automl_runs/20261020_b2_sum_loss_r57")


if __name__ == "__main__":
    main(gate_rule="sum_negative", out=OUT)
    labels = pd.read_csv(OUT / "人工停摆.csv", dtype={"月份": str})
    months = pd.read_csv(OUT / "逐月.csv", dtype={"月份": str})
    joined = labels.merge(months[["月份", "前月末5日", "前月末10日", "月初暂停"]],
                          on="月份", validate="one_to_one")
    assert len(joined) == len(labels) == 31
    assert joined.停摆.eq(joined.月初暂停.astype(int)).all()
    assert joined.停摆.eq((joined.前月末5日 + joined.前月末10日 < 0).astype(int)).all()
    audit = json.loads((OUT / "audit.json").read_text())
    audit.update(manual_labels_sha256=sha(OUT / "人工停摆.csv"),
                 manual_labels_matched=len(joined),
                 manual_labels_total=len(labels))
    dump(OUT / "audit.json", audit)
