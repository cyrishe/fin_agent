"""Compact, auditable differences between R86 old-source and R106 QDC replay."""
from __future__ import annotations

import json

import joblib
import pandas as pd

from scripts.research_b2_qdc_r106 import OUT, RISK, ROOT, digest
from scripts import research_b2_qdc_r104 as r104
from scripts import research_b2_qdc_r105 as r105


def main() -> None:
    old = pd.read_csv(ROOT / "docs/stock_automl_runs/20261022_b2_floor_countermeasure_r86/306笔_综合因子与基线.csv",
                      dtype={"stk_code": str})
    new = pd.read_pickle(OUT / "risk_sell_panel.pkl")
    keys = ["stk_code", "d0_date"]
    linked = new.merge(old[keys + ["entry", "d2_close", "D1_Q75", "D1买前公告否认热点"]],
                       on=keys, how="outer", validate="one_to_one", indicator=True,
                       suffixes=("_qdc", "_old"))
    linked["entry_gap"] = linked.entry_qdc - linked.entry_old
    linked["d2_close_gap"] = linked.qdc_d2_close - linked.d2_close_old
    linked["q75_changed"] = linked.q75_veto.ne(linked.D1_Q75.astype("boolean"))
    linked["news_changed"] = linked.news_veto.ne(linked["D1买前公告否认热点"].astype("boolean"))
    linked[keys + ["_merge", "entry_qdc", "entry_old", "entry_gap", "qdc_d2_close", "d2_close_old",
                   "d2_close_gap", "q75_score", "q75_veto", "D1_Q75", "q75_changed", "news_veto",
                   "D1买前公告否认热点", "news_changed"]].to_csv(OUT / "source_trade_comparison.csv", index=False)

    gates_new = pd.read_csv(r105.OUT / "gates.csv")
    gates_old = pd.read_csv(r104.OUT / "original_gates_check.csv")
    gates = gates_new[["model_month", "paused"]].merge(
        gates_old[["model_month", "paused"]], on="model_month", suffixes=("_qdc", "_old"))
    gates = gates[gates.model_month.ge("2022-05")].copy()
    gates["changed"] = gates.paused_qdc.ne(gates.paused_old)
    gates.to_csv(OUT / "gate_comparison.csv", index=False)

    selection = json.loads((RISK / "selection_frozen.json").read_text())
    rule = next(x["rule"] for x in selection["candidates"] if x["id"] == "union_early_lr01")
    new[keys + ["d1_date", "d2_date", *rule["columns"], "q75_score", "q75_veto",
                "news_veto", "entry", "qdc_d2_close"]].to_csv(OUT / "risk_features_301.csv", index=False)
    model = joblib.load(RISK / rule["file"])
    training_panel = pd.read_pickle(RISK / "panel.pkl")
    training_panel["rescored_veto"] = model.predict_proba(training_panel[rule["columns"]])[:, 1] > rule["threshold"]
    verified = training_panel.merge(old[keys + ["D1_Q75"]], on=keys, validate="one_to_one")
    both = linked[linked._merge.eq("both")]
    summary = dict(old_signals=len(old), qdc_signals=len(new), common=len(both),
                   qdc_only=int(linked._merge.eq("left_only").sum()),
                   old_only=int(linked._merge.eq("right_only").sum()),
                   q75_common_flips=int(both.q75_changed.sum()),
                   news_common_flips=int(both.news_changed.sum()),
                   common_entry_diff_over_1c=int(both.entry_gap.abs().gt(.011).sum()),
                   common_d2_close_diff_over_1c=int(both.d2_close_gap.abs().gt(.011).sum()),
                   gate_flips=gates.loc[gates.changed, "model_month"].tolist(),
                   q75_original_flags_exact=bool(verified.rescored_veto.eq(verified.D1_Q75.eq(1)).all()),
                   qdc_d0_240_bars=int(new.completed_bars.eq(240).sum()),
                   qdc_d0_ohlc_match=int(new.d0_ohlc_match.sum()),
                   qdc_d2_240_bars=int(new.d2_bars.eq(240).sum()),
                   qdc_d2_ohlc_match=int(new.d2_ohlc_match.sum()),
                   historical_mv_exact=int(new.mv_source.eq("D0 historical value").sum()),
                   historical_mv_prior_shares=int(new.mv_source.eq("prior shares x D0 QDC close").sum()),
                   news_known=int(new.news_known.sum()),
                   news_veto_total=int(new.news_veto.sum()),
                   q75_veto_total=int(new.q75_veto.sum()),
                   retained=int((~(new.q75_veto | new.news_veto)).sum()),
                   retained_close_floor=int(new.loc[~(new.q75_veto | new.news_veto), "close_floor"].sum()),
                   retained_close_unverified=int(new.loc[~(new.q75_veto | new.news_veto), "close_unverified"].sum()),
                   retained_morning_unverified=int(new.loc[~(new.q75_veto | new.news_veto), "seven_unverified"].sum()),
                   upstream_sha256={"R105_signals": digest(r105.OUT / "signals.pkl"),
                                    "R106_risk_panel": digest(OUT / "risk_panel.pkl"),
                                    "R106_sell_panel": digest(OUT / "risk_sell_panel.pkl")})
    (OUT / "comparison_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
