from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import itertools
import json
from pathlib import Path
import platform
import random
import subprocess
import time
import uuid

import joblib
import numpy as np
import pandas as pd
import sklearn

from .evaluation import backtest_predictions, prediction_metrics
from .features import build_panel, feature_columns, labeled_panel, sample_mask
from .learning import fit_model, temporal_folds


def write_json(path, value):
    def encode(x):
        if isinstance(x, (np.integer, np.floating)):
            return x.item()
        if isinstance(x, (pd.Timestamp, datetime)):
            return x.isoformat()
        raise TypeError(type(x).__name__)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=encode, allow_nan=False))


def candidate_pool(spec, available_columns):
    samplers = [s for s in spec.samplers if s != "news" or "news_count_7d" in available_columns]
    pool = []
    for h, task, model, sampler, features, setting in itertools.product(
            spec.horizons, spec.tasks, spec.models, samplers, spec.feature_sets, range(2)):
        c = {"horizon": h, "task": task, "model": model, "sampler": sampler,
             "features": features, "depth": (3, 6)[setting], "strength": (.3, 3.0)[setting]}
        c["id"] = hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()[:12]
        pool.append(c)
    random.Random(spec.seed).shuffle(pool)
    # Seed broad coverage before arbitrary combinations; reproducible random exploration follows.
    first, seen = [], set()
    for c in pool:
        key = (c["horizon"], c["task"], c["model"])
        if key not in seen:
            first.append(c)
            seen.add(key)
    first_ids = {c["id"] for c in first}
    return first + [c for c in pool if c["id"] not in first_ids]


def _baseline(train, task, spec):
    return float((train.forward_return > spec.target_return).mean()) if task == "classification" else float(train.forward_return.mean())


def constraint_checks(folds, spec):
    metrics = [f["prediction"] for f in folds]
    return {
        "enough_signals": sum(m["signal_count"] for m in metrics) >= spec.min_signals,
        "each_fold_win_rate": all(m["signal_win_rate_after_cost"] is not None
                                  and m["signal_win_rate_after_cost"] >= spec.min_win_rate for m in metrics),
        "drawdown_within_limit": all(abs(f["portfolio"]["max_drawdown"]) <= spec.max_drawdown for f in folds),
        "each_fold_beats_constant": all(m["skill_vs_constant"] > 0 for m in metrics),
    }


def assess_candidate(data, panel, candidate, spec, extra_columns):
    columns = feature_columns(data, candidate["features"], extra_columns)
    folds = []
    for train, validation in temporal_folds(data, spec.folds):
        train = train[sample_mask(train, candidate["sampler"], spec)]
        validation = validation[sample_mask(validation, candidate["sampler"], spec)]
        if validation.empty:
            raise ValueError("no samples matching strategy in validation fold")
        model = fit_model(train, candidate, columns, spec)
        scored = validation.copy()
        scored["prediction"] = model.predict(scored)
        metrics = prediction_metrics(scored, candidate["task"], spec, _baseline(train, candidate["task"], spec))
        bt = backtest_predictions(scored, panel, candidate, spec)
        folds.append({"train_end": train.label_end.max(), "validation_start": validation.date.min(),
                      "validation_end": validation.label_end.max(), "prediction": metrics,
                      "portfolio": bt["metrics"]})
    if len(folds) != spec.folds:
        raise ValueError("insufficient chronological folds")
    skills = [f["prediction"]["skill_vs_constant"] for f in folds]
    signals = sum(f["prediction"]["signal_count"] for f in folds)
    checks = constraint_checks(folds, spec)
    eligible = all(checks.values())
    return {"candidate": candidate, "columns": columns, "folds": folds,
            "meets_constraints": bool(eligible), "constraint_checks": checks, "score": float(np.mean(skills) - np.std(skills)),
            "signal_count": signals}


