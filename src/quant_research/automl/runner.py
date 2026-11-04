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
from .config import ResearchSpec
from .features import build_panel, feature_columns, labeled_panel, sample_mask
from .learning import fit_model, temporal_folds
from .decision import choose_decision_policy, decision_policy_rank


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


def _precision_constraint_checks(policy, folds, spec):
    """Precision never substitutes for cost-aware wins or portfolio drawdown limits."""
    signals = sum(fold["prediction"]["signal_count"] for fold in folds)
    wins = sum(fold["prediction"]["signal_count"] * (fold["prediction"]["signal_win_rate_after_cost"] or 0)
               for fold in folds)
    return {**policy["checks"],
            "drawdown_within_limit": all(abs(fold["portfolio"]["max_drawdown"]) <= spec.max_drawdown for fold in folds),
            "net_signal_win_rate_at_least_minimum": signals > 0 and wins / signals >= spec.min_win_rate}


def _policy_evidence(policy):
    """Compact numerical evidence for both planning feedback and final language review."""
    keys = ("signal_count", "signal_dates", "target_precision", "target_recall", "actual_down_count", "actual_down_rate",
            "coverage", "positive_rows", "negative_rows", "precision_lift", "unconditional_up_rate", "date_macro_precision",
            "date_precision_lower_quartile", "worst_active_month_precision", "signal_win_rate_after_cost", "mean_signal_net_return", "by_month")
    return {**{key: policy[key] for key in ("threshold", "top_k", "eligible", "checks", "selection_source", "selection_note",
                                          "rows", "signal_start", "signal_end", "search_trials", "portfolio") if key in policy},
            "metrics": {key: policy.get("metrics", {})[key] for key in keys if key in policy.get("metrics", {})}}


def _planning_evidence(result):
    summary = {key: result[key] for key in ("candidate", "score", "meets_constraints", "signal_count", "constraint_checks")}
    if result.get("decision_policy") is not None:
        summary["decision_policy"] = _policy_evidence(result["decision_policy"])
    return summary


def assess_candidate(data, panel, candidate, spec, extra_columns, check_cancel=lambda: None):
    columns = feature_columns(data, candidate["features"], extra_columns, requested=spec.feature_names)
    folds, predictions = [], []
    for train, validation in temporal_folds(data, spec.folds):
        check_cancel()
        train = train[sample_mask(train, candidate["sampler"], spec)]
        validation = validation[sample_mask(validation, candidate["sampler"], spec)]
        if validation.empty:
            raise ValueError("no samples matching strategy in validation fold")
        model = fit_model(train, candidate, columns, spec)
        scored = validation.copy()
        scored["prediction"] = model.predict(scored)
        predictions.append((scored, _baseline(train, candidate["task"], spec)))
        folds.append({"train_end": train.label_end.max(), "validation_start": validation.date.min(),
                      "validation_end": validation.label_end.max(), "training": model.training_counts,
                      "validation_rows": len(validation)})
    if len(folds) != spec.folds:
        raise ValueError("insufficient chronological folds")
    precision_mode = spec.min_precision is not None or spec.optimize_threshold
    policy = choose_decision_policy(pd.concat([p for p, _ in predictions]), candidate["task"], spec) if precision_mode else None
    for fold, (scored, baseline) in zip(folds, predictions):
        check_cancel()
        fold["prediction"] = prediction_metrics(scored, candidate["task"], spec, baseline, policy=policy)
        fold["portfolio"] = backtest_predictions(scored, panel, candidate, spec, policy=policy)["metrics"]
    skills = [f["prediction"]["skill_vs_constant"] for f in folds]
    signals = sum(f["prediction"]["signal_count"] for f in folds)
    checks = constraint_checks(folds, spec)
    if policy is not None:
        checks = _precision_constraint_checks(policy, folds, spec)
        policy["metrics"].update(
            signal_win_rate_after_cost=sum(f["prediction"]["signal_count"] * (f["prediction"]["signal_win_rate_after_cost"] or 0)
                                           for f in folds) / signals if signals else None)
        policy.update(checks=checks, eligible=all(checks.values()))
    eligible = all(checks.values())
    return {"candidate": candidate, "columns": columns, "folds": folds,
            "meets_constraints": bool(eligible), "constraint_checks": checks,
            "score": float(policy["metrics"]["date_macro_precision"] or 0) if policy else float(np.mean(skills) - np.std(skills)),
            "signal_count": signals, **({"decision_policy": policy} if policy is not None else {})}


