from __future__ import annotations

from typing import Iterable


def _normalize_names(values: Iterable[object] | None) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        name = str(value or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        items.append(name)
    return items


def resolve_step_budget(
    *,
    base_max_steps: int | None,
    tool_mode: str,
    selected_tools: Iterable[object] | None = None,
    required_tools_before_final: Iterable[object] | None = None,
) -> int:
    base = max(1, int(base_max_steps or 6))
    mode = str(tool_mode or "strict").strip().lower() or "strict"
    selected = _normalize_names(selected_tools)
    required_tools = _normalize_names(required_tools_before_final)

    if mode == "strict":
        # Strict mode should be able to cover all declared/required tools plus
        # at least one synthesis turn and one finalization/correction turn.
        required_count = max(len(selected), len(required_tools))
        if required_count <= 0:
            return base
        return max(base, required_count + 2)

    if mode in {"auto", "free"}:
        # Auto/free mode needs a looser budget because the model may explore
        # more than the final minimal tool set before converging.
        candidate_count = max(len(selected), len(required_tools))
        if candidate_count <= 0:
            return base
        return max(base, min(16, candidate_count + 3))

    return base
