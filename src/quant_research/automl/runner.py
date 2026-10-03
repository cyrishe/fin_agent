from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import itertools
import json
from pathlib import Path
import platform
import os
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
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=encode, allow_nan=False))
    os.replace(temporary, path)


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


def assess_candidate(data, panel, candidate, spec, extra_columns, check_cancel=lambda: None):
    columns = feature_columns(data, candidate["features"], extra_columns)
    folds = []
    for train, validation in temporal_folds(data, spec.folds):
        check_cancel()
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
                      "validation_end": validation.label_end.max(), "training": model.training_counts,
                      "validation_rows": len(validation), "prediction": metrics,
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


def _implementation_files():
    files = sorted(Path(__file__).parent.glob("*.py")) + sorted(Path(__file__).with_name("prompts").glob("*.md"))
    return files + sorted(Path(__file__).parents[2].joinpath("backtest").glob("*.py"))


def _implementation_hash(files):
    return hashlib.sha256(b"".join(p.read_bytes() for p in files)).hexdigest()


def _load_json(path):
    return json.loads(Path(path).read_text())


def render_report(report, review):
    c = report["selected"]
    lines = ["# 股票 AutoML 研究报告", "", report["objective"], "", f"候选：`{c}`", "",
             f"开发集约束满足：{report['development_constraints_met']}。实验：{report['successful_trials']}/{report['attempted_trials']} 成功。", "",
             "| 盲测切片 | 信号数 | 信号胜率（扣成本） | 组合收益 | 同池基准 | 最大回撤 |", "|---|---:|---:|---:|---:|---:|"]
    for name, result in report["evaluation"].items():
        if "prediction" in result:
            pred, portfolio = result["prediction"], result["portfolio"]
            win = f"{pred['signal_win_rate_after_cost']:.1%}" if pred['signal_win_rate_after_cost'] is not None else "无信号"
            lines.append(f"| {name} | {pred['signal_count']} | {win} | {portfolio['total_return']:.2%} | {result['benchmark']['total_return']:.2%} | {portfolio['max_drawdown']:.2%} |")
        else:
            lines.append(f"| {name} | 无匹配样本 | — | — | — | — |")
    counts = report.get("sample_counts", {})
    lines += ["", "## 样本与检验", "", "```json", json.dumps(counts, ensure_ascii=False, indent=2, default=str), "```",
              "", "## 大模型解读", "", "以下为自动解释；事实核对以本报告的数值指标与样本统计为准。", "", review, "", "## 边界", ""] + [f"- {text}" for text in report["limitations"]]
    return "\n".join(lines) + "\n"


def review_evidence(root, report):
    """Add saved development scope and implementation facts, leaving machine metrics intact."""
    root = Path(root)
    selection = _load_json(root / "selection.json")
    development = _load_json(root / "development.json")
    candidates = [item["candidate"] for item in development.get("results", [])]
    saved_learning = root / "implementation/quant_research/automl/learning.py"
    current_learning = Path(__file__).with_name("learning.py")
    same_learning = saved_learning.is_file() and saved_learning.read_bytes() == current_learning.read_bytes()
    method = (
        "数值特征在基础拟合集内拟合中位数插补、缺失指示和StandardScaler标准化，随后用于校准/验证/测试；"
        "行业使用OneHotEncoder且允许未知类别。分类模型用按时间靠后的独立校准集做sigmoid/Platt式LogisticRegression校准，"
        "基础拟合与校准边界按标签结束时间purge；回归模型不做概率校准。已有标准化和Platt式校准，不将它们当作尚未实现功能。"
        if same_learning else "保存的学习实现与当前实现不同；本次未核实具体预处理/校准实现，不推断它们缺失。"
    )
    method += (
        "开发阶段是同一开发股票池的扩展时间窗口验证；整家公司留出只用于最终评估，未实现开发期留公司交叉验证。"
        "分类概率的事件是毛收益超过target_return，扣成本信号胜率是另一个口径。信号逐日统计且持有期重叠；"
        "组合每h个交易日再平衡，非再平衡日的信号未必成交。trade_count是BUY/SELL成交记录数，不是独立完整交易次数。"
    )
    if "symbol" not in selection.get("columns", []):
        method += "当前选中特征不含股票代码；仅凭公司留出差异不能断言模型使用或记忆了公司ID。"
    compact = [{"candidate": item["candidate"], "score": item["score"],
                "constraint_checks": item["constraint_checks"],
                "folds": [{"training": fold.get("training", {}), "validation_rows": fold.get("validation_rows"),
                            "signal_count": fold["prediction"]["signal_count"],
                            "signal_win_rate_after_cost": fold["prediction"]["signal_win_rate_after_cost"],
                            "skill_vs_constant": fold["prediction"]["skill_vs_constant"],
                            "portfolio": fold["portfolio"]} for fold in item["folds"]]}
               for item in development.get("results", [])]
    return {**report, "review_context": {
        "methodology": method, "selected_feature_columns": selection.get("columns", []),
        "actually_evaluated": {"horizons": sorted({item["horizon"] for item in candidates}),
                               "tasks": sorted({item["task"] for item in candidates}),
                               "models": sorted({item["model"] for item in candidates})},
        "development_evidence": compact,
        "constraint_meaning": "enough_signals检查各折信号总数；each_fold_win_rate检查各折扣成本信号胜率门槛；"
            "drawdown_within_limit检查各折组合回撤绝对值；each_fold_beats_constant检查各折预测损失相对常数基准改善"
            "（分类Brier或回归MSE），不是胜率超过常数基准。",
        "interpretation": "最后只对开发集选出的一名候选揭盲；整项研究覆盖范围以actually_evaluated为准。"
            "未做统计显著性检验或多组独立公司/新时期重复验证。小公司数、少量信号、重叠标签和有限成交记录只能支持当前切片的描述。"
            "trade_count大于0代表有实际成交及其收益观测；记录少不等于没有收益证据，需同时报告观测和不确定性。"
    }}


