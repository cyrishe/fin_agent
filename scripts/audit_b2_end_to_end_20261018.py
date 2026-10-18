"""Recheck the B2 monthly model, shape selection, picks and account ledger.

The report uses a fresh reconstruction under b2_independent_recheck_20261018.
Candidate rule metrics are training/validation diagnostics, not test returns.
"""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from scripts.experiment_limit_rebound_four_minutes_r24 import choose_rule
from scripts.experiment_limit_rebound_short_mixed_r12 import xmatrix
from scripts.experiment_limit_rebound_two_stage_r15 import classify, rank, window
import scripts.experiment_limit_rebound_shape_r19 as shape


ROOT = Path("outputs/stock_automl/b2_independent_recheck_20261018")
R18 = Path("outputs/stock_automl/limit_rebound_segments_r18_20261015")
R24 = Path("outputs/stock_automl/limit_rebound_four_minutes_r24_20261015")
OUT = ROOT / "audit"


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def ordinal(month: str) -> int:
    return (pd.Period(month, freq="M") - pd.Period("2024-01", freq="M")).n + 1


def pct(value):
    return None if value is None or not math.isfinite(float(value)) else 100 * float(value)


def display_rule(value: str) -> str:
    # Frozen internal aliases still say "五分"; this replay observes four complete bars.
    return value.replace("前五分", "前四K").replace("五分", "四K")


