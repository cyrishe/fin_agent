from __future__ import annotations

from typing import Any, Dict


RESEARCH_MODES = frozenset({"fast", "auto", "deep"})

_MODE_LABELS = {
    "fast": "快速回答",
    "auto": "智能分析",
    "deep": "深度研究",
}


def normalize_research_mode(value: Any) -> str:
    """Normalize the optional per-turn research preference.

    The request field is intentionally small and stable: ``fast`` and ``deep``
    are explicit user constraints, while ``auto`` delegates the effective
    evidence depth to the selected business Skill. Older clients omit the field
    and therefore retain the default intelligent behavior.
    """

    normalized = str(value or "").strip().lower() or "auto"
    if normalized not in RESEARCH_MODES:
        raise ValueError("research_mode 仅支持 fast、auto 或 deep")
    return normalized


def research_mode_metadata(value: Any) -> Dict[str, str]:
    mode = normalize_research_mode(value)
    return {
        "requested": mode,
        "label": _MODE_LABELS[mode],
        "decision_owner": "skill" if mode == "auto" else "user",
    }


def research_mode_prompt(value: Any) -> str:
    mode = normalize_research_mode(value)
    if mode == "fast":
        return "\n".join(
            [
                "用户通过界面明确选择了“快速回答”。这是本轮显式约束。",
                "若问题需要个股研究，聚焦一个核心判断和二至三个会改变结论的证据目标；已有证据足够时立即综合，不展开完整报告。",
                "不要因为问题文本出现“深度、全面、研究”等词自行升级；若文本与界面选择冲突，以本界面选择为准。",
            ]
        )
    if mode == "deep":
        return "\n".join(
            [
                "用户通过界面明确选择了“深度研究”。这是本轮显式约束。",
                "若问题涉及一只或少量股票，应加载个股研究 Skill，先形成最小充分证据计划，再围绕重要矛盾、反证、估值或预期和验证点渐进深化。",
                "深度研究表示更完整的取证与交付，不表示机械填满模板，也不允许为增加篇幅重复查询。若文本与界面选择冲突，以本界面选择为准。",
            ]
        )
    return "\n".join(
        [
            "用户选择了默认的“智能分析”，没有固定本轮研究深度。",
            "不要新增独立分类轮次；由匹配的业务 Skill 结合用户完整语义、当前上下文和首批最小证据决定有效深度。用户明确要求深度分析、完整研究报告或可独立阅读的 PDF 报告时，应把它作为交付要求；这不是根据孤立关键词机械分类。",
            "没有明确交付要求时，再根据决策强度、重要异动或事件、经营/预期拐点、证据冲突和关键缺口决定是否深化；没有强信号时采用标准研究。热点、单日异动或单个形容词本身不自动扩张为完整深度报告。",
        ]
    )
