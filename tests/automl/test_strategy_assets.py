import json

import joblib
import numpy as np
import pandas as pd
import pytest

from src.quant_research.automl.assets import export_strategy, resolve_strategy_directory
from src.quant_research.automl.config import ResearchSpec
from src.quant_research.automl.demo import synthetic_market
from src.quant_research.automl.features import build_panel, labeled_panel
from src.quant_research.automl.inference import predict_latest, predict_strategy
from src.quant_research.automl.learning import fit_model
from src.quant_research.automl.runner import review_research


@pytest.fixture
def saved_run(tmp_path):
    def create(name="linear", task="classification", policy=None, symbols=()):
        root = tmp_path / f"{name}_{task}"
        root.mkdir()
        daily, events = synthetic_market(companies=3, sessions=100)
        daily.loc[np.arange(len(daily)) % 5 == 0, "turn_ratio"] = np.nan
        frame = labeled_panel(build_panel(daily, events), 1)
        candidate = {"id": "candidate-a", "model": name, "task": task, "horizon": 1,
                     "sampler": "all", "features": "enriched", "depth": 3}
        spec = ResearchSpec(start="2023-01-01", end="2024-01-01", symbols=symbols, top_k=3)
        model = fit_model(frame, candidate, ["return_1", "return_7", "turn_ratio", "industry"], spec)
        selection = {"candidate": candidate, "columns": model.columns, "meets_constraints": False}
        if policy is not None:
            selection["frozen_policy"] = policy
        payloads = {
            "spec.json": spec.to_dict(), "selection.json": selection,
            "report.json": {"run_id": root.name, "sample_counts": {"panel_rows": len(daily), "companies": 3},
                            "development_constraints_met": False,
                            "evaluation": {"new_period": {"prediction": {"signal_count": 0, "target_precision": None}}},
                            "limitations": ["Synthetic pipeline test, not investment evidence."]},
            "manifest.json": {"run_id": root.name, "source": {"source": "synthetic", "symbols": daily.symbol.unique().tolist(),
                              "private_database_url": "must-not-export", "tables": {"private": "schema"}},
                              "panel_sha256": "test-panel-fingerprint", "git_commit": "test-commit"},
        }
        for filename, payload in payloads.items():
            (root / filename).write_text(json.dumps(payload))
        joblib.dump(model, root / "model.joblib")
        return root, model, daily, events, frame
    return create


@pytest.mark.parametrize("slope", [.8, -.5, 0])
def test_linear_export_exactly_reconstructs_calibrated_predictions(saved_run, slope):
    root, model, _, _, frame = saved_run()
    model.calibrator.coef_[0, 0] = slope
    joblib.dump(model, root / "model.joblib")
    asset = export_strategy(root, "用户希望观察低波动股票的日线模式")
    explanation = json.loads((root / "explanation.json").read_text())
    coefficients = np.array([row["calibrated_log_odds_coefficient"] for row in explanation["coefficients"]])
    transformed = model.pipeline[0].transform(frame[model.columns])
    log_odds = transformed @ coefficients + explanation["calibrated_intercept"]
    np.testing.assert_allclose(1 / (1 + np.exp(-log_odds)), model.predict(frame), atol=1e-12)
    assert explanation["calibration"]["ordering"] == ("reversed" if slope < 0 else "preserved" if slope > 0 else "constant")
    assert any(feature["transform"] == "one_hot" for feature in explanation["features"])
    assert any("Missing-value indicator" in feature.get("meaning", "") for feature in explanation["features"])
    assert asset["hypothesis"] == "用户希望观察低波动股票的日线模式"
    assert asset["evidence"]["development_constraints_met"] is False
    exported = (root / "strategy.json").read_text()
    assert str(root) not in exported and "must-not-export" not in exported and "private_database_url" not in exported
    assert asset["artifacts"]["executable_model"] == "model.joblib"
    assert len(asset["artifacts"]["model_sha256"]) == 64


@pytest.mark.parametrize("task", ["classification", "regression"])
def test_exported_tree_paths_reproduce_actual_leaf_predictions(saved_run, task):
    root, model, _, _, frame = saved_run("tree", task)
    export_strategy(root)
    explanation = json.loads((root / "explanation.json").read_text())
    transform, tree = model.pipeline[0], model.pipeline[-1]
    transformed = transform.transform(frame[model.columns])
    names = list(transform.get_feature_names_out())
    actual_leaves = tree.apply(transformed)
    actual_prediction = model.predict(frame)
    covered = np.zeros(len(frame), dtype=int)
    for rule in explanation["rules"]:
        mask = np.ones(len(frame), dtype=bool)
        for condition in rule["conditions"]:
            values = transformed[:, names.index(condition["transformed_feature"])]
            if "scale" in condition:
                values = values * condition["scale"] + condition["mean"]
            threshold = condition["original_threshold"]
            mask &= values <= threshold if condition["operator"] == "<=" else values > threshold
        np.testing.assert_array_equal(mask, actual_leaves == rule["leaf"])
        value = rule.get("calibrated_probability", rule.get("predicted_return"))
        np.testing.assert_allclose(actual_prediction[mask], value, atol=1e-12)
        covered += mask
    assert np.all(covered == 1)
    assert "training support, not holdout precision" in explanation["interpretation"]


