"""User-facing research facts remain faithful to saved measurements and their scope."""
from copy import deepcopy
import json

import pytest

from src.quant_research.automl.runner import model_display_name, render_report, review_evidence, review_research


@pytest.fixture
def report():
    def result(skill, signals, win_rate, trades):
        return {
            "prediction": {"rows": 100, "companies": 3, "brier": .25 * (1 - skill),
                           "baseline_brier": .25, "skill_vs_constant": skill,
                           "signal_count": signals, "signal_win_rate_after_cost": win_rate},
            "portfolio": {"total_return": .012 if trades else 0, "max_drawdown": -.023 if trades else 0,
                          "trade_count": trades},
            "benchmark": {"total_return": .04, "max_drawdown": -.05},
        }
    return {
        "objective": "历史窗口流程复验",
        "selected": {"model": "linear", "task": "classification", "horizon": 3, "depth": 6},
        "development_constraints_met": False,
        "development_constraint_checks": {"enough_signals": True, "each_fold_win_rate": False,
                                          "drawdown_within_limit": True, "each_fold_beats_constant": True},
        "attempted_trials": 12, "successful_trials": 11,
        "decision_policy": {"target_return": .01},
        "sample_counts": {"panel_rows": 7760, "companies": 16, "sessions": 485,
                          "development": {"input_rows": 4731, "fit_rows": 3497, "calibration_rows": 1182,
                                          "purged_rows": 52, "budget_sampled_out_rows": 0},
                          "test_start": "2025-08-11T00:00:00", "test_signal_end": "2025-12-19T00:00:00",
                          "heldout_companies": ["A", "B", "C"]},
        "evaluation": {"new_period": result(.0087362, 3, 2 / 3, 2),
                       "new_companies_and_period": result(-.0032649, 0, None, 0)},
        "limitations": ["信号持有区间可能重叠。"],
    }


def test_classification_report_keeps_opposite_loss_directions_and_no_signal_distinct(report):
    saved = deepcopy(report)
    text = render_report(report, "少量信号不足以确认稳定泛化。")
    assert "逻辑回归 · 持有 3 个交易日" in text
    assert "超过 1.00%的概率" in text
    assert "+0.87%（损失更低）" in text
    assert "-0.33%（损失更高）" in text
    assert "66.67%" in text and "无信号，不计算" in text
    assert "0（未成交）" in text and "1.20%" in text and "-2.30%" in text
    assert "每个时间验证折的扣成本信号胜率" in text
    assert "3,497" in text and "1,182" in text and "52" in text
    assert "未找到满足全部" in text and "新的盲测证据" in text
    assert "```json" not in text and "False" not in text and "'depth'" not in text
    assert "new_period" not in text and "new_companies_and_period" not in text
    assert report == saved


def test_regression_report_labels_rmse_and_mse_skill_without_probability_claim(report):
    report["selected"]["task"] = "regression"
    report["development_constraints_met"] = True
    report["development_constraint_checks"] = {}
    report["sample_counts"]["development"]["calibration_rows"] = 0
    for evidence in report["evaluation"].values():
        pred = evidence["prediction"]
        pred.pop("brier")
        pred.pop("baseline_brier")
        pred.update(rmse=.01, baseline_rmse=.02, skill_vs_constant=.75)
    text = render_report(report, "收益预测需要在新窗口验证。")
    assert "Ridge 收益回归" in text and "预测到期收益率" in text
    assert "表中展示 RMSE；相对改善按 MSE 计算" in text
    assert "| 0.010000 | 0.020000 | +75.00%（损失更低）" in text
    assert "收益回归不做概率校准" in text
    assert "满足全部预设筛选条件" in text and "Brier" not in text


@pytest.mark.parametrize("evaluation", [
    {},
    {"new_companies_and_period": {"unavailable": "no matching samples"}},
])
def test_report_preserves_missing_holdout_as_unavailable(report, evaluation):
    report["evaluation"] = evaluation
    saved = deepcopy(report)
    text = render_report(report, "留出证据尚不足。")
    assert "尚无可评估切片" in text
    assert ("无匹配样本，未评估" if evaluation else "尚无留出评估记录") in text
    assert "无信号，不计算" not in text  # Missing samples are not a measured zero-signal result.
    assert report == saved


def test_equal_loss_and_unrecorded_legacy_counts_are_not_improvements(report):
    report["sample_counts"] = {}
    report["evaluation"] = {"new_period": report["evaluation"]["new_period"]}
    report["evaluation"]["new_period"]["prediction"]["skill_vs_constant"] = 0
    text = render_report(report, "")
    assert "+0.00%（损失相同）" in text and "未记录" in text


def test_reviewer_receives_same_program_facts_without_altering_saved_evidence(report, tmp_path):
    (tmp_path / "selection.json").write_text(json.dumps({"columns": ["return_7"]}))
    (tmp_path / "development.json").write_text(json.dumps({"results": []}))
    saved = deepcopy(report)
    evidence = review_evidence(tmp_path, report)
    facts = evidence["review_context"]["fact_summary"]
    assert facts in render_report(report, "interpretation")
    assert "+0.87%（损失更低）" in facts and "-0.33%（损失更高）" in facts
    assert evidence["evaluation"] == saved["evaluation"] and report == saved


def test_archived_aggregate_report_without_model_remains_reviewable(report, tmp_path):
    (tmp_path / "report.json").write_text(json.dumps(report))
    (tmp_path / "selection.json").write_text(json.dumps({"columns": ["return_7"]}))
    (tmp_path / "development.json").write_text(json.dumps({"results": []}))

    class Advisor:
        def review(self, evidence):
            assert "model_explanation" not in evidence["review_context"]
            return "归档报告只有聚合指标，无法据此还原模型规则。"

    assert review_research(tmp_path, Advisor())["completed"]
    assert "无法据此还原模型规则" in (tmp_path / "report.md").read_text()


def test_legacy_report_distinguishes_uncapped_signals_from_capped_portfolio(report):
    report["decision_policy"].update(probability_threshold=.6, top_k=3)
    text = render_report(report, "")
    assert "信号统计包含全部过线样本" in text
    assert "组合执行时每次最多选 3 只" in text


@pytest.mark.parametrize("model,classification,regression", [
    ("linear", "逻辑回归", "Ridge 收益回归"),
    ("elastic_net", "ElasticNet 逻辑回归", "ElasticNet 收益回归"),
    ("tree", "决策树分类", "决策树回归"),
    ("forest", "随机森林分类", "随机森林回归"),
    ("svm", "支持向量分类（SVC）", "支持向量回归（SVR）"),
    ("hist_gradient_boosting", "直方图梯度提升分类", "直方图梯度提升回归"),
])
def test_algorithm_family_display_distinguishes_actual_prediction_task(model, classification, regression):
    assert model_display_name({"model": model, "task": "classification"}) == classification
    assert model_display_name({"model": model, "task": "regression"}) == regression