def run_research(spec, daily, events=(), *, source=None, output_root="outputs/stock_automl", advisor=None, progress=print):
    started = time.monotonic()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    root = Path(output_root) / run_id
    root.mkdir(parents=True, exist_ok=False)
    write_json(root / "spec.json", spec.to_dict())
    # Code is snapshotted, including uncommitted implementation, so a Git HEAD alone is never evidence.
    implementation = sorted(Path(__file__).parent.glob("*.py")) + sorted(Path(__file__).with_name("prompts").glob("*.md"))
    implementation += sorted(Path(__file__).parents[2].joinpath("backtest").glob("*.py"))
    snapshot = root / "implementation"
    snapshot.mkdir()
    for file in implementation:
        destination = snapshot / file.relative_to(Path(__file__).parents[2])
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(file.read_bytes())
    code_hash = hashlib.sha256(b"".join(p.read_bytes() for p in implementation)).hexdigest()
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip())
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = None, None
    panel = build_panel(daily, events)
    extra = sorted({c for event in events for c in event.columns if c not in ("symbol", "available_at", "industry")})
    dates = np.array(sorted(panel.date.unique()))
    if len(dates) < 140:
        raise ValueError("at least 140 trading sessions are required")
    test_start = pd.Timestamp(dates[int(len(dates) * (1 - spec.test_fraction))])
    symbols = sorted(panel.symbol.unique())
    if len(symbols) < 5:
        raise ValueError("at least five companies are required")
    shuffled = symbols.copy()
    random.Random(spec.seed).shuffle(shuffled)
    heldout = sorted(shuffled[:max(1, int(len(shuffled) * spec.company_holdout_fraction))])
    fingerprint = hashlib.sha256(pd.util.hash_pandas_object(panel, index=True).values.tobytes()).hexdigest()
    manifest = {"run_id": run_id, "git_commit": commit, "working_tree_dirty": dirty,
                "implementation_sha256": code_hash, "panel_sha256": fingerprint,
                "python": platform.python_version(), "pandas": pd.__version__, "sklearn": sklearn.__version__,
                "created_at": datetime.now(timezone.utc).isoformat(), "test_start": test_start,
                "heldout_companies": heldout, "source": source or {"source": "provided_frame"},
                "rows": len(panel), "sessions": len(dates), "repeated_holdout_warning":
                "同一数据反复运行会污染盲测；后续研究须指定未揭盲的新时间窗口。"}
    write_json(root / "manifest.json", manifest)
    panel.to_pickle(root / "panel.pkl")
    candidates = candidate_pool(spec, panel.columns)
    write_json(root / "candidates.json", candidates)
    lookup = {c["id"]: c for c in candidates}
    results, attempted, failures, planning = [], set(), [], []
    labels = {h: labeled_panel(panel, h) for h in spec.horizons}
    common_end = min(frame.date.max() for frame in labels.values())
    if pd.isna(common_end) or common_end < test_start:
        raise ValueError("no shared out-of-time evaluation window")
    for round_index in range(spec.rounds):
        remaining = [c for c in candidates if c["id"] not in attempted]
        budget = min(len(remaining), (spec.max_trials - len(attempted) + spec.rounds - round_index - 1) // (spec.rounds - round_index))
        if budget <= 0:
            break
        # Later deterministic rounds refine development winners while keeping exploratory candidates.
        if results:
            best = max(results, key=lambda r: (r["meets_constraints"], r["score"]))["candidate"]
            remaining.sort(key=lambda c: sum(c[k] != best[k] for k in ("model", "task", "horizon", "sampler", "features")))
            exploit = remaining[:max(1, budget // 2)]
            rng = random.Random(spec.seed + round_index)
            explore = remaining[len(exploit):]
            rng.shuffle(explore)
            remaining = exploit + explore
        picks = [c["id"] for c in remaining[:budget]]
        if advisor is not None:
            try:
                proposed, analysis = advisor.plan(spec, remaining[:min(120, len(remaining))],
                    [{"candidate": r["candidate"], "score": r["score"], "meets_constraints": r["meets_constraints"],
                      "signal_count": r["signal_count"], "constraint_checks": r["constraint_checks"]} for r in results], budget=budget)
                picks = list(dict.fromkeys(proposed + picks))[:budget]
                planning.append({"round": round_index + 1, "analysis": analysis, "candidate_ids": picks})
            except Exception as exc:
                planning.append({"round": round_index + 1, "fallback": type(exc).__name__,
                                 "analysis": "LLM规划未完成，使用已保存约束下的确定性探索。"})
        elif spec.objective != "探索具有样本外稳定性的股票量价规律":
            planning.append({"round": round_index + 1, "analysis": "未启用LLM，自然语言目标仅记录；执行约束来自spec字段。"})
        for candidate_id in picks:
            candidate = lookup[candidate_id]
            attempted.add(candidate_id)
            progress(f"trial {len(attempted)}/{spec.max_trials}: {candidate['model']} {candidate['task']} h={candidate['horizon']} {candidate['sampler']}")
            data = labels[candidate["horizon"]]
            development = data[(~data.symbol.isin(heldout)) & (data.label_end < test_start)]
            try:
                result = assess_candidate(development, panel[~panel.symbol.isin(heldout)], candidate, spec, extra)
                results.append(result)
            except ValueError as exc:
                failures.append({"candidate": candidate, "reason": str(exc)[:300]})
            write_json(root / "development.json", {"results": results, "failures": failures, "planning": planning})
    if not results:
        raise ValueError(f"no evaluable candidates; inspect {root / 'development.json'}")
    results.sort(key=lambda r: (r["meets_constraints"], r["score"]), reverse=True)
    winner = results[0]
    # Commit the decision before revealing either test slice. No search uses these results.
    write_json(root / "selection.json", winner)
    c = winner["candidate"]
    data = labels[c["horizon"]]
    development = data[(~data.symbol.isin(heldout)) & (data.label_end < test_start)]
    development = development[sample_mask(development, c["sampler"], spec)]
    model = fit_model(development, c, winner["columns"], spec)
    joblib.dump(model, root / "model.joblib")
    blind = data[data.date.between(test_start, common_end)]
    reports = {}
    for name, subset in (("new_period", blind[~blind.symbol.isin(heldout)]),
                         ("new_companies_and_period", blind[blind.symbol.isin(heldout)])):
        market_subset = subset.copy()
        subset = subset[sample_mask(subset, c["sampler"], spec)].copy()
        if subset.empty:
            reports[name] = {"unavailable": "no samples matching selection in heldout slice"}
            continue
        subset["prediction"] = model.predict(subset)
        subset.to_csv(root / f"{name}_predictions.csv", index=False)
        metrics = prediction_metrics(subset, c["task"], spec, _baseline(development, c["task"], spec))
        bt = backtest_predictions(subset, panel, c, spec)
        # Same dates and company universe, but without strategy selection or ranking.
        benchmark_rows = market_subset[market_subset.date.between(subset.date.min(), subset.date.max())].copy()
        benchmark_rows["prediction"] = 0.0
        benchmark = backtest_predictions(benchmark_rows, panel, c, spec, benchmark=True)
        write_json(root / f"{name}_backtest.json", bt)
        write_json(root / f"{name}_benchmark.json", benchmark)
        reports[name] = {"prediction": metrics, "portfolio": bt["metrics"], "benchmark": benchmark["metrics"],
                         "excess_total_return": bt["metrics"]["total_return"] - benchmark["metrics"]["total_return"],
                         "execution_issue_count": len(bt["issues"])}
    report = {"run_id": run_id, "objective": spec.objective, "selected": c,
              "development_constraints_met": winner["meets_constraints"],
              "development_constraint_checks": winner["constraint_checks"],
              "decision_policy": {"probability_threshold": spec.probability_threshold,
                                  "regression_threshold": spec.regression_threshold, "target_return": spec.target_return,
                                  "top_k": spec.top_k, "min_signals": spec.min_signals,
                                  "min_win_rate": spec.min_win_rate, "max_drawdown": spec.max_drawdown},
              "attempted_trials": len(attempted), "successful_trials": len(results),
              "evaluation": reports, "elapsed_seconds": round(time.monotonic() - started, 2),
              "limitations": [
                  "研究回测使用复权价格及1份单位，未完整模拟A股100股整手、开盘涨跌停排队与停复牌可成交性；不作为实盘回报。",
                  "交易成本为任务假设，不自动推断历史税费制度；复权数据可能被供应商修订。",
                  "信号胜率是重叠持有期样本统计，不能视为独立已成交交易胜率；组合收益另由逐日现金账本计算。",
                  "horizon定义：收盘后信号，下一交易日开盘买入，持有h个交易日后开盘退出；不是期间触及目标价概率。",
                  "随机起始股票池可含后来退市股票，但退市清算和历史数据库完整性尚未认证。",
                  "所有盲测只评估开发集选定的一名候选；本轮报告不触发自动再训练或交易。",
              ] + (source or {}).get("warnings", [])}
    write_json(root / "report.json", report)
    review = "未启用大模型评审；数值证据已生成。"
    if advisor is not None:
        progress("reviewing heldout metrics with configured LLM")
        try:
            review = advisor.review(report)
        except Exception as exc:
            review = f"大模型评审未完成（{type(exc).__name__}）；保留完整数值证据，可独立重试评审。"
    (root / "review.md").write_text(review)
    lines = ["# 股票 AutoML 研究报告", "", spec.objective, "", f"候选：`{c}`", "",
             f"开发集约束满足：{winner['meets_constraints']}。实验：{len(results)}/{len(attempted)} 成功。", "",
             "| 盲测切片 | 信号数 | 信号胜率（扣成本） | 组合收益 | 同池基准 | 最大回撤 |", "|---|---:|---:|---:|---:|---:|"]
    for name, r in reports.items():
        if "prediction" in r:
            p, b = r["prediction"], r["portfolio"]
            win = f"{p['signal_win_rate_after_cost']:.1%}" if p['signal_win_rate_after_cost'] is not None else "无信号"
            lines.append(f"| {name} | {p['signal_count']} | {win} | {b['total_return']:.2%} | {r['benchmark']['total_return']:.2%} | {b['max_drawdown']:.2%} |")
        else:
            lines.append(f"| {name} | 无匹配样本 | — | — | — | — |")
    lines += ["", "## 评审", "", review, "", "## 边界", ""] + [f"- {s}" for s in report["limitations"]]
    (root / "report.md").write_text("\n".join(lines) + "\n")
    progress(f"saved: {root.resolve()}")
    return root, report