@pytest.mark.parametrize("name", ["forest", "svm", "hist_gradient_boosting"])
def test_complex_model_export_does_not_invent_rules(saved_run, name):
    root, _, _, _, _ = saved_run(name)
    export_strategy(root)
    explanation = json.loads((root / "explanation.json").read_text())
    assert explanation["method"] == "bounded_model_summary"
    assert "rules" not in explanation and "coefficients" not in explanation and "support_vectors" not in explanation
    if name == "forest":
        assert explanation["impurity_importance"]
    if name == "svm":
        assert explanation["support_vector_counts"]


def test_prediction_reuses_frozen_policy_and_audits_nonselected_scores(saved_run):
    root, _, daily, events, _ = saved_run(policy={"threshold": 0., "top_k": 1})
    result = predict_latest(root, daily, events)
    assert len(result) == 3
    assert result.selected.sum() == 1 and result.iloc[0].selected
    assert result.prediction.is_monotonic_decreasing
    selection = json.loads((root / "selection.json").read_text())
    selection["frozen_policy"]["threshold"] = 1.0
    (root / "selection.json").write_text(json.dumps(selection))
    assert not predict_strategy(root, daily, events).selected.any()


def test_explicit_stock_scope_is_preserved_at_reuse(saved_run):
    root, _, daily, events, _ = saved_run(symbols=("000001.SZ",), policy={"threshold": 0., "top_k": 3})
    assert predict_latest(root, daily, events).symbol.tolist() == ["000001.SZ"]


def test_study_requires_explicit_strategy_choice_and_cannot_escape_directory(saved_run):
    root, _, daily, events, _ = saved_run(policy={"threshold": 0., "top_k": 1})
    study = root.parent
    (study / "study.json").write_text(json.dumps({"strategies": [{"id": "user-angle", "strategy_id": "saved-strategy", "research_dir": root.name}]}))
    with pytest.raises(ValueError, match="strategy_id is required"):
        predict_strategy(study, daily, events)
    expected = predict_latest(root, daily, events)
    actual = predict_strategy(study, daily, events, strategy_id="user-angle")
    pd.testing.assert_frame_equal(expected, actual)
    pd.testing.assert_frame_equal(expected, predict_strategy(study, daily, events, strategy_id="saved-strategy"))
    with pytest.raises(ValueError, match="does not identify"):
        predict_strategy(study, daily, events, strategy_id="missing")
    (study / "study.json").write_text(json.dumps({"strategies": [{"id": "failed"}]}))
    with pytest.raises(ValueError, match="no saved research model"):
        resolve_strategy_directory(study, "failed")
    (study / "study.json").write_text(json.dumps({"strategies": [{"id": "outside", "research_dir": "../elsewhere"}]}))
    with pytest.raises(ValueError, match="inside its study"):
        resolve_strategy_directory(study, "outside")


def test_empty_latest_scope_has_stable_audit_columns(saved_run):
    root, _, daily, events, _ = saved_run(symbols=("not-in-market",))
    result = predict_latest(root, daily, events)
    assert result.empty
    assert set(("symbol", "date", "prediction", "selected")) <= set(result)
    assert result.selected.dtype == bool


def test_first_language_review_receives_real_calibrated_explanation_and_preserves_hypothesis(saved_run):
    root, model, _, _, _ = saved_run()
    model.calibrator.coef_[0, 0] = -.5
    joblib.dump(model, root / "model.joblib")
    report = json.loads((root / "report.json").read_text())
    selection = json.loads((root / "selection.json").read_text())
    report.update(selected=selection["candidate"], objective="研究低波动下的选股角度", attempted_trials=1,
                  successful_trials=1, decision_policy={"target_return": 0., "probability_threshold": .6, "top_k": 3})
    (root / "report.json").write_text(json.dumps(report))
    (root / "development.json").write_text(json.dumps({"results": []}))
    payloads = []

    class Advisor:
        def review(self, evidence):
            assert (root / "explanation.json").exists()
            explanation = evidence["review_context"]["model_explanation"]
            assert explanation["calibration"]["ordering"] == "reversed"
            assert explanation["coefficients"]
            for row in explanation["coefficients"]:
                assert row["calibrated_log_odds_coefficient"] == pytest.approx(-.5 * row["base_coefficient"])
            payloads.append(evidence)
            return "校准后应按反向的系数组合解释这个选股维度，尚无额外样本外规则证据。"

    assert not (root / "explanation.json").exists()
    assert review_research(root, Advisor())["completed"]
    assert len(payloads) == 1
    assert "校准后应按反向" in (root / "report.md").read_text()
    # A later study-level title should survive a user-requested language review retry.
    asset = json.loads((root / "strategy.json").read_text())
    asset["hypothesis"] = "用户确认的低波动研究角度"
    (root / "strategy.json").write_text(json.dumps(asset))
    assert review_research(root, Advisor())["completed"]
    assert json.loads((root / "strategy.json").read_text())["hypothesis"] == "用户确认的低波动研究角度"
