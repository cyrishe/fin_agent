"""Score frozen high-label models; evaluate their picks by 10-bar open/close labels."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from scripts.benchmark_automl_1440_inference import FEATURES
from scripts.experiment_automl_close9_models import DATA
from scripts.experiment_automl_matrix_10bar import OUT as MATRIX, target_class


OUT = Path("docs/stock_automl_runs/20261009_high_train_open_close")
VALIDATION = {"开盘价": "max_open", "收盘价": "max_close"}


def score(model, rows):
    result = rows.copy()
    result["score"] = model.predict_proba(result[list(FEATURES)])[:, 1]
    result = result.sort_values(["signal_date", "score", "symbol6"],
                                ascending=[True, False, True])
    result["rank"] = result.groupby("signal_date").cumcount() + 1
    return result


def count(scored, label, n, scope, training, structure, validation):
    picks = scored[scored["rank"].le(n)]
    cutoffs = [int(day.score.ge(day.score.iloc[n-1]).sum())
               for _, day in scored.groupby("signal_date")]
    y = scored[label].eq(1).astype(int)
    return {"训练范围": training, "因子": "7因子", "模型结构": structure,
            "训练标签": "最高价", "验证标签": validation, "评价日期": scope,
            "交易日数": scored.signal_date.nunique(), "候选股票数": len(scored),
            "每日取前": n, "选中股票数": len(picks),
            "超过1%": int(picks[label].eq(1).sum()),
            "0.5%至1%": int(picks[label].eq(0).sum()),
            "低于0.5%": int(picks[label].eq(-1).sum()),
            "候选池超过1%比例": float(y.mean()),
            "前列超过1%比例": float(picks[label].eq(1).mean()),
            "Lift": float(picks[label].eq(1).mean()/y.mean()),
            "全候选AUC": float(roc_auc_score(y, scored.score)),
            "临界名次最多并列数": max(cutoffs)}


def main():
    manifest = json.loads((MATRIX / "manifest.json").read_text())
    rows = pd.read_csv(DATA, dtype={"symbol6": str})
    extrema = pd.read_csv(MATRIX / "tenbar_price_extrema.csv.gz", dtype={"symbol6": str})
    rows = rows.merge(extrema, on=["next_date", "symbol6"], validate="one_to_one")
    if len(rows) != 14292 or rows.signal_date.nunique() != 40:
        raise ValueError("Exact candidate set changed")
    for label, price in VALIDATION.items():
        rows[f"return_{price}"] = rows[price]/rows.entry_1440 - 1
        rows[f"label_{price}"] = target_class(rows[f"return_{price}"])
    rows["return_max_high"] = rows.max_high/rows.entry_1440 - 1
    rows["label_max_high"] = target_class(rows.return_max_high)
    previous = pd.read_csv(MATRIX / "matrix_summary.csv")
    summary, daily, detail, checked_models = [], [], [], []
    for item in manifest["models"]:
        if item["price"] != "最高价":
            continue
        model_file = Path(item["path"])
        actual_hash = hashlib.sha256(model_file.read_bytes()).hexdigest()
        if actual_hash != item["sha256"]:
            raise ValueError(f"Frozen model changed: {model_file}")
        checked_models.append({"name": item["name"], "sha256": actual_hash})
        model = joblib.load(model_file)
        training = item["training"]
        structure = item["model"]
        train_dates = set(manifest["training_dates"][training])
        outside = rows[~rows.signal_date.isin(train_dates)]
        common = outside[outside.signal_date.isin(manifest["common_out_of_training_dates"])]
        if outside.signal_date.nunique() != 20 or common.signal_date.nunique() != 9:
            raise ValueError("Training/test date split changed")
        for scope, population in (("各自未训练20日", outside), ("共同未训练9日", common)):
            scored = score(model, population)
            for validation, price in VALIDATION.items():
                label_col = f"label_{price}"
                for n in (2, 5):
                    summary.append(count(scored, label_col, n, scope,
                                         training, structure, validation))
            if scope != "各自未训练20日":
                continue
            for day, group in scored.groupby("signal_date"):
                best2 = group.head(2)
                best5 = group.head(5)
                record = {"训练范围": training, "因子": "7因子", "模型结构": structure,
                          "训练标签": "最高价", "信号日": day,
                          "次交易日": group.next_date.iloc[0], "候选股票数": len(group)}
                for validation, price in VALIDATION.items():
                    label_col = f"label_{price}"
                    record.update({f"候选池{validation}超过1%": int(group[label_col].eq(1).sum()),
                                   f"前二{validation}超过1%": int(best2[label_col].eq(1).sum()),
                                   f"前二{validation}低于0.5%": int(best2[label_col].eq(-1).sum()),
                                   f"前五{validation}超过1%": int(best5[label_col].eq(1).sum()),
                                   f"前五{validation}低于0.5%": int(best5[label_col].eq(-1).sum())})
                daily.append(record)
            chosen = scored[scored["rank"].le(5)].copy()
            chosen["训练范围"] = training
            chosen["模型结构"] = structure
            chosen["前二"] = chosen["rank"].le(2).astype(int)
            chosen["前三"] = chosen["rank"].le(3).astype(int)
            chosen["前五"] = 1
            detail.append(chosen[["训练范围", "模型结构", "signal_date", "next_date", "rank",
                                  "前二", "前三", "前五", "symbol6", "name", "score",
                                  "entry_1440", "max_open", "max_close", "max_high",
                                  "return_max_open", "return_max_close", "return_max_high",
                                  "label_max_open", "label_max_close", "label_max_high", *FEATURES]])
    if len(checked_models) != 4:
        raise ValueError("Expected four frozen highest-price models")
    detail_frame = pd.concat(detail, ignore_index=True)
    archived = pd.read_csv(MATRIX / "daily_top5.csv", dtype={"symbol6": str})
    archived = archived[archived["卖价字段"].eq("最高价")]
    aligned = detail_frame.merge(
        archived[["训练范围", "模型结构", "signal_date", "symbol6", "rank", "score"]],
        on=["训练范围", "模型结构", "signal_date", "symbol6", "rank"],
        how="outer", indicator=True, suffixes=("_now", "_saved"),
        validate="one_to_one")
    if (len(aligned) != 400 or not aligned._merge.eq("both").all() or
            (aligned.score_now-aligned.score_saved).abs().max() > 1e-12):
        raise ValueError("Frozen high-label model picks changed during validation")
    result = pd.DataFrame(summary)
    # The prior matched-label rows are a read-only reference, on exactly these dates.
    matched = previous[(previous["卖价字段"].isin(VALIDATION)) &
                       (previous["评价日期"].isin(["各自未训练20日", "共同未训练9日"]))]
    paired = result.merge(matched[["训练范围", "模型结构", "卖价字段", "评价日期", "每日取前",
                                  "超过1%", "低于0.5%", "Lift"]],
                          left_on=["训练范围", "模型结构", "验证标签", "评价日期", "每日取前"],
                          right_on=["训练范围", "模型结构", "卖价字段", "评价日期", "每日取前"],
                          validate="one_to_one", suffixes=("_最高价训练", "_同字段训练"))
    paired = paired.drop(columns=["卖价字段"])
    OUT.mkdir(parents=True, exist_ok=True)
    result.to_csv(OUT / "high_train_open_close_summary.csv", index=False)
    pd.DataFrame(daily).to_csv(OUT / "daily_results.csv", index=False)
    detail_frame.to_csv(OUT / "daily_top5.csv", index=False)
    paired.to_csv(OUT / "same_dates_training_label_comparison.csv", index=False)
    report = {"definition": "Train four frozen seven-factor models with highest price among T+1 09:31–09:40 1m bars; evaluate the same selected stocks separately using maximum open or maximum close against T 14:40 price.",
              "model_files": checked_models,
              "training_dates": manifest["training_dates"],
              "common_out_of_training_dates": manifest["common_out_of_training_dates"],
              "source_sha256": {"candidate_set": hashlib.sha256(DATA.read_bytes()).hexdigest(),
                                "tenbar_prices": hashlib.sha256((MATRIX/"tenbar_price_extrema.csv.gz").read_bytes()).hexdigest()}}
    (OUT / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    print(result[result["每日取前"].eq(2)].to_string(index=False))


if __name__ == "__main__":
    main()
