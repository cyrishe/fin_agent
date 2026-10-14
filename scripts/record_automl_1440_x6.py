"""Record the frozen 2026-10-13 X6 capture replay under its own strategy ID."""
from __future__ import annotations

import argparse
import copy
import getpass
import json
from pathlib import Path

from scripts.run_automl_1440_strategy import DEFINITION, ROOT, save_selection


DAY = "2026-10-13"
STRATEGY_ID = "x6_20261013_rolling20_7f_sklearn171_postfill"
EVIDENCE = ROOT / "docs/stock_automl_runs/20261013_171_1440_postfill_x6/evidence"


def load_record(root: Path) -> tuple[dict, dict, dict]:
    decision = json.loads((root / "capture_replay" / f"{DAY}_decision.json").read_text())
    meta = json.loads((root / "cache" / f"{DAY}_model.json").read_text())
    if decision["day"] != DAY or decision["mode"] != "saved_1440_capture_replay":
        raise ValueError("X6 决策日期或来源不匹配")
    if decision["sklearn_version"] != "1.7.1":
        raise ValueError("X6 必须使用已核验的 sklearn 1.7.1 模型")
    for key in ("model_sha256", "training_sha256", "training_rows"):
        if decision[key] != meta[key]:
            raise ValueError(f"X6 决策与模型清单的 {key} 不匹配")
    if len(decision["picks"]) != 2 or [p["selection_rank"] for p in decision["picks"]] != [1, 2]:
        raise ValueError("X6 决策必须含原始 Top1 和 Top2")
    definition = copy.deepcopy(DEFINITION)
    definition["title"] = "X6 2026-10-13 补数后 1.7.1 七因子模拟"
    definition["summary"] = (
        "保存的14:40:08行情快照在补数后以sklearn 1.7.1重新评分；"
        "X6实验记录，未复现近期B1输入，不作为B1实时信号。"
    )
    definition["formula"].update({
        "experiment": "X6",
        "sklearn_version": "1.7.1",
        "training_sha256": meta["training_sha256"],
        "model_sha256": meta["model_sha256"],
        "capture_sha256": decision["capture_sha256"],
        "capture_mode": decision["mode"],
    })
    return decision, meta, definition


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--user-id", default=getpass.getuser())
    parser.add_argument("--evidence", type=Path, default=EVIDENCE)
    parser.add_argument("--write-db", action="store_true")
    args = parser.parse_args()
    decision, meta, definition = load_record(args.evidence)
    if args.write_db:
        save_selection(
            args.env_file, args.user_id, DAY, decision["picks"], meta,
            decision["candidate_count"], decision["mode"], decision["capture_at"],
            strategy_id=STRATEGY_ID, strategy_definition=definition,
            extra_context={"experiment": "X6", "sklearn_version": "1.7.1",
                           "capture_sha256": decision["capture_sha256"],
                           "baseline_reproduced": False},
        )
    print(json.dumps({"signal_date": DAY, "strategy_id": STRATEGY_ID,
                      "model_sha256": meta["model_sha256"],
                      "top1": decision["picks"][0]["symbol6"],
                      "top1_actionable": decision["top1_actionable"],
                      "saved_to_kingdomai": args.write_db}, ensure_ascii=False))


if __name__ == "__main__":
    main()
