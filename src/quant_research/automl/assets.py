"""Reusable strategy descriptions derived from trusted, locally fitted models.

The executable model remains local. Exported explanations contain fitted model
parameters and aggregate evidence, never training rows or support vectors.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import joblib
import numpy as np

from .config import ResearchSpec


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def resolve_strategy_directory(directory, strategy_id=None):
    """A study is a collection, not an instruction to silently pick its first model."""
    root = Path(directory).resolve()
    if (root / "study.json").exists():
        choices = read_json(root / "study.json").get("strategies", [])
        if strategy_id is None:
            raise ValueError("strategy_id is required when scoring a multi-strategy study")
        matches = [item for item in choices if strategy_id in (item.get("id"), item.get("strategy_id"))]
        if len(matches) != 1:
            raise ValueError("strategy_id does not identify one strategy in this study")
        if not matches[0].get("research_dir"):
            raise ValueError("this strategy has no saved research model")
        child = (root / matches[0]["research_dir"]).resolve()
        if not child.is_relative_to(root) or child == root:
            raise ValueError("strategy research_dir must stay inside its study directory")
        return child
    if strategy_id is not None:
        saved_id = read_json(root / "strategy.json").get("strategy_id") if (root / "strategy.json").exists() else root.name
        if strategy_id != saved_id:
            raise ValueError("strategy_id does not match this strategy")
    return root


def frozen_policy(selection, spec):
    if selection.get("frozen_policy") is not None:
        return selection["frozen_policy"]
    candidate = selection["candidate"]
    return {"threshold": spec.probability_threshold if candidate["task"] == "classification" else spec.regression_threshold,
            "top_k": spec.top_k, "task": candidate["task"], "target_return": spec.target_return,
            "selection_source": "configured_fixed_threshold"}


def _feature_descriptions(model):
    transform = model.pipeline[0]
    names = list(transform.get_feature_names_out())
    descriptions = []
    numeric = transform.named_transformers_.get("numeric")
    numeric_count = 0
    if numeric is not None:
        raw_names = [name for name in model.columns if name != "industry"]
        imputer, scaler = numeric.named_steps["simpleimputer"], numeric.named_steps["standardscaler"]
        expanded = imputer.get_feature_names_out(raw_names)
        numeric_count = len(expanded)
        for index, feature in enumerate(expanded):
            item = {"transformed_feature": str(names[index]), "feature": str(feature),
                    "transform": "(imputed_value - mean) / scale", "mean": float(scaler.mean_[index]),
                    "scale": float(scaler.scale_[index])}
            if feature in raw_names:
                item["missing_imputed_as"] = float(imputer.statistics_[raw_names.index(feature)])
            else:
                item["meaning"] = "Missing-value indicator: 1 means the original feature was absent."
            descriptions.append(item)
    for name in names[numeric_count:]:
        descriptions.append({"transformed_feature": str(name), "feature": str(name), "transform": "one_hot",
                             "meaning": "1 means this category matches; unseen categories map to all zero indicators."})
    return descriptions


def explain_model(model, policy):
    """Export structural explanations without using holdout outcomes or an LLM."""
    estimator = model.pipeline[-1]
    features = _feature_descriptions(model)
    calibration = None
    if model.calibrator is not None:
        slope = float(model.calibrator.coef_[0, 0])
        calibration = {"slope": slope, "intercept": float(model.calibrator.intercept_[0]),
                       "formula": "sigmoid(slope * raw_score + intercept)",
                       "raw_score": "decision_function" if hasattr(model.pipeline, "decision_function") else "logit(clip(base_probability, 1e-5, 1-1e-5))",
                       "ordering": "reversed" if slope < 0 else "preserved" if slope > 0 else "constant",
                       "note": "Negative calibration slopes reverse the base model's score direction."}
    explanation = {"estimator": type(estimator).__name__, "task": model.task,
                   "raw_features": list(model.columns), "features": features, "calibration": calibration,
                   "interpretation": "Model associations, not causal effects or independently verified trading rules."}
    if hasattr(estimator, "coef_"):
        coefficients = np.asarray(estimator.coef_).reshape(-1)
        intercept = float(np.asarray(estimator.intercept_).reshape(-1)[0])
        explanation.update(method="linear_coefficients", base_intercept=intercept,
                           coefficient_unit="log_odds" if model.task == "classification" else "return")
        explanation["coefficients"] = [
            {"transformed_feature": feature["transformed_feature"], "base_coefficient": float(coefficient),
             **({"calibrated_log_odds_coefficient": float(coefficient * calibration["slope"])} if calibration else {})}
            for feature, coefficient in zip(features, coefficients)
        ]
        if calibration:
            explanation["calibrated_intercept"] = intercept * calibration["slope"] + calibration["intercept"]
        explanation["interpretation"] += " Numeric coefficients refer to standardized features; one-hot coefficients refer to indicator changes. Log-odds are not percentage-point probability effects."
    elif hasattr(estimator, "tree_"):
        tree = estimator.tree_
        rules = []

        def walk(node, conditions):
            left, right = int(tree.children_left[node]), int(tree.children_right[node])
            if left == right:
                rule = {"leaf": node, "conditions": conditions, "fit_support": int(tree.n_node_samples[node])}
                if model.task == "classification":
                    positive_index = list(estimator.classes_).index(1)
                    probabilities = tree.value[node, 0] / tree.value[node, 0].sum()
                    raw_probability = float(probabilities[positive_index])
                    clipped = np.clip(raw_probability, 1e-5, 1 - 1e-5)
                    raw_score = np.log(clipped / (1 - clipped))
                    value = float(model.calibrator.predict_proba([[raw_score]])[0, 1]) if model.calibrator is not None else raw_probability
                    rule.update(raw_leaf_probability=raw_probability, calibrated_probability=value)
                else:
                    value = float(tree.value[node, 0, 0])
                    rule["predicted_return"] = value
                rule["passes_threshold"] = bool(value >= policy["threshold"])
                rules.append(rule)
                return
            index = int(tree.feature[node])
            feature = features[index]
            threshold = float(tree.threshold[node])
            condition = {**feature, "transformed_threshold": threshold,
                         "original_threshold": threshold * feature["scale"] + feature["mean"] if "scale" in feature else threshold}
            walk(left, conditions + [{**condition, "operator": "<="}])
            walk(right, conditions + [{**condition, "operator": ">"}])

        walk(0, [])
        explanation.update(method="tree_paths", depth=int(estimator.get_depth()), leaves=int(estimator.get_n_leaves()), rules=rules)
        explanation["interpretation"] += " Leaf support is training support, not holdout precision. Passing a leaf threshold is necessary but daily top_k may further exclude a stock."
    else:
        explanation["method"] = "bounded_model_summary"
        if hasattr(estimator, "feature_importances_"):
            explanation["impurity_importance"] = [
                {"transformed_feature": feature["transformed_feature"], "importance": float(value)}
                for feature, value in zip(features, estimator.feature_importances_)
            ]
            explanation["interpretation"] += " Impurity importance is an aggregate training diagnostic; it has no direction, is biased by feature cardinality, and is not an exact decision rule."
        if hasattr(estimator, "n_support_"):
            explanation["support_vector_counts"] = estimator.n_support_.tolist()
        explanation["interpretation"] += " This model has no exported compact exact rule or additive coefficient explanation. Support vectors and training rows are not exported."
    return explanation


def _render_explanation(explanation):
    lines = ["# 策略模型解释", "", f"模型：{explanation['estimator']}；任务：{explanation['task']}。", "",
             explanation["interpretation"], ""]
    if explanation["calibration"]:
        calibration = explanation["calibration"]
        lines += [f"概率校准：sigmoid({calibration['slope']:.6g} × 原始分数 + {calibration['intercept']:.6g})；排序方向：{calibration['ordering']}。", ""]
    if "coefficients" in explanation:
        lines += ["| 模型输入 | 基础系数 | 校准后 log-odds 系数 |", "|---|---:|---:|"]
        for coefficient in explanation["coefficients"]:
            calibrated = coefficient.get("calibrated_log_odds_coefficient")
            lines.append(f"| {coefficient['transformed_feature']} | {coefficient['base_coefficient']:.6g} | {calibrated if calibrated is not None else '不适用'} |")
    if "rules" in explanation:
        for rule in explanation["rules"]:
            conditions = " 且 ".join(f"{c['feature']} {c['operator']} {c['original_threshold']:.6g}" for c in rule["conditions"]) or "全部输入（常数树）"
            value = rule.get("calibrated_probability", rule.get("predicted_return"))
            lines += [f"- 叶子 {rule['leaf']}：{conditions}。预测值 {value:.6g}；拟合支持 {rule['fit_support']}；达到阈值：{rule['passes_threshold']}。"]
    lines += ["", "预处理的均值、缩放、缺失填充值、行业编码和完整规则见 explanation.json；解释未声称进行新剪枝或获得额外样本外验证。", ""]
    return "\n".join(lines)


def export_strategy(root, hypothesis=""):
    """Materialize a reusable public description from a trusted local run directory."""
    root = Path(root)
    spec = ResearchSpec.from_dict(read_json(root / "spec.json"))
    selection, report, manifest = (read_json(root / name) for name in ("selection.json", "report.json", "manifest.json"))
    model = joblib.load(root / "model.joblib")
    policy = frozen_policy(selection, spec)
    explanation = explain_model(model, policy)
    source = manifest.get("source", {})
    counts = report.get("sample_counts", {})
    estimator = model.pipeline[-1]
    parameters = {key: value for key, value in estimator.get_params(deep=False).items()
                  if value is None or isinstance(value, (str, bool, int, float))}
    asset = {
        "strategy_id": report.get("run_id", root.name), "hypothesis": hypothesis or spec.objective,
        "candidate": selection["candidate"], "features": list(model.columns),
        "method": {"estimator": type(estimator).__name__, "parameters": parameters, "explanation_method": explanation["method"]},
        "scope": {"start": spec.start, "end": spec.end, "symbols": list(spec.symbols), "industries": list(spec.industries),
                  "min_market_cap": spec.min_market_cap, "max_market_cap": spec.max_market_cap, "min_amount": spec.min_amount,
                  "sampler": selection["candidate"]["sampler"], "sample_counts": counts,
                  "source": {key: source[key] for key in ("source", "symbols", "price_basis", "universe", "warnings") if key in source}},
        "target": {"definition": "next_open_hold_return", "horizon": selection["candidate"]["horizon"],
                   "return_formula": "adjusted_open[t+h+1] / adjusted_open[t+1] - 1",
                   "positive_event": "forward_return > target_return", "target_return": spec.target_return},
        "decision_policy": policy,
        "evidence": {"development_constraints_met": report.get("development_constraints_met"),
                     "development_constraint_checks": report.get("development_constraint_checks", {}),
                     "evaluation": report.get("evaluation", {}), "limitations": report.get("limitations", [])},
        "provenance": {key: manifest[key] for key in ("run_id", "git_commit", "working_tree_dirty", "implementation_sha256", "panel_sha256", "test_start", "created_at") if key in manifest},
        "artifacts": {"executable_model": "model.joblib", "model_sha256": hashlib.sha256((root / "model.joblib").read_bytes()).hexdigest(),
                      "spec": "spec.json", "selection": "selection.json", "explanation": "explanation.json", "report": "report.json"},
        "reuse_note": "Score with the saved preprocessing, calibrated model and frozen policy. Interpret its predictions with the recorded research scope, holdout evidence and limitations.",
    }
    for name, value in (("strategy.json", asset), ("explanation.json", explanation)):
        (root / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (root / "explanation.md").write_text(_render_explanation(explanation), encoding="utf-8")
    return asset
