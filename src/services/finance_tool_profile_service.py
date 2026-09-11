from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


FINANCE_TOOL_PROFILE_PROTOCOL = "finance_tool_profile.v1"
FINANCE_TOOL_FAMILIES = frozenset(
    {"information", "analytics", "strategy", "action"}
)
FINANCE_TOOL_EXECUTION_SHAPES = frozenset(
    {
        "aggregate_context",
        "entity_local",
        "cross_sectional",
        "portfolio_stateful",
    }
)
FINANCE_TOOL_OUTPUT_SEMANTICS = frozenset(
    {
        "facts",
        "metric",
        "series",
        "assessment",
        "ranked_selection",
        "signal",
        "portfolio_target",
        "action_receipt",
    }
)
ACTION_EXECUTION_POLICY = "planned_non_executable"


class FinanceToolProfileError(ValueError):
    """A semantic Tool profile is malformed or contradicts HARD contracts."""


def _trim(value: Any) -> str:
    return str(value or "").strip()


@dataclass(frozen=True)
class FinanceToolProfile:
    """Small canonical business portrait for one immutable Tool revision.

    The execution shape records whether business results are independent or
    collection-dependent. It is not an authorization, concurrency-limit,
    strategy-runtime, or backtest contract.
    """

    family: str
    execution_shape: str
    output_semantic: str
    summary: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "FinanceToolProfile":
        protocol = _trim(value.get("protocol"))
        if protocol and protocol != FINANCE_TOOL_PROFILE_PROTOCOL:
            raise FinanceToolProfileError(
                f"unsupported finance Tool profile protocol: {protocol}"
            )
        family = _trim(value.get("family")).lower()
        if family not in FINANCE_TOOL_FAMILIES:
            raise FinanceToolProfileError(
                "finance_tool_profile.family must be one of: "
                + ", ".join(sorted(FINANCE_TOOL_FAMILIES))
            )
        execution_shape = _trim(value.get("execution_shape")).lower()
        if execution_shape not in FINANCE_TOOL_EXECUTION_SHAPES:
            raise FinanceToolProfileError(
                "finance_tool_profile.execution_shape must be one of: "
                + ", ".join(sorted(FINANCE_TOOL_EXECUTION_SHAPES))
            )
        output_semantic = _trim(value.get("output_semantic")).lower()
        if output_semantic not in FINANCE_TOOL_OUTPUT_SEMANTICS:
            raise FinanceToolProfileError(
                "finance_tool_profile.output_semantic must be one of: "
                + ", ".join(sorted(FINANCE_TOOL_OUTPUT_SEMANTICS))
            )
        return cls(
            family=family,
            execution_shape=execution_shape,
            output_semantic=output_semantic,
            summary=_trim(value.get("summary")),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "protocol": FINANCE_TOOL_PROFILE_PROTOCOL,
            "family": self.family,
            "execution_shape": self.execution_shape,
            "output_semantic": self.output_semantic,
        }
        if self.summary:
            result["summary"] = self.summary
        if self.family == "action":
            result["execution_policy"] = ACTION_EXECUTION_POLICY
        return result


class FinanceToolProfileService:
    """Canonicalize SOFT classification without granting HARD capabilities."""

    def normalize(
        self,
        profile: Any,
        *,
        strategy_runtime_profile: Any = None,
        selection_output_profile: Any = None,
    ) -> dict[str, Any] | None:
        has_runtime = isinstance(strategy_runtime_profile, Mapping) and bool(
            strategy_runtime_profile
        )
        has_selection = isinstance(selection_output_profile, Mapping) and bool(
            selection_output_profile
        )

        # Profile absence is a supported legacy revision shape.  Existing
        # strategy companions remain authoritative and continue to execute.
        if profile in (None, "") or (
            isinstance(profile, Mapping) and not profile
        ):
            return None
        if not isinstance(profile, Mapping):
            raise FinanceToolProfileError("finance_tool_profile must be an object")

        normalized = FinanceToolProfile.from_mapping(profile)
        if has_runtime and normalized.family != "strategy":
            raise FinanceToolProfileError(
                "strategy_runtime_profile requires finance Tool family=strategy"
            )
        payload = normalized.to_dict()
        if has_selection:
            # A SelectionOutputProfile is executable evidence that this
            # revision emits an ordered selection, not a loose model label.
            payload["output_semantic"] = "ranked_selection"
        return payload

    @staticmethod
    def assert_implementation_allowed(profile: Mapping[str, Any] | None) -> None:
        if isinstance(profile, Mapping) and _trim(profile.get("family")) == "action":
            raise FinanceToolProfileError(
                "action finance Tools are design-only and cannot be implemented "
                "or registered as executable custom Tools"
            )


__all__ = [
    "ACTION_EXECUTION_POLICY",
    "FINANCE_TOOL_EXECUTION_SHAPES",
    "FINANCE_TOOL_FAMILIES",
    "FINANCE_TOOL_OUTPUT_SEMANTICS",
    "FINANCE_TOOL_PROFILE_PROTOCOL",
    "FinanceToolProfile",
    "FinanceToolProfileError",
    "FinanceToolProfileService",
]
