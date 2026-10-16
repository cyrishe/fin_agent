"""Domain SOFT compiler: meaning becomes the small executable ResearchSpec contract."""
from __future__ import annotations

from dataclasses import fields
from datetime import date, datetime, timedelta
import json
from zoneinfo import ZoneInfo

from .advisor import ResearchAdvisor, research_prompt
from .config import ResearchSpec


class UnsupportedResearchRequirement(ValueError):
    """The user must be told which requested execution semantics cannot be honored."""


def default_spec(reference_time=None):
    if reference_time:
        value = datetime.fromisoformat(str(reference_time).replace("Z", "+00:00"))
        day = value.astimezone(ZoneInfo("Asia/Shanghai")).date() if value.tzinfo else value.date()
    else:
        day = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    end = day - timedelta(days=1)
    start = end - timedelta(days=365 * 3)
    return ResearchSpec(start=start.isoformat(), end=end.isoformat(), max_trials=12, rounds=2,
                        max_symbols=None, min_precision=.6, optimize_threshold=True)


def _read_spec(value):
    if not isinstance(value, dict):
        raise ValueError("spec must be an object")
    allowed = {field.name for field in fields(ResearchSpec)}
    unknown = set(value) - allowed
    if unknown:
        raise UnsupportedResearchRequirement("不支持的研究参数：" + ", ".join(sorted(unknown)))
    return ResearchSpec.from_dict(value)


def _inherit_spec(base, patch):
    """SOFT patches inherit unspecified values; explicit configs remain authoritative.

    A model may express an omitted override as null. Only declared nullable
    parameters use null as an executable value; other nulls retain system facts.
    Unknown keys survive this merge so _read_spec can reject them normally.
    """
    nullable = {field.name for field in fields(ResearchSpec) if field.default is None}
    return {**base, **{key: value for key, value in patch.items()
                      if value is not None or key in nullable or key not in base}}


def normalize_plan(value):
    """Freeze executable direction envelopes; explanations remain natural language.

    A direction may narrow the shared research scope. The task owns budgets and
    evaluation conventions, so every direction cannot claim a fresh full budget.
    Old saved spec-only plans are a compatible single direction.
    """
    if not isinstance(value, dict):
        raise ValueError("research plan must be an object")
    common = _read_spec(value.get("spec")).to_dict()
    requirement = value.get("requirement_brief") or common["objective"]
    if not isinstance(requirement, str):
        raise ValueError("requirement_brief must be text")
    raw_directions = value.get("directions")
    if raw_directions is None:
        # Preserve the legacy execution spec exactly, including its objective.
        return {**value, "spec": common, "requirement_brief": requirement, "directions": [
            {"id": "direction_1", "hypothesis": value.get("design") or requirement, "spec": common.copy()}]}
    if not isinstance(raw_directions, list) or not raw_directions:
        raise ValueError("directions must contain at least one research direction")
    if len(raw_directions) > common["max_trials"]:
        raise ValueError("research directions exceed the shared experiment budget")
    directions = []
    for index, direction in enumerate(raw_directions, start=1):
        if not isinstance(direction, dict):
            raise ValueError("research direction must be an object")
        hypothesis = direction.get("hypothesis")
        if not isinstance(hypothesis, str) or not hypothesis.strip():
            raise ValueError("research direction must explain its hypothesis")
        patch = direction.get("spec", {})
        if not isinstance(patch, dict):
            raise ValueError("direction spec must be an object")
        merged = _read_spec(_inherit_spec(common, patch)).to_dict()
        for key in ("max_trials", "rounds", "max_train_rows"):
            merged[key] = common[key]
        for key in ("seed", "folds", "test_fraction", "company_holdout_fraction",
                    "commission_rate", "sell_tax_rate", "slippage_rate", "initial_cash"):
            if merged[key] != common[key]:
                raise UnsupportedResearchRequirement(f"研究方向不能改写共同执行约定：{key}")
        if (date.fromisoformat(merged["start"]) < date.fromisoformat(common["start"])
                or date.fromisoformat(merged["end"]) > date.fromisoformat(common["end"])):
            raise UnsupportedResearchRequirement("研究方向的日期范围不能扩大共同研究范围")
        if common["max_symbols"] is not None and (merged["max_symbols"] is None or merged["max_symbols"] > common["max_symbols"]):
            raise UnsupportedResearchRequirement("研究方向不能扩大共同股票上限")
        for key in ("symbols", "industries", "models", "tasks", "horizons", "feature_names"):
            if common.get(key) and (not merged.get(key) or not set(merged[key]) <= set(common[key])):
                raise UnsupportedResearchRequirement(f"研究方向不能扩大共同研究范围：{key}")
        for key in ("target_return", "min_precision", "min_market_cap", "min_amount"):
            if common.get(key) is not None and (merged.get(key) is None or merged[key] < common[key]):
                raise UnsupportedResearchRequirement(f"研究方向不能降低共同门槛：{key}")
        if common["max_market_cap"] is not None and (merged["max_market_cap"] is None or merged["max_market_cap"] > common["max_market_cap"]):
            raise UnsupportedResearchRequirement("研究方向不能扩大共同上限：max_market_cap")
        # The model contributes its hypothesis; the system retains the original
        # user meaning and assigns identifiers without asking the model to copy it.
        if patch.get("objective") != common["objective"]:
            merged["objective"] = requirement + "\n\n本研究方向：" + hypothesis.strip()
        directions.append({"id": f"direction_{index}", "hypothesis": hypothesis.strip(), "spec": merged})
    return {**value, "spec": common, "requirement_brief": requirement, "directions": directions}


