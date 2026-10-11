"""The research compiler preserves user meaning and a shared execution envelope."""
import json

import pytest

from src.quant_research.automl.config import ResearchSpec
from src.quant_research.automl.planning import (
    UnsupportedResearchRequirement,
    compile_research,
    explicit_plan,
    normalize_plan,
)


def shared(**patch):
    return ResearchSpec(start="2024-01-01", end="2025-12-31", max_trials=6,
                        **patch).to_dict()


def test_natural_request_preserves_distinct_directions_and_original_requirement():
    requirement = "从低波动的价格收敛、行业估值两个角度研究，宁可漏选。只用3、7日持有期，总共6次实验。"
    observed = []

    def complete(system, payload):
        observed.append(payload)
        return json.dumps({
            "spec": {"start": "2024-01-01", "end": "2025-12-31", "max_trials": 6,
                     "horizons": [3, 7], "models": ["linear", "tree"],
                     "tasks": ["classification"], "min_precision": .75},
            "design": "分别检验价格收敛与行业估值，验证后冻结门槛，两个方向各保留结果。",
            "directions": [
                {"id": "model-should-not-own-this", "hypothesis": "低波动且均线附近是否产生更可靠的机会",
                 "spec": {"samplers": ["low_volatility"], "horizons": [3],
                          "feature_sets": ["technical"], "feature_names": ["volatility_20", "ma_distance"]}},
                {"hypothesis": "行业与估值组合是否对较长持有期有增益",
                 "spec": {"samplers": ["all"], "horizons": [7],
                          "feature_sets": ["enriched"], "feature_names": ["industry", "pe_ttm", "pb_mrq"]}},
            ], "unsupported_requirements": [],
        }, ensure_ascii=False)

    plan = compile_research(requirement, reference_time="2026-10-03T09:00:00+08:00", complete=complete)
    assert observed[0]["requirement_brief"] == requirement
    assert observed[0]["default_spec"]["max_symbols"] is None
    assert observed[0]["default_spec"]["optimize_threshold"] is True
    assert plan["requirement_brief"] == requirement
    assert [direction["id"] for direction in plan["directions"]] == ["direction_1", "direction_2"]
    assert [direction["spec"]["horizons"] for direction in plan["directions"]] == [(3,), (7,)]
    for direction in plan["directions"]:
        assert direction["spec"]["max_trials"] == 6  # Allocation belongs to the runner.
        assert direction["spec"]["min_precision"] == .75
        assert direction["spec"]["max_symbols"] is None
        assert requirement in direction["spec"]["objective"]
        assert direction["hypothesis"] in direction["spec"]["objective"]
    assert normalize_plan(plan) == plan


def test_legacy_compiler_and_saved_plan_remain_single_direction_compatible():
    plan = compile_research("用逻辑回归观察3日持有收益", reference_time="2026-10-03", complete=lambda *_: json.dumps({
        "spec": {"tasks": ["classification"], "models": ["linear"], "horizons": [3]},
        "design": "原有规划输出没有 directions 字段。",
    }))
    assert len(plan["directions"]) == 1
    assert plan["directions"][0]["spec"] == plan["spec"]
    assert normalize_plan(plan) == plan
    old_spec = shared(max_symbols=16, min_precision=None, optimize_threshold=False)
    old = {"spec": old_spec, "design": "旧运行设计", "requirement_brief": "用户完整要求", "reference_time": None}
    restored = normalize_plan(json.loads(json.dumps(old)))
    assert restored["directions"][0]["spec"] == old_spec
    assert restored["spec"]["max_symbols"] == 16
    assert restored["spec"]["min_precision"] is None
    assert normalize_plan(restored) == restored
    explicit = explicit_plan(old_spec, "用户完整要求")
    assert explicit["directions"][0]["spec"] == old_spec
    assert normalize_plan(explicit) == explicit