def candidate_rank(result):
    if "decision_policy" in result:
        return (result["meets_constraints"], *decision_policy_rank(result["decision_policy"])[1:])
    return (result["meets_constraints"], result["score"])


def _implementation_files():
    files = sorted(Path(__file__).parent.glob("*.py")) + sorted(Path(__file__).with_name("prompts").glob("*.md"))
    return files + sorted(Path(__file__).parents[2].joinpath("backtest").glob("*.py"))


def _implementation_hash(files):
    return hashlib.sha256(b"".join(p.read_bytes() for p in files)).hexdigest()


def _load_json(path):
    return json.loads(Path(path).read_text())


def model_display_name(candidate):
    """Name the fitted estimator, rather than exposing its shared candidate-family key."""
    classification = candidate.get("task") == "classification"
    names = {
        "linear": ("逻辑回归", "Ridge 收益回归"),
        "elastic_net": ("ElasticNet 逻辑回归", "ElasticNet 收益回归"),
        "tree": ("决策树分类", "决策树回归"),
        "forest": ("随机森林分类", "随机森林回归"),
        "svm": ("支持向量分类（SVC）", "支持向量回归（SVR）"),
        "hist_gradient_boosting": ("直方图梯度提升分类", "直方图梯度提升回归"),
    }
    pair = names.get(candidate.get("model"))
    return pair[0 if classification else 1] if pair else str(candidate.get("model") or "未记录模型")