def explicit_plan(value, requirement_brief=""):
    spec = _read_spec(value)
    universe = "使用符合范围的全股票池" if spec.max_symbols is None else f"股票上限 {spec.max_symbols}"
    design = (f"研究 {spec.start} 至 {spec.end} 的行情，{universe}，持有周期 {list(spec.horizons)} 个交易日；"
              f"最多 {spec.max_trials} 个实验，{spec.folds} 个时间验证折。先冻结公司与未来时间留出集，"
              "仅用开发集比较候选，再做最终训练、留出回测及报告。")
    if requirement_brief:
        design += " 本次采用显式结构化配置，说明文字不另行改写执行参数。"
    return normalize_plan({"spec": spec.to_dict(), "design": design,
            "reference_time": None, "requirement_brief": requirement_brief or spec.objective})


def compile_research(requirement_brief, *, reference_time=None, complete=None):
    if not isinstance(requirement_brief, str) or not requirement_brief.strip():
        raise ValueError("requirement_brief is required")
    defaults = default_spec(reference_time)
    system = research_prompt("compile.md")
    payload = {
        "requirement_brief": requirement_brief,
        "reference_time": str(reference_time or datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()),
        "default_spec": defaults.to_dict(),
    }
    # Comparing the requested label/timing to executable capabilities needs reasoning.
    text = complete(system, payload) if complete else ResearchAdvisor._complete(system, payload, enable_thinking=True)
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
    result = json.loads(cleaned)
    if not isinstance(result, dict):
        raise ValueError("research planner must return an object")
    unsupported = result.get("unsupported_requirements", [])
    if not isinstance(unsupported, list) or any(not isinstance(item, str) for item in unsupported):
        raise ValueError("unsupported_requirements must be a list of explanations")
    if unsupported:
        raise UnsupportedResearchRequirement("暂不能执行以下明确要求：" + "；".join(unsupported))
    patch = result.get("spec")
    if not isinstance(patch, dict):
        raise ValueError("research planner must provide spec")
    # Explicit execution keys must never disappear through ResearchSpec's metadata compatibility.
    plan = explicit_plan({**_inherit_spec(defaults.to_dict(), patch), "objective": requirement_brief})
    if date.fromisoformat(plan["spec"]["end"]) > date.fromisoformat(defaults.end):
        raise UnsupportedResearchRequirement("研究结束日期超过本次运行锚点之前已结束的日期。")
    design = result.get("design")
    if not isinstance(design, str) or not design.strip():
        raise ValueError("research planner must explain the design")
    plan.update(design=design, requirement_brief=requirement_brief, reference_time=str(reference_time or ""))
    # Omission is legacy single-direction output; an explicitly empty direction
    # list has no executable work and is rejected by normalization.
    plan.pop("directions", None)
    if "directions" in result:
        plan["directions"] = result["directions"]
    return normalize_plan(plan)
