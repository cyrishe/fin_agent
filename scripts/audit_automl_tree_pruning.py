#!/usr/bin/env python3
"""Reconstruct saved development trees and audit cost-complexity pruning.

Only trusted, locally produced research artifacts are supported (panel.pkl is a
pickle). Original files and holdout evaluations are never modified. The alpha
grid comes exclusively from the earliest fold's base-fit partition. This is an
exploratory development audit, not a new out-of-sample performance claim.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

for _key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_key, "1")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import sklearn
from sklearn.base import clone

from src.quant_research.automl.config import ResearchSpec
from src.quant_research.automl.evaluation import backtest_predictions, prediction_metrics
from src.quant_research.automl.features import labeled_panel, sample_mask
from src.quant_research.automl.learning import FittedModel, fit_model, raw_score, temporal_folds, training_partition
from src.quant_research.automl.runner import constraint_checks, write_json


def read_json(path):
    return json.loads(path.read_text())


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_matching_code(source):
    """Do not silently reconstruct with a different learning/data/backtest implementation."""
    names = [f"quant_research/automl/{name}.py" for name in ("config", "features", "learning", "evaluation")]
    names += [f"backtest/{path.name}" for path in sorted((source / "implementation/backtest").glob("*.py"))]
    evidence = {}
    for name in names:
        historical, current = source / "implementation" / name, ROOT / "src" / name
        if historical.read_bytes() != current.read_bytes():
            raise ValueError(f"Saved implementation differs: {name}; use its original checkout")
        evidence[name] = sha256(current)
    return evidence


def tree_info(model):
    tree = model.pipeline[-1]
    calibration = None
    if model.calibrator is not None:
        calibration = {"coefficient_on_raw_log_odds": float(model.calibrator.coef_[0, 0]),
                       "intercept": float(model.calibrator.intercept_[0])}
    return {"depth": tree.get_depth(), "leaves": tree.get_n_leaves(),
            "nodes": tree.tree_.node_count, "ccp_alpha": tree.ccp_alpha,
            "max_depth_parameter": tree.max_depth, "min_samples_leaf": tree.min_samples_leaf,
            "calibration": calibration}


def prune_refit(base, train, candidate, spec, alpha):
    if alpha == 0:
        return base
    fit, calibration, counts = training_partition(train, candidate, spec)
    pipeline = clone(base.pipeline)
    pipeline[-1].set_params(ccp_alpha=float(alpha))
    target = (fit.forward_return > spec.target_return).astype(int) if candidate["task"] == "classification" else fit.forward_return
    pipeline.fit(fit[base.columns], target)
    calibrator = None
    if calibration is not None:
        calibrator = clone(base.calibrator)
        calibrator.fit(raw_score(pipeline, calibration[base.columns]).reshape(-1, 1),
                       (calibration.forward_return > spec.target_return).astype(int))
    return FittedModel(pipeline, calibrator, base.columns, candidate["task"], counts)


def alpha_grid(base, train, candidate, spec):
    fit, _, _ = training_partition(train, candidate, spec)
    features = base.pipeline[:-1].transform(fit[base.columns])
    target = (fit.forward_return > spec.target_return).astype(int) if candidate["task"] == "classification" else fit.forward_return
    path = base.pipeline[-1].cost_complexity_pruning_path(features, target)
    positive = np.unique(path.ccp_alphas[path.ccp_alphas > 0])
    indices = np.unique(np.linspace(0, len(positive) - 1, min(4, len(positive)), dtype=int)) if len(positive) else []
    return [0.0] + [float(positive[index]) for index in indices]


def numeric_differences(a, b, path=""):
    """Compare original numerical fold evidence, including nulls and counts."""
    differences = []
    if isinstance(a, dict):
        for key, value in a.items():
            differences += numeric_differences(value, b.get(key), f"{path}.{key}")
    elif isinstance(a, list):
        if not isinstance(b, list) or len(a) != len(b):
            differences.append(path)
        else:
            for index, value in enumerate(a):
                differences += numeric_differences(value, b[index], f"{path}[{index}]")
    elif isinstance(a, (int, float)):
        if b is None or not np.isclose(a, b, rtol=1e-10, atol=1e-12):
            differences.append(path)
    elif a != b:
        differences.append(path)
    return differences


def extract_rules(model, fit, validation, spec):
    """Inverse standardization and attach descriptive validation counts to every leaf."""
    transform, tree = model.pipeline[0], model.pipeline[-1]
    names = transform.get_feature_names_out()
    numeric = transform.named_transformers_["numeric"]
    scaler = numeric.named_steps["standardscaler"]
    imputer = numeric.named_steps["simpleimputer"]
    original_numeric = [name for name in model.columns if name != "industry"]
    numeric_names = imputer.get_feature_names_out(original_numeric)
    fit_leaves = tree.apply(transform.transform(fit[model.columns]))
    validation_leaves = tree.apply(transform.transform(validation[model.columns]))
    cost = 2 * (spec.commission_rate + spec.slippage_rate) + spec.sell_tax_rate
    rules = []

    def walk(node, conditions):
        left, right = tree.tree_.children_left[node], tree.tree_.children_right[node]
        if left == right:
            fitting = fit[fit_leaves == node]
            testing = validation[validation_leaves == node]
            result = {"leaf": int(node), "conditions": conditions, "fit_support": len(fitting),
                      "fit_target_hit_rate": float((fitting.forward_return > spec.target_return).mean()),
                      "fit_mean_return": float(fitting.forward_return.mean()),
                      "validation_support": len(testing),
                      "validation_target_hit_rate": float((testing.forward_return > spec.target_return).mean()) if len(testing) else None,
                      "validation_win_rate_after_cost": float((testing.forward_return > cost).mean()) if len(testing) else None,
                      "validation_mean_net_return": float(testing.forward_return.mean() - cost) if len(testing) else None}
            if model.task == "classification":
                # Leaf probability before calibration is the fitted class frequency.
                raw = float(tree.tree_.value[node, 0, 1] / tree.tree_.value[node, 0].sum())
                bounded = np.clip(raw, 1e-5, 1 - 1e-5)
                calibrated = float(model.calibrator.predict_proba([[np.log(bounded / (1 - bounded))]])[0, 1])
                result.update(raw_leaf_probability=raw, calibrated_probability=calibrated,
                              emits_signal=bool(calibrated >= spec.probability_threshold))
            else:
                value = float(tree.tree_.value[node, 0, 0])
                result.update(predicted_return=value, emits_signal=bool(value >= spec.regression_threshold))
            rules.append(result)
            return
        index = tree.tree_.feature[node]
        threshold = float(tree.tree_.threshold[node])
        condition = {"transformed_feature": str(names[index]), "standardized_threshold": threshold}
        if index < len(numeric_names):
            feature = str(numeric_names[index])
            condition.update(feature=feature, original_threshold=float(threshold * scaler.scale_[index] + scaler.mean_[index]))
            if feature in original_numeric:
                condition["missing_imputed_as"] = float(imputer.statistics_[original_numeric.index(feature)])
            else:
                condition["meaning"] = "missing indicator: 1 means original value was absent"
        else:
            condition.update(feature=str(names[index]), original_threshold=threshold,
                             meaning="one-hot indicator: 1 means category matches, 0 otherwise; unknown categories are all zero")
        walk(left, conditions + [{**condition, "operator": "<="}])
        walk(right, conditions + [{**condition, "operator": ">"}])

    walk(0, [])
    assert sum(rule["fit_support"] for rule in rules) == len(fit)
    assert sum(rule["validation_support"] for rule in rules) == len(validation)
    # Verify that exported original-scale branches route every actual row to its true leaf.
    for frame, leaves in ((fit, fit_leaves), (validation, validation_leaves)):
        transformed = transform.transform(frame[model.columns])
        expected = np.full(len(frame), -1, dtype=int)
        for rule in rules:
            mask = np.ones(len(frame), dtype=bool)
            for condition in rule["conditions"]:
                index = list(names).index(condition["transformed_feature"])
                values = transformed[:, index]
                if index < len(numeric_names):
                    values = values * scaler.scale_[index] + scaler.mean_[index]
                threshold = condition["original_threshold"]
                mask &= values <= threshold if condition["operator"] == "<=" else values > threshold
            expected[mask] = rule["leaf"]
        if not np.array_equal(expected, leaves):
            raise AssertionError("Inverse-scaled rule routing differs from fitted tree")
    return rules


def render_summary(report):
    constant_winners = sum(all(fold["tree"]["leaves"] == 1 for fold in max(candidate["variants"], key=lambda item: item["score"])["folds"])
                           for candidate in report["candidates"])
    qualified = sum(variant["meets_constraints"] for candidate in report["candidates"] for variant in candidate["variants"])
    lines = ["# 历史决策树的重建、解释与剪枝审计", "",
             "这是开发集上的探索性审计。历史候选树未保存，按冻结配置与实现重新拟合；未读取原盲测预测、回测或报告，未修改原研究文件。", "",
             "原树已有 max_depth=3/6 与 min_samples_leaf=20 的预剪枝；alpha=0 表示未做额外成本复杂度后剪枝，不代表无限制树。", "",
             "每个候选的 alpha 固定网格仅从第一时间折的基础拟合分区生成，分类校准、两折验证及留出数据都不参与生成；含剪到根节点的对照。", "",
             "分类每次重拟合仍使用原时间校准分区做 LogisticRegression 校准；负校准系数会反转原树概率排序，近零系数会压平概率。", "",
             f"本次 {len(report['candidates'])} 个树候选各比较 5 个 alpha、2 折。满足原全部约束的变体共 {qualified} 个；按原开发 score 排序，{constant_winners} 个候选的最优变体在两折都只剩常数根节点。减少预测误差不等于发现有用的选股条件。", "",
             "| 候选 | alpha | 折 | 深度/叶数 | Brier/RMSE | 信号 | 扣成本信号胜率 | 组合收益 | 最大回撤 | 校准系数 |", "|---|---:|---:|---|---:|---:|---:|---:|---:|---:|"]
    for result in report["candidates"]:
        c = result["candidate"]
        for variant in result["variants"]:
            for index, fold in enumerate(variant["folds"], 1):
                p, bt, tree = fold["prediction"], fold["portfolio"], fold["tree"]
                win = "无信号" if p["signal_win_rate_after_cost"] is None else f"{p['signal_win_rate_after_cost']:.1%}"
                coefficient = "—" if tree["calibration"] is None else f"{tree['calibration']['coefficient_on_raw_log_odds']:.5f}"
                loss = p.get("brier", p.get("rmse"))
                lines.append(f"| {c['horizon']}日{c['task']} / {c['id']} | {variant['alpha']:.6g} | {index} | {tree['depth']}/{tree['leaves']} | {loss:.6f} | {p['signal_count']} | {win} | {bt['total_return']:.2%} | {bt['max_drawdown']:.2%} | {coefficient} |")
    lines += ["", "## 解释边界", "",
              "- rules_*.json 导出真实叶子路径、原始量纲阈值、缺失填充值、基础拟合支持数、原始/校准后概率、同折验证表现。baseline 文件为重建的原候选第一折；pruned 文件只按结构选择（第一折叶数最少但大于1），没有按验证胜率挑叶子。",
              "- 标准化阈值反算为 threshold × scale + mean。行业 one-hot 分支表示某行业指示值；缺失指示值与原值含义不同。脚本逐行验证反算后的路径与树实际路由一致。",
              "- 叶子训练支持、训练目标命中率不能当作样本外胜率；验证叶子指标只是描述性观察，不表示独立交易或显著有效。",
              "- 信号按股票日统计，持有期会重叠；组合沿用原代码的周期调仓、次日开盘执行及成本模型。交易记录数不是完整交易次数。",
              "- 剪枝能减少分支，但预测损失和交易收益可能向不同方向变化；常数树可能减少误差但不再发现任何条件场景。",
              "- 本报告没有选择可上线的新模型。此次开发集反复查看和比较后，如需确认泛化，应冻结新方案并使用新的未揭盲时期/公司。",
              "", "剪枝方法：[scikit-learn 官方说明](https://scikit-learn.org/stable/auto_examples/tree/plot_cost_complexity_pruning.html)。"]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs/model_audit_20261003/tree_pruning")
    args = parser.parse_args()
    source = args.research_dir or next((ROOT / "outputs/task_runtime_eval/20261003T042736").rglob("20261003T042758Z_5370a71f"))
    source = source.resolve()
    output = args.output_dir.resolve()
    if output == source or source in output.parents:
        raise ValueError("Audit must write outside the original research directory")
    implementation = require_matching_code(source)
    manifest, spec = read_json(source / "manifest.json"), ResearchSpec.from_dict(read_json(source / "spec.json"))
    history = read_json(source / "development.json")
    panel = pd.read_pickle(source / "panel.pkl")
    fingerprint = hashlib.sha256(pd.util.hash_pandas_object(panel, index=True).values.tobytes()).hexdigest()
    if fingerprint != manifest["panel_sha256"]:
        raise ValueError("Frozen panel fingerprint mismatch")
    # Drop held-out companies and future bars before constructing any audit labels.
    test_start = pd.Timestamp(manifest["test_start"])
    panel = panel[(~panel.symbol.isin(manifest["heldout_companies"])) & (panel.date < test_start)].copy()
    output.mkdir(parents=True, exist_ok=True)
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "source_run_id": manifest["run_id"],
              "source_manifest_sha256": sha256(source / "manifest.json"), "source_panel_sha256": fingerprint,
              "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "script_sha256": sha256(Path(__file__)), "implementation_sha256": implementation,
              "environment": {"python": platform.python_version(), "pandas": pd.__version__, "sklearn": sklearn.__version__},
              "scope": {"development_companies": int(panel.symbol.nunique()), "development_panel_rows": len(panel),
                        "last_bar": panel.date.max(), "holdout_starts": test_start, "holdout_evaluations_read": False,
                        "original_model_modified": False, "historical_trees_reconstructed": True,
                        "alpha_source": "first fold base-fit partition only, fixed across both folds",
                        "selected_replacement": None}, "candidates": []}
    for historical in history["results"]:
        c = historical["candidate"]
        if c["model"] != "tree":
            continue
        data = labeled_panel(panel, c["horizon"])
        folds = [(train[sample_mask(train, c["sampler"], spec)], val[sample_mask(val, c["sampler"], spec)])
                 for train, val in temporal_folds(data, spec.folds)]
        bases = [fit_model(train, c, historical["columns"], spec) for train, _ in folds]
        alphas = alpha_grid(bases[0], folds[0][0], c, spec)
        candidate_result = {"candidate": c, "columns": historical["columns"], "alpha_grid": alphas,
                            "historical_reproduction": [], "variants": []}
        first_fold_models = []
        for alpha in alphas:
            variant = {"alpha": alpha, "folds": []}
            for index, ((train, val), base) in enumerate(zip(folds, bases)):
                model = prune_refit(base, train, c, spec, alpha)
                scored = val.copy()
                scored["prediction"] = model.predict(scored)
                baseline = float((train.forward_return > spec.target_return).mean()) if c["task"] == "classification" else float(train.forward_return.mean())
                metrics = prediction_metrics(scored, c["task"], spec, baseline)
                bt = backtest_predictions(scored, panel, c, spec)["metrics"]
                fold = {"train_end": train.label_end.max().isoformat(), "validation_start": val.date.min().isoformat(),
                        "validation_end": val.label_end.max().isoformat(), "training": model.training_counts,
                        "validation_rows": len(val), "prediction": metrics, "portfolio": bt, "tree": tree_info(model)}
                variant["folds"].append(fold)
                if alpha == 0:
                    differences = numeric_differences(historical["folds"][index], fold)
                    candidate_result["historical_reproduction"].append({"fold": index + 1, "matches": not differences, "differences": differences})
                    if differences:
                        raise AssertionError(f"Historical reproduction differs: {c['id']} fold {index + 1}: {differences}")
                if index == 0:
                    first_fold_models.append((alpha, model))
            skills = [fold["prediction"]["skill_vs_constant"] for fold in variant["folds"]]
            variant["score"] = float(np.mean(skills) - np.std(skills))
            variant["constraint_checks"] = constraint_checks(variant["folds"], spec)
            variant["meets_constraints"] = all(variant["constraint_checks"].values())
            candidate_result["variants"].append(variant)
        # Structural representative only: fewest leaves among nonconstant first-fold trees.
        choices = [(alpha, model) for alpha, model in first_fold_models if model.pipeline[-1].get_n_leaves() > 1]
        alpha, model = min(choices, key=lambda pair: (pair[1].pipeline[-1].get_n_leaves(), pair[0])) if choices else first_fold_models[0]
        fit, _, _ = training_partition(folds[0][0], c, spec)
        baseline_rules_path = output / f"rules_baseline_{c['id']}.json"
        write_json(baseline_rules_path, {"candidate": c, "fold": 1, "alpha": 0.0, "tree": tree_info(bases[0]),
                                        "selection_basis": "original candidate reconstructed on first fold",
                                        "rules": extract_rules(bases[0], fit, folds[0][1], spec)})
        rules_path = output / f"rules_pruned_{c['id']}.json"
        write_json(rules_path, {"candidate": c, "fold": 1, "alpha": alpha, "tree": tree_info(model),
                               "selection_basis": "minimum first-fold leaf count >1; no validation performance selection",
                               "rules": extract_rules(model, fit, folds[0][1], spec)})
        candidate_result["representative_rules"] = rules_path.name
        candidate_result["baseline_rules"] = baseline_rules_path.name
        report["candidates"].append(candidate_result)
        print(f"Reproduced {c['id']}; audited {len(alphas)} alpha values across {len(folds)} folds", flush=True)
    write_json(output / "audit.json", report)
    (output / "README.md").write_text(render_summary(report))
    print(output / "README.md", flush=True)


if __name__ == "__main__":
    main()