def _report_facts(report):
    """Render saved measurements once for both the user and the language reviewer."""
    c = report["selected"]
    classification = c["task"] == "classification"
    policy = report.get("decision_policy", {})
    target = policy.get("target_return")
    target_text = f"超过 {target:.2%}" if target is not None else "超过设定阈值"
    goal = f"预测到期毛收益{target_text}的概率" if classification else "预测到期收益率"
    outcome = ("开发集模型满足全部预设筛选条件，泛化仍需结合留出评估。"
               if report["development_constraints_met"] else
               "本轮未找到满足全部开发集筛选条件的模型；以下交付的是最佳候选及其未达标证据。")
    lines = [f"**{model_display_name(c)} · 持有 {c['horizon']} 个交易日 · {goal}。**", "", outcome,
             f"完成 {report['attempted_trials']} 个候选实验，其中 {report['successful_trials']} 个可评估。"]
    check_names = {
        "enough_signals": "开发验证信号总数",
        "each_fold_win_rate": "每个时间验证折的扣成本信号胜率",
        "drawdown_within_limit": "每个时间验证折的组合回撤",
        "each_fold_beats_constant": "每个时间验证折的预测损失优于常数基准",
        "enough_signal_dates": "有信号的交易日期数",
        "target_precision_at_least_minimum": "选中事件精确率",
        "date_macro_precision_at_least_minimum": "按日期平均的选中精确率",
        "net_signal_win_rate_at_least_minimum": "开发期扣成本信号胜率",
        "final_policy_support_and_precision": "最终模型独立开发选择段的支持数与精确率",
        "final_policy_net_signal_win_rate_at_least_minimum": "最终门槛选择段的扣成本信号胜率",
        "final_policy_drawdown_within_limit": "最终门槛选择段的组合回撤",
    }
    failed = [check_names.get(key, key) for key, passed in report.get("development_constraint_checks", {}).items() if not passed]
    if failed:
        lines += ["未通过的条件：" + "；".join(failed) + "。"]
    counts = report.get("sample_counts", {})
    training = counts.get("development", {})
    number = lambda value: f"{value:,}" if isinstance(value, (int, float)) else "未记录"
    lines += ["", "## 样本与划分", "",
              f"行情面板共 {number(counts.get('panel_rows'))} 行，覆盖 {number(counts.get('companies'))} 家公司、{number(counts.get('sessions'))} 个交易日。样本单位为“公司 × 交易日”。", "",
              "| 开发集用途 | 样本行数 |", "|---|---:|"]
    for label, key in (("进入最终训练的开发样本", "input_rows"), ("实际模型拟合", "fit_rows"),
                       ("独立概率校准", "calibration_rows"), ("时间边界剔除", "purged_rows"),
                       ("训练预算抽样排除", "budget_sampled_out_rows")):
        if key in training:
            suffix = "（收益回归不做概率校准）" if key == "calibration_rows" and not classification else ""
            lines.append(f"| {label}{suffix} | {number(training[key])} |")
    if counts.get("test_start") and counts.get("test_signal_end"):
        lines += ["", f"留出信号日期：{str(counts['test_start'])[:10]} 至 {str(counts['test_signal_end'])[:10]}。"]
    if isinstance(counts.get("heldout_companies"), list):
        lines += [f"整家公司留出 {len(counts['heldout_companies'])} 家，不参与开发集训练和候选选择。"]
    if counts.get("policy_selection"):
        p = counts["policy_selection"]
        lines += [f"模型和概率校准完成后，另用 {number(p['rows'])} 行开发末段样本选择出手门槛；这些样本未参与最终模型拟合或校准，仍属于开发选择证据。"]
        if p.get("signal_start") and p.get("signal_end"):
            lines += [f"最终门槛冻结于开发选择段：{str(p['signal_start'])[:10]} 至 {str(p['signal_end'])[:10]}；冻结后直接用于后续留出评估，未再重训模型或按测试结果调整门槛。"]
    lines += ["", "## 留出评估", "",
              "留出描述本次训练的数据划分，不自动证明历史窗口此前未被使用。重复使用的历史窗口仅作流程复验，不能作为新的盲测证据。",
              "", "### 预测表现", "",
              "预测损失越低越好。相对常数基准的改善为正表示损失更低，为负表示损失更高，不等于收益率。"]
    loss_key, baseline_key, loss_label = (("brier", "baseline_brier", "Brier") if classification
                                         else ("rmse", "baseline_rmse", "RMSE"))
    lines += ["分类采用 Brier 损失。" if classification else "表中展示 RMSE；相对改善按 MSE 计算。", "",
              f"| 留出范围 | 样本 / 公司 | {loss_label} | 常数基准 {loss_label} | 相对基准改善 |",
              "|---|---:|---:|---:|---|"]
    labels = {"new_period": "原公司 · 后续时段", "new_companies_and_period": "新公司 · 后续时段"}
    available = []
    for name, result in report.get("evaluation", {}).items():
        label = labels.get(name, name)
        if "prediction" not in result:
            lines.append(f"| {label} | 无匹配样本，未评估 | — | — | — |")
            continue
        available.append((label, result))
        pred = result["prediction"]
        skill = pred.get("skill_vs_constant")
        direction = "损失更低" if skill is not None and skill > 0 else "损失更高" if skill is not None and skill < 0 else "损失相同"
        skill_text = f"{skill:+.2%}（{direction}）" if skill is not None else "未记录"
        loss = lambda key: f"{pred[key]:.6f}" if pred.get(key) is not None else "未记录"
        lines.append(f"| {label} | {number(pred.get('rows'))} / {number(pred.get('companies'))} | {loss(loss_key)} | {loss(baseline_key)} | {skill_text} |")
    if not report.get("evaluation"):
        lines.append("| 尚无留出评估记录 | — | — | — | — |")
    lines += ["", "### 信号与组合回测", ""]
    threshold = policy.get("threshold", policy.get("probability_threshold" if classification else "regression_threshold"))
    if threshold is not None:
        lines += [(f"预测概率至少达到 {threshold:.2%} 时计作信号。" if classification else
                   f"预测收益率至少达到 {threshold:.2%} 时计作信号。"), ""]
        if policy.get("top_k") is not None and policy.get("threshold") is not None:
            lines += [f"每日从达到门槛的股票中最多选 {policy['top_k']} 只；达不到门槛可以空选。", ""]
        elif policy.get("top_k") is not None:
            lines += [f"本次固定门槛的信号统计包含全部过线样本；组合执行时每次最多选 {policy['top_k']} 只。", ""]
    if any("target_precision" in result["prediction"] for _, result in available):
        lines += ["| 留出范围 | 目标命中精确率 | 命中 / 选中 | 实际下跌数 | 覆盖率 | 出手日期 |",
                  "|---|---:|---:|---:|---:|---:|"]
        fmt = lambda value: f"{value:.2%}" if value is not None else "无信号，不计算"
        for label, result in available:
            m = result["prediction"]
            lines.append(f"| {label} | {fmt(m.get('target_precision'))} | {number(m.get('true_positive'))} / {number(m.get('signal_count'))} | {number(m.get('actual_down_count'))} | {fmt(m.get('coverage'))} | {number(m.get('signal_dates'))} |")
        lines += ["", "目标精确率检查毛收益是否超过 target_return；扣成本信号胜率检查毛收益扣除假设双边费用后是否为正，两者是不同事件。信号扣费采用费率直接相减的估算，实际成交费用与收益另由组合账本计算。实际下跌另行统计；按日期的统计仍是描述证据，不是未来胜率保证。", ""]
    lines += [
              "| 留出范围 | 信号数 | 扣成本信号胜率 | 组合收益 | 同池基准收益 | 组合最大回撤 | 同池基准最大回撤 | 买卖成交记录 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
    percent = lambda value: f"{value:.2%}" if value is not None else "未记录"
    for label, result in available:
        pred, portfolio, benchmark = result["prediction"], result.get("portfolio", {}), result.get("benchmark", {})
        win = "无信号，不计算" if pred.get("signal_count") == 0 else percent(pred.get("signal_win_rate_after_cost"))
        trades = portfolio.get("trade_count")
        trade_text = "0（未成交）" if trades == 0 else number(trades)
        lines.append(f"| {label} | {number(pred.get('signal_count'))} | {win} | {percent(portfolio.get('total_return'))} | {percent(benchmark.get('total_return'))} | {percent(portfolio.get('max_drawdown'))} | {percent(benchmark.get('max_drawdown'))} | {trade_text} |")
    if not available:
        lines.append("| 尚无可评估切片 | — | — | — | — | — | — | — |")
    lines += ["", "信号按公司和日期统计，持有区间可能重叠；组合按持有周期调仓，信号不一定成交。买卖成交记录数不是完整交易次数；零成交时的零收益代表未建立仓位，不代表模型预测正确。"]
    return lines


def render_report(report, review):
    lines = ["# 股票 AutoML 研究报告", "", *_report_facts(report), "", "## 研究解读", "",
             "以下为基于上述事实的语言分析，用于解释局限与下一轮假设；数值与筛选结论由程序生成。", "", review,
             "", "## 本次研究要求", "", report["objective"], "", "## 口径与限制", ""]
    lines += [f"- {text}" for text in report["limitations"]]
    lines += ["", "完整候选参数、样本统计与数值证据可在交付的 spec.json、selection.json、sample_counts.json、development.json 和 report.json 中查看。"]
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
    if selection.get("frozen_policy"):
        method += (
            "候选模型用开发期OOF预测比较出手阈值与精确率；最终候选选定后，开发末段另作门槛选择段，"
            "最终模型拟合及其内部概率校准均只使用此前的开发数据，标签结束时间也早于该选择段。"
            "最终模型及校准器保持冻结，再在该开发末段选择最终门槛；这仍是开发选择证据，并非未参与选择的测试。"
            "最终留出评估复用这个模型、校准器及门槛，不按留出结果重训或调阈值。"
        )
    compact = [{"candidate": item["candidate"], "score": item["score"],
                "constraint_checks": item["constraint_checks"],
                **({"decision_policy": _policy_evidence(item["decision_policy"])} if item.get("decision_policy") else {}),
                "folds": [{"training": fold.get("training", {}), "validation_rows": fold.get("validation_rows"),
                            "signal_count": fold["prediction"]["signal_count"],
                            "signal_win_rate_after_cost": fold["prediction"]["signal_win_rate_after_cost"],
                            **{key: fold["prediction"][key] for key in ("target_precision", "signal_dates", "actual_down_count", "coverage", "date_macro_precision") if key in fold["prediction"]},
                            "skill_vs_constant": fold["prediction"]["skill_vs_constant"],
                            "portfolio": fold["portfolio"]} for fold in item["folds"]]}
               for item in development.get("results", [])]
    return {**report, "review_context": {
        "fact_summary": "\n".join(_report_facts(report)),
        "methodology": method, "selected_feature_columns": selection.get("columns", []),
        "actually_evaluated": {"horizons": sorted({item["horizon"] for item in candidates}),
                               "tasks": sorted({item["task"] for item in candidates}),
                               "models": sorted({item["model"] for item in candidates})},
        "development_evidence": compact,
        **({"model_explanation": _load_json(root / "explanation.json")} if (root / "explanation.json").is_file() else {}),
        **({"frozen_policy": _policy_evidence(selection["frozen_policy"])} if selection.get("frozen_policy") else {}),
        "constraint_meaning": "enough_signals检查各折信号总数；each_fold_win_rate检查各折扣成本信号胜率门槛；"
            "drawdown_within_limit检查各折组合回撤绝对值；each_fold_beats_constant检查各折预测损失相对常数基准改善"
            "（分类Brier或回归MSE），不是胜率超过常数基准。"
            "precision模式允许无信号验证折：按开发OOF合计支持数、出手日期数、目标精确率、日期平均精确率、"
            "扣成本信号胜率及各折组合回撤判断资格，不要求各折都有信号，也不以预测损失改善作为硬门槛。"
            "最终门槛选择段需再次满足支持数、目标精确率、扣成本信号胜率和组合回撤要求。",
        "interpretation": "最后只对开发集选出的一名候选做留出评估；整项研究覆盖范围以actually_evaluated为准。"
            "留出是本次训练的数据划分，不能据此认定历史窗口从未被使用；重复历史窗口只支持流程复验。"
            "未做统计显著性检验或多组独立公司/新时期重复验证。小公司数、少量信号、重叠标签和有限成交记录只能支持当前切片的描述。"
            "trade_count大于0代表有实际成交及其收益观测；记录少不等于没有收益证据，需同时报告观测和不确定性。"
    }}


def review_research(root, advisor=None):
    """Retry language assessment using saved aggregate evidence; never fits a model."""
    root = Path(root)
    report = _load_json(root / "report.json")
    if (root / "model.joblib").is_file():
        from .assets import export_strategy
        existing = _load_json(root / "strategy.json") if (root / "strategy.json").is_file() else {}
        export_strategy(root, hypothesis=existing.get("hypothesis", ""))
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
    Missing optional spec fields use compatible defaults; the implementation snapshot must
    still match exactly. This is not cross-version retraining or recovery.
    """
    started = time.monotonic()
    files = _implementation_files()
    code_hash = _implementation_hash(files)
    def emit(stage, message, **facts):
        check_cancel()
        progress({"stage": stage, "message": message, **facts})
    if resume_dir is not None:
        root = Path(resume_dir)
        if ResearchSpec.from_dict(_load_json(root / "spec.json")).to_dict() != spec.to_dict():
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
        if not symbols:
            raise ValueError("at least one company is required")
        if len(symbols) < 2 and spec.company_holdout_fraction:
            raise ValueError("single-company research requires company_holdout_fraction=0")
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
        heldout = sorted(shuffled[:max(1, int(len(shuffled) * spec.company_holdout_fraction))]) if spec.company_holdout_fraction else []
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
                best = max(results, key=candidate_rank)["candidate"]
                remaining.sort(key=lambda c: sum(c[k] != best[k] for k in ("model", "task", "horizon", "sampler", "features")))
                exploit = remaining[:max(1, budget // 2)]
                explore = remaining[len(exploit):]
                random.Random(spec.seed + round_index).shuffle(explore)
                remaining = exploit + explore
            picks = [c["id"] for c in remaining[:budget]]
            emit("模型设计", f"规划第 {round_index + 1} 轮，最多 {budget} 个实验",
                 completed=len(results) + len(failures), total=spec.max_trials, attempted=len(attempted))
            if advisor is not None:
                try:
                    proposed, analysis = advisor.plan(spec, remaining[:min(120, len(remaining))],
                        [_planning_evidence(result) for result in results], budget=budget)
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
            emit("训练与验证", f"已处理 {len(results) + len(failures)} 个候选，其中 {len(results)} 个可评估、{len(failures)} 个不可评估",
                 completed=len(results) + len(failures), total=spec.max_trials, attempted=len(attempted))
    if not results:
        raise ValueError("no evaluable candidates; inspect the saved development evidence")
    results.sort(key=candidate_rank, reverse=True)
    selection_path = root / "selection.json"
    winner = _load_json(selection_path) if selection_path.exists() else results[0]
    write_json(selection_path, winner)  # candidate frozen before any test outcome is read
    c = winner["candidate"]
    data = labels[c["horizon"]]
    development = data[(~data.symbol.isin(heldout)) & (data.label_end < test_start)]
    development = development[sample_mask(development, c["sampler"], spec)]
    policy_data = None
    model_development = development
    if spec.min_precision is not None or spec.optimize_threshold:
        policy_start = sorted(development.date.unique())[int(development.date.nunique() * .8)]
        model_development = development[(development.date < policy_start) & (development.label_end < policy_start)]
        policy_data = development[development.date >= policy_start].copy()
    emit("最终训练", "冻结开发集选择，训练用于留出评估的模型",
         completed=len(results) + len(failures), total=spec.max_trials)
    if (root / "model.joblib").exists():
        model = joblib.load(root / "model.joblib")
    else:
        model = fit_model(model_development, c, winner["columns"], spec)
        joblib.dump(model, root / "model.joblib.tmp")
        os.replace(root / "model.joblib.tmp", root / "model.joblib")
    policy = None
    if policy_data is not None:
        policy = winner.get("frozen_policy")
        policy_data["prediction"] = model.predict(policy_data)
        if policy is None:
            policy = choose_decision_policy(policy_data, c["task"], spec)
            policy.update(selection_source="development_after_final_fit",
                          selection_note="最终模型及校准器冻结后，在未参与该模型拟合/校准的开发末段选择阈值；该段属于选择证据，不是最终测试。",
                          rows=len(policy_data), signal_start=policy_data.date.min(), signal_end=policy_data.date.max())
        policy_metrics = prediction_metrics(policy_data, c["task"], spec, _baseline(model_development, c["task"], spec), policy=policy)
        policy_backtest = backtest_predictions(policy_data, panel, c, spec, policy=policy)
        policy.update(metrics=policy_metrics, portfolio=policy_backtest["metrics"])
        checks = _precision_constraint_checks(policy, [{"prediction": policy_metrics, "portfolio": policy_backtest["metrics"]}], spec)
        policy.update(checks=checks, eligible=all(checks.values()))
        winner["frozen_policy"] = policy
        write_json(root / "policy_selection_backtest.json", policy_backtest)
        write_json(selection_path, winner)
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
        metrics = prediction_metrics(subset, c["task"], spec, _baseline(model_development, c["task"], spec), policy=policy)
        bt = backtest_predictions(subset, panel, c, spec, policy=policy)
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
    if policy is not None:
        sample_counts["policy_selection"] = {"rows": len(policy_data), "signal_start": policy_data.date.min(),
            "signal_end": policy_data.date.max(), "model_latest_label_end": model_development.label_end.max(),
            "purged_before_policy": len(development) - len(model_development) - len(policy_data)}
    write_json(root / "sample_counts.json", sample_counts)
    report = {"run_id": run_id, "objective": spec.objective, "selected": c,
              "development_constraints_met": winner["meets_constraints"] and (policy is None or policy["eligible"]),
              "development_constraint_checks": {**winner["constraint_checks"], **({
                  "final_policy_support_and_precision": all(value for key, value in policy["checks"].items()
                                                            if key not in ("drawdown_within_limit", "net_signal_win_rate_at_least_minimum")),
                  "final_policy_net_signal_win_rate_at_least_minimum": policy["checks"]["net_signal_win_rate_at_least_minimum"],
                  "final_policy_drawdown_within_limit": policy["checks"]["drawdown_within_limit"],
              } if policy else {})},
              "decision_policy": {"probability_threshold": spec.probability_threshold,
                                  "regression_threshold": spec.regression_threshold, "target_return": spec.target_return,
                                  "top_k": spec.top_k, "min_signals": spec.min_signals,
                                  "min_signal_dates": spec.min_signal_dates, "min_precision": spec.min_precision,
                                  "min_win_rate": spec.min_win_rate, "max_drawdown": spec.max_drawdown,
                                  **(policy or {})},
              "sample_counts": sample_counts, "attempted_trials": len(attempted), "successful_trials": len(results),
              "evaluation": reports, "elapsed_seconds": round(base_elapsed + time.monotonic() - started, 2),
              "limitations": [
                  "研究回测使用复权价格及1份单位，未完整模拟A股100股整手、开盘涨跌停排队与停复牌可成交性；不作为实盘回报。",
                  "交易成本为任务假设，不自动推断历史税费制度；复权数据可能被供应商修订。",
                  "信号胜率是重叠持有期样本统计，不能视为独立已成交交易胜率；组合收益另由逐日现金账本计算。",
                  "horizon定义：收盘后信号，下一交易日开盘买入，持有h个交易日后开盘退出；不是期间触及目标价概率。",
                  "股票池按研究配置选择，可含后来退市股票；退市清算和历史数据库完整性尚未认证。",
                  "所有盲测只评估开发集选定的一名候选；本轮报告不触发自动再训练或交易。",
              ] + (source or {}).get("warnings", [])}
    write_json(root / "report.json", report)
    save()
    emit("评审", "研究指标与回测已保存，生成独立语言评审")
    review_research(root, advisor)
    emit("完成", "训练、留出评估和报告已保存", completed=len(attempted), total=spec.max_trials)
    return root, report