def test_local_direction_can_narrow_universe_but_not_get_a_new_budget():
    common = shared(max_symbols=None, company_holdout_fraction=0)
    plan = normalize_plan({"spec": common, "requirement_brief": "研究局部机会", "directions": [
        {"hypothesis": "单股的量价规律", "spec": {"symbols": ["600000.SH"], "max_symbols": 1,
                                                    "start": "2024-04-01", "max_trials": 100,
                                                    "rounds": 4, "max_train_rows": 90000}},
        {"hypothesis": "共同范围的机会", "spec": {}},
    ]})
    local = plan["directions"][0]["spec"]
    assert local["symbols"] == ("600000.SH",)
    assert local["company_holdout_fraction"] == 0
    assert (local["max_trials"], local["rounds"], local["max_train_rows"]) == (
        common["max_trials"], common["rounds"], common["max_train_rows"])


@pytest.mark.parametrize("patch,match", [
    ({"end": "2026-01-01"}, "日期范围"),
    ({"max_symbols": None}, "股票上限"),
    ({"models": ["svm"]}, "models"),
    ({"tasks": ["regression"]}, "tasks"),
    ({"horizons": [1]}, "horizons"),
    ({"min_precision": .5}, "min_precision"),
    ({"target_return": 0}, "target_return"),
    ({"industries": []}, "industries"),
    ({"commission_rate": 0}, "commission_rate"),
    ({"test_fraction": .1}, "test_fraction"),
    ({"shell_command": "do something"}, "shell_command"),
])
def test_direction_cannot_expand_frozen_execution_scope(patch, match):
    common = shared(max_symbols=30, models=("linear", "tree"), tasks=("classification",),
                    horizons=(3, 7), min_precision=.7, target_return=.01, industries=("银行",))
    with pytest.raises(UnsupportedResearchRequirement, match=match):
        normalize_plan({"spec": common, "directions": [{"hypothesis": "局部假设", "spec": patch}]})


@pytest.mark.parametrize("directions,match", [
    ([], "at least one"),
    ([{"hypothesis": "x"}] * 7, "budget"),
    ([{"spec": {}}], "hypothesis"),
    ([{"hypothesis": "x", "spec": "not an object"}], "spec"),
])
def test_unexecutable_direction_contracts_are_rejected(directions, match):
    with pytest.raises(ValueError, match=match):
        normalize_plan({"spec": shared(), "directions": directions})


def test_unsupported_target_is_not_converted_to_a_plausible_direction():
    with pytest.raises(UnsupportedResearchRequirement, match="尾盘分钟.*次日高开"):
        compile_research("按尾盘分钟预测次日高开", reference_time="2026-10-03", complete=lambda *_: json.dumps({
            "spec": {}, "design": "已有持有收益标签不能替代要求。",
            "directions": [{"hypothesis": "看起来相似", "spec": {"horizons": [1]}}],
            "unsupported_requirements": ["尾盘分钟区间和次日高开标签尚未实现"],
        }))


@pytest.mark.parametrize("stage", ["compile", "plan", "review"])
def test_all_soft_stages_receive_the_shared_execution_semantics(stage, tmp_path, monkeypatch):
    import src.quant_research.automl.advisor as advisor_module
    from src.quant_research.automl.advisor import ResearchAdvisor

    original = advisor_module.PROMPTS
    for name in ("compile.md", "plan.md", "review.md"):
        (tmp_path / name).write_text((original / name).read_text())
    # A shared edit must reach every stage, rather than copying definitions into
    # three prompts and letting the reviewer inherit an earlier hallucination.
    contract = (original / "data_contract.md").read_text()
    marker = "本次共享口径修订由同一文件提供。"
    (tmp_path / "data_contract.md").write_text(contract + "\n" + marker)
    monkeypatch.setattr(advisor_module, "PROMPTS", tmp_path)
    captured = []

    def complete(system, payload):
        captured.append((system, payload))
        if stage == "compile":
            return json.dumps({"spec": {}, "design": "根据共享口径研究量价条件。"})
        if stage == "plan":
            return json.dumps({"analysis": "按实际采样范围比较", "candidate_ids": ["available"]})
        return "依据实际筛选和保存的日期解释已有结果。"

    if stage == "compile":
        compile_research("用动量与反转角度研究", reference_time="2026-10-03", complete=complete)
    elif stage == "plan":
        ResearchAdvisor(complete).plan(ResearchSpec.from_dict(shared()), [{"id": "available"}], budget=1)
    else:
        # Historical prose may be wrong. The actual definitions must accompany
        # the result evidence, so the model can correct that prose at this stage.
        ResearchAdvisor(complete).review({"objective": "以前误写为近5日最低30%", "selected": {"sampler": "reversal"}})
    system, _ = captured[0]
    assert system.count(contract) == 1 and system.endswith(marker)
    assert "momentum：return_7 > 0 且 ma_distance > 0" in system
    assert "reversal：return_3 < -0.02" in system
    assert "不会隐式回退到 all" in system
    assert "floor(N*(1-test_fraction))" in system
    assert "不能按自然日跨度估算" in system


