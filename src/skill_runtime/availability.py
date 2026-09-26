from __future__ import annotations

from typing import Any, Mapping


def _trim(value: Any) -> str:
    return str(value or "").strip()


def legacy_skill_is_active(config: Mapping[str, Any]) -> bool:
    """Return whether a compiled legacy Skill may execute.

    ``retrieval_mode`` controls discovery, not direct execution.  The existing
    ``status`` and ``availability.lifecycle`` fields are the authoritative
    execution lifecycle and must agree.
    """

    availability = (
        config.get("availability")
        if isinstance(config.get("availability"), Mapping)
        else {}
    )
    status = _trim(config.get("status")).lower() or "active"
    lifecycle = _trim(availability.get("lifecycle")).lower() or "active"
    return status == "active" and lifecycle == "active"


def legacy_skill_is_publicly_invocable(config: Mapping[str, Any]) -> bool:
    """Return whether a compiled Skill may be selected by a public caller."""

    if not legacy_skill_is_active(config):
        return False
    availability = (
        config.get("availability")
        if isinstance(config.get("availability"), Mapping)
        else {}
    )
    visibility = _trim(availability.get("visibility")).lower() or "visible"
    auth = _trim(config.get("auth")).lower() or "public"
    return visibility != "hidden" and auth == "public"
