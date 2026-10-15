"""Freeze the exact R18/R24 assets into a portable, checked B2 runtime seed."""
from __future__ import annotations
import json
import shutil
from pathlib import Path
import joblib
import pandas as pd
from scripts.automl_b2 import definition, digest
from scripts.experiment_limit_rebound_segments_r18 import OUT as R18
from scripts.experiment_limit_rebound_four_minutes_r24 import OUT as R24
from scripts.experiment_limit_rebound_shape_top3_r20 import OUT as R20
from scripts.experiment_limit_rebound_short_mixed_r12 import dump, sha


OUT = Path("outputs/stock_automl/b2_assets_20261015")


def minute_exclusions():
    f = pd.read_pickle(R20 / "enriched_top3.pkl")
    bad = f[f.audit_ok.eq(False)][["symbol", "d1_date", "reason"]].dropna().drop_duplicates()
    value = {f"{r.d1_date}_{r.symbol}": r.reason for r in bad.itertuples()}
    path = OUT / "minute_history" / "exclusions.json"
    path.parent.mkdir(exist_ok=True)
    dump(path, value)
    return path


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / "SEED_MANIFEST.json").exists():
        old = json.loads((OUT / "SEED_MANIFEST.json").read_text())
        for name, hashed in old["files"].items():
            assert sha(OUT / name) == hashed, name
        # Additional provenance for already excluded historical rows; existing
        # model/sample/raw files are unchanged. New monthly data are not excused.
        if not (OUT / "minute_history/exclusions.json").exists():
            added = minute_exclusions()
            old["files"][str(added.relative_to(OUT))] = sha(added)
            old["metadata_extension"] = "Original R20 failed-minute exclusions, preserving original model and rule samples"
            dump(OUT / "SEED_MANIFEST.json", old)
        print("Existing immutable seed verified", len(old["files"])); return
    panel = pd.read_pickle(R18 / "panel.pkl").set_index(["symbol", "d0_date"])
    chosen = {x["month"]: x for x in json.loads((R24 / "four_rules_frozen.json").read_text())}
    for source in sorted(R18.glob("20??-??/segments.joblib")):
        month = source.parent.name; target = OUT / "months" / month; target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target / "model.joblib")
        bundle = joblib.load(source)
        keys = pd.MultiIndex.from_tuples([(x["symbol"], x["d0_date"]) for x in bundle["training_keys"]])
        train = panel.loc[keys].reset_index()
        counts = train.label_3.value_counts()
        train["sample_weight"] = train.label_3.map({k: len(train) / (2 * n) for k, n in counts.items()})
        train.to_pickle(target / "training.pkl")
        names = ["model.joblib", "training.pkl", "rule.json"]
        for part in ("forecast", "valid", "oof"):
            path = source.parent / f"segments_{part}_scores.pkl"
            if path.exists():
                name = f"{part}_scores.pkl"; shutil.copy2(path, target / name); names.append(name)
        dump(target / "rule.json", chosen.get(month, {"month": month, "selected": {"id": "all", "atoms": [], "text": "仅供历史折外评分", "family": "none"}, "oof_only": True}))
        meta = {"month": month, "source": str(source), "source_sha256": sha(source),
                "source_metrics": json.loads((source.parent / "segments_metrics.json").read_text()),
                "training_rows": len(train), "files": {name: sha(target / name) for name in names}}
        dump(target / "manifest.json", meta)
    columns = "trade_date stk_code preclose open high low close volume amount turn_ratio is_limit_price".split()
    sources = json.loads((R18 / "source_manifest.json").read_text())["raw_sources"]
    raw = []
    for item in sources:
        path = Path(item["path"]); assert sha(path) == item["sha256"]
        raw.append(pd.read_csv(path, usecols=columns, dtype={"stk_code": str, "trade_date": str}))
    ep = Path("outputs/stock_automl/limit_rebound_short_mixed_r12_20261014/price_months/price_2026-10-14_extension.csv.gz")
    raw.append(pd.read_csv(ep, usecols=columns, dtype={"stk_code": str, "trade_date": str}))
    daily = pd.concat(raw, ignore_index=True).drop_duplicates(["stk_code", "trade_date"], keep="last")
    joblib.dump(daily, OUT / "daily.joblib", compress=3)
    shutil.copy2(R20 / "historical_status.csv.gz", OUT / "status_seed.csv.gz")
    minute = OUT / "minute_history"; minute.mkdir(exist_ok=True)
    for path in sorted((R20 / "minute_days").glob("*_raw.pkl")):
        f = pd.read_pickle(path)
        # Preserve one previously reconciled source per stock/day, matching R24.
        wanted = pd.DataFrame(json.loads(path.with_name(path.name.replace("_raw.pkl", ".json")).read_text()))
        for row in wanted[wanted.audit_ok.eq(True)].itertuples():
            b = f[f.symbol.eq(row.symbol) & f.source.eq(row.minute_source)].sort_values("time")
            assert len(b) == 240
            b[["time", "open", "high", "low", "close", "volume"]].to_pickle(minute / f"{row.d1_date}_{row.symbol}.pkl")
    minute_exclusions()
    ref = OUT / "reference"; ref.mkdir(exist_ok=True)
    for root, name in [(R18, "panel.pkl"), (R24, "four_all_top3.pkl"), (R24, "first10_evidence.pkl"),
                       (R24, "policy_picks.pkl"), (R24, "accounts.json"), (R24, "ledger.pkl"), (R24, "four_rules_frozen.json")]:
        shutil.copy2(root / name, ref / name)
    dump(OUT / "definition.json", definition())
    files = {str(p.relative_to(OUT)): sha(p) for p in sorted(OUT.rglob("*")) if p.is_file()}
    dump(OUT / "SEED_MANIFEST.json", {"definition_sha256": digest(definition()), "files": files,
        "daily_rows": len(daily), "daily_through": str(daily.trade_date.max()), "source_prices": sources,
        "endpoint_sha256": sha(ep), "status": "local_frozen_seed_cloud_pending"})
    print(json.dumps({"seed": str(OUT), "files": len(files), "daily_rows": len(daily)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
