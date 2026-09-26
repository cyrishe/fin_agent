from __future__ import annotations

from typing import Any, List, Mapping


def _trim(value: Any) -> str:
    return str(value or "").strip()


def _sentence(value: str) -> str:
    return value.rstrip("。！？!?；;，, ") + "。"


def _design_has_substance(design: Mapping[str, Any]) -> bool:
    if _trim(design.get("document")):
        return True
    if _trim(design.get("plan")):
        return True
    flow = design.get("flow") if isinstance(design.get("flow"), Mapping) else {}
    collections = (
        design.get("inputs"),
        design.get("outputs"),
        design.get("modules"),
        design.get("rules"),
        design.get("logic"),
        flow.get("steps"),
        design.get("data_requirements"),
        design.get("acceptance"),
    )
    return any(isinstance(items, list) and bool(items) for items in collections)


def compose_design_narrative(
    understanding: Mapping[str, Any],
    questions: List[Any],
    design: Mapping[str, Any],
) -> str:
    """Turn an existing Design snapshot into conversational Surface copy.

    This is a presentation adapter only. It does not infer or mutate business facts.
    """
    goal = _trim(understanding.get("goal")) or "创建一个新的金融工具"
    expected = _trim(understanding.get("expected_result"))
    confirmed = [
        _trim(item)
        for item in understanding.get("confirmed_requirements") or []
        if _trim(item)
    ]
    question_count = len(questions)

    if question_count and not _design_has_substance(design):
        parts = [f"明白，我们先从“{goal.rstrip('。')}”这个方向开始。"]
        parts.append("这个方向目前还比较宽，我先不替你假定具体规则、数据范围或返回形式。")
        parts.append(
            f"下面先保留已经明确的目标，再确认 {question_count} 个真正决定工具形态的问题。"
        )
        return "\n\n".join(parts)

    parts = [f"按我的理解，这个工具要解决的是“{goal.rstrip('。')}”。"]
    if expected and expected.rstrip("。") != goal.rstrip("。"):
        parts.append(f"最终结果会是“{expected.rstrip('。')}”。")
    if confirmed:
        facts = "；".join(item.rstrip("。；; ") for item in confirmed[:2])
        parts.append(f"你已经明确了这些要求：{_sentence(facts)}")
    if question_count:
        parts.append(
            f"我先按这些信息整理了核心路径，最后还有 {question_count} 个会影响结果的关键点需要一起确认。"
        )
    else:
        parts.append("下面是按这个目标整理的核心计算和处理路径，你可以先看它是否就是你想做的。")
    return "\n\n".join(parts)