def test_default_reviewer_reasons_over_saved_prose_and_actual_contract(monkeypatch):
    from types import SimpleNamespace
    import src.quant_research.automl.advisor as advisor_module

    calls = []
    monkeypatch.setenv("LLM_BASE_URL", "https://model.invalid/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_DEFAULT_MODEL", "test-model")

    def post(url, **kwargs):
        calls.append(kwargs["json"])
        return SimpleNamespace(ok=True, json=lambda: {"choices": [{
            "finish_reason": "stop", "message": {"content": "按实际执行口径解释。"}}]})

    monkeypatch.setattr(advisor_module.requests, "post", post)
    evidence = {"objective": "旧计划把反转误写为近5日最低30%", "selected": {"sampler": "reversal"}}
    assert advisor_module.ResearchAdvisor().review(evidence) == "按实际执行口径解释。"
    assert calls[0]["enable_thinking"] is True
    assert "reversal：return_3 < -0.02" in calls[0]["messages"][0]["content"]
    assert json.loads(calls[0]["messages"][1]["content"]) == evidence


def test_soft_null_overrides_inherit_system_values_and_keep_nullable_meaning():
    plan = compile_research('自动选择门槛，按共同约定研究', reference_time='2026-10-03', complete=lambda *_: json.dumps({
        'spec': {'probability_threshold': None, 'min_win_rate': None, 'company_holdout_fraction': .25,
                 'max_symbols': None, 'min_precision': .7, 'commission_rate': .0008},
        'design': '未指定门槛初值继承默认，方向保留共同成本与留出。',
        'directions': [{'hypothesis': '量价条件', 'spec': {'probability_threshold': None,
                        'company_holdout_fraction': None, 'commission_rate': None, 'feature_names': None}}],
    }))
    assert plan['spec']['probability_threshold'] == .6
    assert plan['spec']['min_win_rate'] == .5
    direction = plan['directions'][0]['spec']
    assert direction['company_holdout_fraction'] == .25
    assert direction['commission_rate'] == .0008
    assert direction['min_precision'] == .7
    assert direction['max_symbols'] is None and direction['feature_names'] == ()
    assert normalize_plan(plan) == plan
    # Nullable values must not be stripped globally: a direction cannot clear a
    # real common minimum by passing null, nor invent a null executable parameter.
    with pytest.raises(UnsupportedResearchRequirement, match='min_precision'):
        normalize_plan({'spec': shared(min_precision=.7), 'directions': [
            {'hypothesis': '不能取消共同目标', 'spec': {'min_precision': None}}]})
    with pytest.raises(UnsupportedResearchRequirement, match='shell_command'):
        compile_research('x', complete=lambda *_: json.dumps({'spec': {'shell_command': None}, 'design': 'x'}))
    # Explicit machine configuration is not a SOFT patch and is never repaired silently.
    with pytest.raises((ValueError, TypeError)):
        explicit_plan({**shared(), 'probability_threshold': None})
