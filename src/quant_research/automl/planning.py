"""Domain SOFT compiler: meaning becomes the small executable ResearchSpec contract."""
from __future__ import annotations

from dataclasses import fields
from datetime import date, datetime, timedelta
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from .advisor import ResearchAdvisor
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
    return ResearchSpec(start=start.isoformat(), end=end.isoformat(), max_trials=12, rounds=2)


def explicit_plan(value, requirement_brief=""):
    if not isinstance(value, dict):
        raise ValueError("spec must be an object")
    allowed = {field.name for field in fields(ResearchSpec)}
    unknown = set(value) - allowed
    if unknown:
        raise UnsupportedResearchRequirement("不支持的研究参数：" + ", ".join(sorted(unknown)))
    spec = ResearchSpec.from_dict(value)
    design = (f"研究 {spec.start} 至 {spec.end} 的行情，股票上限 {spec.max_symbols}，持有周期 {list(spec.horizons)} 个交易日；"
              f"最多 {spec.max_trials} 个实验，{spec.folds} 个时间验证折。先冻结公司与未来时间留出集，"
              "仅用开发集比较候选，再做最终训练、留出回测及报告。")
    if requirement_brief:
        design += " 本次采用显式结构化配置，说明文字不另行改写执行参数。"
    return {"spec": spec.to_dict(), "design": design,
            "reference_time": None, "requirement_brief": requirement_brief or spec.objective}


def compile_research(requirement_brief, *, reference_time=None, complete=None):
    if not isinstance(requirement_brief, str) or not requirement_brief.strip():
        raise ValueError("requirement_brief is required")
    defaults = default_spec(reference_time)
    system = Path(__file__).with_name("prompts").joinpath("compile.md").read_text()
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
    plan = explicit_plan({**defaults.to_dict(), **patch, "objective": requirement_brief})
    if date.fromisoformat(plan["spec"]["end"]) > date.fromisoformat(defaults.end):
        raise UnsupportedResearchRequirement("研究结束日期超过本次运行锚点之前已结束的日期。")
    design = result.get("design")
    if not isinstance(design, str) or not design.strip():
        raise ValueError("research planner must explain the design")
    plan.update(design=design, requirement_brief=requirement_brief, reference_time=str(reference_time or ""))
    return plan
