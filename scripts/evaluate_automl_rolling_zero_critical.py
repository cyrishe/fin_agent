"""Recount frozen rolling picks with a zero-return critical-error threshold."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from scripts.experiment_automl_close9_models import DATA
from scripts.experiment_automl_matrix_10bar import PRICE_COLUMNS


ROOT = Path("docs/stock_automl_runs/20261009_rolling_windows")
PICKS = ROOT / "daily_top5.csv"
SUMMARY = ROOT / "summary.csv"
MANIFEST = ROOT / "manifest.json"
OUT = ROOT / "zero_critical_review.csv"
PRICES = Path("docs/stock_automl_runs/20261009_tenbar_matrix/tenbar_price_extrema.csv.gz")


def main():
    picks = pd.read_csv(PICKS, dtype={"symbol6": str})
    previous = pd.read_csv(SUMMARY)
    common = set(json.loads(MANIFEST.read_text())["common_test_dates"])
    candidates = pd.read_csv(DATA, dtype={"symbol6": str}).merge(
        pd.read_csv(PRICES, dtype={"symbol6": str}),
        on=["next_date", "symbol6"], validate="one_to_one")
    if len(picks) != 2250 or not picks.target_return.notna().all():
        raise ValueError("Rolling picks changed")
    if not (picks.label.eq(1) == picks.target_return.gt(.01)).all():
        raise ValueError("Saved positive labels disagree with returns")
    if not (picks.label.eq(-1) == picks.target_return.lt(.005)).all():
        raise ValueError("Saved critical labels disagree with returns")

    rows = []
    for scope, frame in (("各窗口全部折外日期", picks),
                         ("共同20个折外日期", picks[picks.signal_date.isin(common)])):
        for (window, model, price), group in frame.groupby(["window", "model", "price"],
                                                           sort=False):
            for top_n in (2, 5):
                chosen = group[group["rank"].le(top_n)]
                pool = candidates[candidates.signal_date.isin(group.signal_date.unique())]
                pool_return = pool[PRICE_COLUMNS[price]]/pool.entry_1440-1
                pool_pass_rate = float(pool_return.ge(0).mean())
                count = len(chosen)
                hit = int(chosen.target_return.gt(.01).sum())
                critical = int(chosen.target_return.lt(0).sum())
                neutral = count-hit-critical
                old = previous[(previous["训练窗口"].eq(window)) &
                               (previous["模型"].eq(model)) &
                               (previous["训练及验证价格"].eq(price)) &
                               (previous["评价范围"].eq(scope)) &
                               (previous["每日取前"].eq(top_n))].iloc[0]
                if count != old["选出总数"] or hit != old["达标"]:
                    raise ValueError("Selection or success count changed")
                rows.append({"训练窗口": window, "模型": model, "价格字段": price,
                             "评价范围": scope, "测试日数": group.signal_date.nunique(),
                             "每日取前": top_n, "总数": count, "达标_大于1%": hit,
                             "中性_0%至1%": neutral, "严重错误_低于0%": critical,
                             "通过_非负": count-critical, "通过率_非负": (count-critical)/count,
                             "候选池通过率_非负": pool_pass_rate,
                             "通过Lift_非负": ((count-critical)/count)/pool_pass_rate,
                             "旧严重错误_低于0.5%": int(old["严重错误"]),
                             "旧通过率_至少0.5%": (count-old["严重错误"])/count,
                             "达标Lift_未变": old["Lift"]})
    result = pd.DataFrame(rows)
    if len(result) != 72 or not ((result["达标_大于1%"]+result["中性_0%至1%"]+
                                  result["严重错误_低于0%"] ) == result["总数"]).all():
        raise ValueError("Recount inconsistent")
    result.to_csv(OUT, index=False)
    (ROOT / "zero_critical_review_manifest.json").write_text(json.dumps({
        "method": "No retraining or reranking. Success >1%, new critical <0%, neutral 0%-1% inclusive at zero and one percent.",
        "source_hashes": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in (PICKS, SUMMARY, MANIFEST, DATA, PRICES)},
    }, ensure_ascii=False, indent=2)+"\n")
    print(result[(result["评价范围"]=="共同20个折外日期") &
                 (result["每日取前"]==2)].to_string(index=False))


if __name__ == "__main__":
    main()