def review_research(root, advisor=None):
    """Retry language assessment using saved aggregate evidence; never fits a model."""
    root = Path(root)
    report = _load_json(root / "report.json")
    evidence = review_evidence(root, report)
    write_json(root / "review_evidence.json", evidence)
    review = "未启用大模型评审；数值证据已生成。"
    outcome = {"completed": False, "requested": advisor is not None}
    if advisor is not None:
        try:
            review = advisor.review(evidence)
            outcome["completed"] = True
        except Exception as exc:
            outcome["error_type"] = type(exc).__name__
            review = f"大模型评审未完成（{type(exc).__name__}）；保留完整数值证据，可独立重试评审。"
    (root / "review.md").write_text(review)
    (root / "report.md").write_text(render_report(report, review))
    write_json(root / "review_status.json", outcome)
    return outcome


def run_research(spec, daily=None, events=(), *, source=None, output_root="outputs/stock_automl",
                 advisor=None, progress=print, resume_dir=None, check_cancel=lambda: None,
                 checkpoint=lambda value: None):
    """Bounded research with atomic trial checkpoints. Resume trusts only local run artifacts.

    An interrupted trial consumes its already reserved attempt. Completed trials, round picks,
    data and the pre-holdout selection are never recomputed on resume. A running sklearn fit
    is interrupted by the caller's process supervisor; callbacks cooperate at work boundaries.
    """
    started = time.monotonic()
    files = _implementation_files()
    code_hash = _implementation_hash(files)
    def emit(stage, message, **facts):
        check_cancel()
        progress({"stage": stage, "message": message, **facts})
    if resume_dir is not None:
        root = Path(resume_dir)
        if _load_json(root / "spec.json") != json.loads(json.dumps(spec.to_dict())):
            raise ValueError("resume spec differs from frozen research spec")
        manifest = _load_json(root / "manifest.json")
        if manifest["implementation_sha256"] != code_hash:
            raise ValueError("resume requires the same implementation snapshot")
        panel = pd.read_pickle(root / "panel.pkl")
        fingerprint = hashlib.sha256(pd.util.hash_pandas_object(panel, index=True).values.tobytes()).hexdigest()
        if fingerprint != manifest["panel_sha256"]:
            raise ValueError("resume panel fingerprint mismatch")
        source = manifest["source"]
        run_id = manifest["run_id"]
        candidates = _load_json(root / "candidates.json")
        state = _load_json(root / "checkpoint.json")
        extra = state["extra_columns"]
        if (root / "report.json").exists():
            emit("评审", "计算证据已保存，读取既有结果")
            if not (root / "review_status.json").exists():
                review_research(root, advisor)
            return root, _load_json(root / "report.json")
    else:
        emit("准备数据", "固定数据、时间与公司留出集")
        if daily is None:
            raise ValueError("daily data is required for a new research run")
        panel = build_panel(daily, events)
        dates = np.array(sorted(panel.date.unique()))
        symbols = sorted(panel.symbol.unique())
        if len(dates) < 140:
            raise ValueError("at least 140 trading sessions are required")
        if len(symbols) < 5:
            raise ValueError("at least five companies are required")
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
        root = Path(output_root) / run_id
        root.mkdir(parents=True, exist_ok=False)
        write_json(root / "spec.json", spec.to_dict())
        snapshot = root / "implementation"
        snapshot.mkdir()
        for file in files:
            destination = snapshot / file.relative_to(Path(__file__).parents[2])
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(file.read_bytes())
        try:
            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
            dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip())
        except (OSError, subprocess.CalledProcessError):
            commit, dirty = None, None
        test_start = pd.Timestamp(dates[int(len(dates) * (1 - spec.test_fraction))])
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
        extra = sorted({column for event in events for column in event.columns if column not in ("symbol", "available_at", "industry")})
        state = {"extra_columns": extra, "results": [], "attempted": [], "failures": [], "planning": [],
                 "round_picks": {}, "elapsed_seconds": 0}
        write_json(root / "checkpoint.json", state)
    base_elapsed = float(state.get("elapsed_seconds", 0))
    def save():
        state["elapsed_seconds"] = base_elapsed + time.monotonic() - started
        write_json(root / "checkpoint.json", state)
        write_json(root / "development.json", {key: state[key] for key in ("results", "failures", "planning")})
        checkpoint({"research_dir": str(root.resolve()), "attempted_trials": len(state["attempted"]),
                    "completed_trials": len(state["results"]) + len(state["failures"])})
    results, failures, planning = state["results"], state["failures"], state["planning"]
    attempted = set(state["attempted"])
    lookup = {candidate["id"]: candidate for candidate in candidates}
    completed = {result["candidate"]["id"] for result in results + failures}
    for candidate_id in attempted - completed:
        failures.append({"candidate": lookup[candidate_id], "reason": "Previous attempt was interrupted; its trial budget remains consumed."})
    save()
    test_start = pd.Timestamp(manifest["test_start"])
    heldout = manifest["heldout_companies"]
    dates = np.array(sorted(panel.date.unique()))
    symbols = sorted(panel.symbol.unique())
    labels = {h: labeled_panel(panel, h) for h in spec.horizons}
    common_end = min(frame.date.max() for frame in labels.values())
    if pd.isna(common_end) or common_end < test_start:
        raise ValueError("no shared out-of-time evaluation window")
    for round_index in range(spec.rounds):
        round_key = str(round_index)
        if round_key in state["round_picks"]:
            picks = state["round_picks"][round_key]
        else:
            remaining = [c for c in candidates if c["id"] not in attempted]
            budget = min(len(remaining), (spec.max_trials - len(attempted) + spec.rounds - round_index - 1) // (spec.rounds - round_index))
            if budget <= 0:
                break
            if results:
                best = max(results, key=lambda r: (r["meets_constraints"], r["score"]))["candidate"]
                remaining.sort(key=lambda c: sum(c[k] != best[k] for k in ("model", "task", "horizon", "sampler", "features")))
                exploit = remaining[:max(1, budget // 2)]
                explore = remaining[len(exploit):]
                random.Random(spec.seed + round_index).shuffle(explore)
                remaining = exploit + explore
            picks = [c["id"] for c in remaining[:budget]]
            emit("模型设计", f"规划第 {round_index + 1} 轮，最多 {budget} 个实验")
            if advisor is not None:
                try:
                    proposed, analysis = advisor.plan(spec, remaining[:min(120, len(remaining))],
                        [{k: r[k] for k in ("candidate", "score", "meets_constraints", "signal_count", "constraint_checks")} for r in results], budget=budget)
                    picks = list(dict.fromkeys(proposed + picks))[:budget]
                    planning.append({"round": round_index + 1, "analysis": analysis, "candidate_ids": picks})
                except Exception as exc:
                    planning.append({"round": round_index + 1, "fallback": type(exc).__name__,
                                     "analysis": "LLM规划未完成，使用已保存约束下的确定性探索。"})
            else:
                planning.append({"round": round_index + 1, "analysis": "使用冻结的研究参数和确定性候选探索。", "candidate_ids": picks})
            state["round_picks"][round_key] = picks
            save()
        for candidate_id in picks:
            if candidate_id in attempted:
                continue
            check_cancel()
            candidate = lookup[candidate_id]
            attempted.add(candidate_id)
            state["attempted"].append(candidate_id)
            save()  # reserve the budget before starting a potentially interrupted fit
            emit("训练与验证", f"{candidate['model']} / {candidate['task']} / 持有 {candidate['horizon']} 个交易日",
                 completed=len(results) + len(failures), total=spec.max_trials, attempted=len(attempted))
            data = labels[candidate["horizon"]]
            development = data[(~data.symbol.isin(heldout)) & (data.label_end < test_start)]
            try:
                result = assess_candidate(development, panel[~panel.symbol.isin(heldout)], candidate, spec, extra, check_cancel)
                results.append(result)
            except ValueError as exc:
                failures.append({"candidate": candidate, "reason": str(exc)[:300]})
            save()
    if not results:
        raise ValueError("no evaluable candidates; inspect the saved development evidence")
    results.sort(key=lambda r: (r["meets_constraints"], r["score"]), reverse=True)
    selection_path = root / "selection.json"
    winner = _load_json(selection_path) if selection_path.exists() else results[0]
    write_json(selection_path, winner)  # freeze before revealing any test slice
    c = winner["candidate"]
    data = labels[c["horizon"]]
    development = data[(~data.symbol.isin(heldout)) & (data.label_end < test_start)]
    development = development[sample_mask(development, c["sampler"], spec)]
    emit("最终训练", "冻结开发集选择，训练用于留出评估的模型",
         completed=len(results) + len(failures), total=spec.max_trials)
    if (root / "model.joblib").exists():
        model = joblib.load(root / "model.joblib")
    else:
        model = fit_model(development, c, winner["columns"], spec)
        joblib.dump(model, root / "model.joblib.tmp")
        os.replace(root / "model.joblib.tmp", root / "model.joblib")
    blind = data[data.date.between(test_start, common_end)]
    reports = {}
    for name, subset in (("new_period", blind[~blind.symbol.isin(heldout)]),
                         ("new_companies_and_period", blind[blind.symbol.isin(heldout)])):
        check_cancel()
        emit("盲测与回测", f"评估 {name}")
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
    sample_counts = {"panel_rows": len(panel), "companies": len(symbols), "sessions": len(dates),
                     "eligible_by_horizon": {str(h): len(frame) for h, frame in labels.items()},
                     "development": model.training_counts,
                     "test_start": test_start, "test_signal_end": common_end,
                     "heldout_companies": heldout,
                     "heldout_rows": {name: r["prediction"]["rows"] for name, r in reports.items() if "prediction" in r}}
    write_json(root / "sample_counts.json", sample_counts)
    report = {"run_id": run_id, "objective": spec.objective, "selected": c,
              "development_constraints_met": winner["meets_constraints"],
              "development_constraint_checks": winner["constraint_checks"],
              "decision_policy": {"probability_threshold": spec.probability_threshold,
                                  "regression_threshold": spec.regression_threshold, "target_return": spec.target_return,
                                  "top_k": spec.top_k, "min_signals": spec.min_signals,
                                  "min_win_rate": spec.min_win_rate, "max_drawdown": spec.max_drawdown},
              "sample_counts": sample_counts, "attempted_trials": len(attempted), "successful_trials": len(results),
              "evaluation": reports, "elapsed_seconds": round(base_elapsed + time.monotonic() - started, 2),
              "limitations": [
                  "研究回测使用复权价格及1份单位，未完整模拟A股100股整手、开盘涨跌停排队与停复牌可成交性；不作为实盘回报。",
                  "交易成本为任务假设，不自动推断历史税费制度；复权数据可能被供应商修订。",
                  "信号胜率是重叠持有期样本统计，不能视为独立已成交交易胜率；组合收益另由逐日现金账本计算。",
                  "horizon定义：收盘后信号，下一交易日开盘买入，持有h个交易日后开盘退出；不是期间触及目标价概率。",
                  "随机起始股票池可含后来退市股票，但退市清算和历史数据库完整性尚未认证。",
                  "所有盲测只评估开发集选定的一名候选；本轮报告不触发自动再训练或交易。",
              ] + (source or {}).get("warnings", [])}
    write_json(root / "report.json", report)
    save()
    emit("评审", "研究指标与回测已保存，生成独立语言评审")
    review_research(root, advisor)
    emit("完成", "训练、留出评估和报告已保存", completed=len(attempted), total=spec.max_trials)
    return root, report
