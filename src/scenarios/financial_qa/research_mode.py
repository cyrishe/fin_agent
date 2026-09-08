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
                "围绕核心问题取得最小充分证据，简洁回答；本轮深度以界面选择为准。",
            ]
        )
    if mode == "deep":
        return "\n".join(
            [
                "用户通过界面明确选择了“深度研究”。这是本轮显式约束。",
                "按本轮选定的方法形成证据计划，围绕重要判断和关键缺口渐进取证，交付完整研究；本轮深度以界面选择为准。",
            ]
        )
    return "\n".join(
        [
            "用户选择了默认的“智能分析”，没有固定本轮研究深度。",
            "依据用户完整语义、当前上下文和已有证据决定有效深度，采用本轮选定的方法。用户明确的交付要求优先，按结论所需渐进补足证据。",
        ]
    )