def same_json(left, right) -> bool:
    return json.dumps(left, sort_keys=True, ensure_ascii=False) == json.dumps(
        right, sort_keys=True, ensure_ascii=False
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    replay_path = ROOT / "continuous/replay.json"
    replay = json.loads(replay_path.read_text())
    months = [str(m) for m in pd.period_range("2025-05", "2026-10", freq="M")]
    assert [row["month"] for row in replay["monthly"]] == months
    assert len(replay["trades"]) == 226 and len(replay["daily"]) == 351
    decisions = pd.DataFrame(replay["candidates"])
    trades = pd.DataFrame(replay["trades"])
    panel = pd.read_pickle(R18 / "panel.pkl")
    new_rules = {r["month"]: r for r in json.loads((ROOT / "fourk/four_rules_frozen.json").read_text())}
    old_rules = {r["month"]: r for r in json.loads((R24 / "four_rules_frozen.json").read_text())}
    new_train = pd.read_pickle(ROOT / "fourk/top1_four.pkl")
    old_train = pd.read_pickle(R24 / "four_all_top3.pkl")
    rule_definitions = shape.rules()
    model_rows, rule_rows, chosen_rows, trade_rows = [], [], [], []
    max_score_diff = 0.0
    all_rank_exact = True

    for i, month in enumerate(months, 1):
        w = window(month)
        assert w["test"] == month
        model_path = R18 / month / "segments.joblib"
        bundle = joblib.load(model_path)
        expected_scores = pd.read_pickle(R18 / month / "segments_forecast_scores.pkl")
        test = panel[panel.d0_date.str.startswith(month)].copy()
        scored = rank(test, bundle["model"].predict_proba(xmatrix(test, bundle["features"]))[:, 1])
        actual_scores = scored[["symbol", "d0_date", "score", "d0_rank"]]
        pd.testing.assert_frame_equal(actual_scores.reset_index(drop=True), expected_scores.reset_index(drop=True),
                                      check_exact=False, atol=1e-14, rtol=0)
        max_score_diff = max(max_score_diff,
                             float(np.max(np.abs(actual_scores.score.to_numpy() - expected_scores.score.to_numpy()))))
        top3 = actual_scores[actual_scores.d0_rank.le(3)][["d0_date", "symbol", "d0_rank"]]
        observed = decisions[decisions.d0_date.str.startswith(month)][["d0_date", "symbol", "d0_rank"]]
        left = top3.sort_values(["d0_date", "d0_rank"]).reset_index(drop=True)
        right = observed.sort_values(["d0_date", "d0_rank"]).reset_index(drop=True)
        if not left.equals(right):
            all_rank_exact = False
            raise AssertionError(f"D0 Top3 differs from fresh daily ranking: {month}")

        daily_metrics = json.loads((R18 / month / "segments_metrics.json").read_text())
        assert daily_metrics["window"] == w
        valid_top3 = daily_metrics["valid"]["open"]["top3"]
        model_rows.append({
            "回测月序": i, "全局月序": ordinal(month),
            "日线训练月序": f"{ordinal(w['train'][0])}—{ordinal(w['train'][1])}",
            "日线验证月序": f"{ordinal(w['valid'][0])}—{ordinal(w['valid'][1])}",
            "日线训练样本": len(bundle["training_keys"]),
            "日线验证Top3样本": valid_top3["n"],
            "日线验证Top3命中%": pct(valid_top3["rate"]),
            "当月D0候选": len(test), "原Top3": len(top3),
            "模型哈希": sha(model_path),
        })

        frozen = new_rules[month] if month in new_rules else old_rules[month]
        source = new_train if month in new_rules else old_train
        source = source[(source.model_month == month) & source.d0_rank.eq(1)]
        train = source[source.part.eq("train") & source.complete].copy()
        valid = source[source.part.eq("valid") & source.complete].copy()
        rebuilt = choose_rule(train, valid, month, rule_definitions)
        assert same_json(rebuilt["selected"], frozen["selected"])
        assert rebuilt["eligible_train_rules"] == frozen["eligible_train_rules"]
        assert same_json(rebuilt["train_selected"], frozen["train_selected"])
        assert same_json(rebuilt["valid_selected"], frozen["valid_selected"])
        shortlist = {item["rule"]["id"]: item for item in frozen["shortlist"]}
        base = shape.metrics(train, rule_definitions[0])
        train_months = [str(m) for m in pd.period_range(*w["train"], freq="M")]
        chunks = [train_months[j:j + 2] for j in range(0, 6, 2)]
        eligible_count = 0
        for rule in rule_definitions:
            tr = shape.metrics(train, rule)
            va = shape.metrics(valid, rule)
            eligible = False
            if rule["id"] != "all":
                n = tr["known"]
                eligible = (n >= max(15, math.ceil(.4 * base["known"]))
                            and tr["hit"] >= base["hit"] + .03 - 1e-12
                            and tr["strategy"]["mean"] >= base["strategy"]["mean"] - 1e-12)
                if eligible:
                    blocks = [(shape.metrics(train[train.d0_date.str[:7].isin(c)], rule),
                               shape.metrics(train[train.d0_date.str[:7].isin(c)], rule_definitions[0]))
                              for c in chunks]
                    eligible = (min(a["known"] for a, _ in blocks) >= 3
                                and sum(a["hit"] >= b["hit"] for a, b in blocks) >= 2)
                eligible_count += int(eligible)
            short = shortlist.get(rule["id"])
            status = ("选中" if rule["id"] == frozen["selected"]["id"] else
                      "入围且通过验证" if short and short["accepted"] else
                      "入围但未通过验证" if short else
                      "训练通过、未入围" if eligible else
                      "基准规则" if rule["id"] == "all" else "训练未通过")
            rule_rows.append({
                "回测月序": i, "规则ID": rule["id"], "规则": display_rule(rule["text"]),
                "训练样本": tr["known"], "训练再板%": pct(tr["hit"]),
                "验证样本": va["known"], "验证再板%": pct(va["hit"]),
                "训练选规收益均%": pct(tr["strategy"]["mean"]),
                "验证选规收益均%": pct(va["strategy"]["mean"]),
                "训练D2毛均%": pct(tr["ret2"]["mean"]),
                "验证D2毛均%": pct(va["ret2"]["mean"]),
                "筛选结果": status,
            })
        assert eligible_count == frozen["eligible_train_rules"], month
        c = decisions[decisions.d0_date.str.startswith(month)]
        t = trades[trades.d0_date.str.startswith(month)]
        closed = t[t.exit_date.notna()]
        selected = frozen["selected"]
        chosen_rows.append({
            "回测月序": i, "规则ID": selected["id"], "规则": display_rule(selected["text"]),
            "形态训练月序": f"{ordinal(w['train'][0])}—{ordinal(w['train'][1])}",
            "形态验证月序": f"{ordinal(w['valid'][0])}—{ordinal(w['valid'][1])}",
            "规则训练样本": frozen["train_selected"]["known"],
            "规则训练再板%": pct(frozen["train_selected"]["hit"]),
            "规则验证样本": frozen["valid_selected"]["known"],
            "规则验证再板%": pct(frozen["valid_selected"]["hit"]),
            "训练通过规则数": frozen["eligible_train_rules"],
            "入围规则数": len(frozen["shortlist"]),
        })
        trade_rows.append({
            "回测月序": i, "D0候选日": c.d0_date.nunique(), "原Top3": len(c),
            "形态通过": int(c.shape_pass.sum()), "锁定": int(c.chosen.sum()),
            "买入": len(t), "已卖出": len(closed), "待卖": len(t) - len(closed),
            "已卖出毛均%": pct(((closed.sell / closed.buy) - 1).mean()) if len(closed) else None,
            "已卖出费后均%": pct(closed.return_.mean()) if len(closed) else None,
            "已卖出净盈亏": float(closed.pnl.sum()),
        })

    model = pd.DataFrame(model_rows)
    candidate = pd.DataFrame(rule_rows)
    chosen = pd.DataFrame(chosen_rows)
    result = pd.DataFrame(trade_rows)
    research_months = [str(m) for m in pd.period_range("2025-09", "2026-10", freq="M")]
    daily_alternatives = []
    for variant, label in [("old", "旧35因子"), ("aligned", "板前对齐35因子"),
                           ("segments", "完整分段51因子")]:
        forecast = pd.concat([pd.read_pickle(R18 / m / f"{variant}_forecast_scores.pkl")
                              for m in research_months], ignore_index=True)
        scored = forecast.merge(panel, on=["symbol", "d0_date"], validate="one_to_one")
        stats = classify(scored)["open"]
        daily_alternatives.append({
            "方案": label, "研究月数": len(research_months),
            "Top1样本": stats["top1"]["n"], "Top1再板%": pct(stats["top1"]["rate"]),
            "Top3样本": stats["top3"]["n"], "Top3再板%": pct(stats["top3"]["rate"]),
            "Top5样本": stats["top5"]["n"], "Top5再板%": pct(stats["top5"]["rate"]),
            "B2采用": "是" if variant == "segments" else "否",
            "B2交易回放": "当前B2" if variant == "segments" else "未按B2交易规则复跑",
        })
    daily_alternatives = pd.DataFrame(daily_alternatives)
    assert len(model) == len(chosen) == len(result) == 18
    assert len(candidate) == 18 * 79
    assert result["买入"].sum() == 226 and result["已卖出"].sum() == 225
    for name, frame in [("日线方案对照", daily_alternatives), ("日线逐月训练", model), ("四K规则候选", candidate),
                        ("四K逐月入选", chosen), ("模型月交易结果", result)]:
        frame.to_csv(OUT / f"{name}.csv", index=False, encoding="utf-8-sig")

    previous = Path("outputs/stock_automl/b2_continuous_20261018/replay.json")
    assert sha(replay_path) == sha(previous)
    receipt = {
        "status": "PASS", "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "scope": "2025-05 through 2026-10-14, original B2 four-K rules and D2 close exit",
        "complete_test_months": 17, "partial_test_months": 1,
        "daily_models_checked": 18, "daily_model_scores_max_abs_diff": max_score_diff,
        "daily_top3_exact": all_rank_exact,
        "shape_rules_per_month": len(rule_definitions), "candidate_rule_rows": len(candidate),
        "selected_rules_recomputed_exact": 18,
        "entries": len(trades), "closed": int(trades.exit_date.notna().sum()),
        "account_days": len(replay["daily"]),
        "replay_sha256": sha(replay_path), "same_as_previous_replay": True,
        "initial_cash": replay["receipt"]["summary"]["initial"],
        "ending_nav": replay["receipt"]["summary"]["final_nav"],
        "total_return": replay["receipt"]["summary"]["total_return"],
        "max_drawdown": replay["receipt"]["summary"]["max_drawdown"],
        "limit": "Old Sep-2025 onward minute inputs are frozen first-ten-minute evidence; May-Aug-2025 was reread from minute archive. Historical strategy/model design was researched on these years, not an untouched blind test.",
    }
    (OUT / "audit_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
