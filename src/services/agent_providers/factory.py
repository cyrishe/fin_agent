from __future__ import annotations

import os
from typing import Any

from src.services.agent_providers.claude import ClaudeSdkSkillHarness
from src.services.agent_providers.protocol import AgentSkillHarness
from src.services.agent_providers.runtime_policy import (
    AgentCapabilityPolicy,
    AgentComplexityLevel,
    resolve_agent_profile,
)
from src.services.codex_exec_skill_harness import CodexSdkSkillHarness


def _trim(value: Any) -> str:
    return str(value or "").strip()


def build_agent_skill_harness(
    provider: str = "",
    *,
    cwd: str = ".",
    complexity: AgentComplexityLevel | str = "",
    capabilities: AgentCapabilityPolicy | None = None,
) -> AgentSkillHarness:
    """Build a slow-agent adapter without leaking provider choices to business code."""
    selected = _trim(provider or os.environ.get("CUSTOM_TOOL_AGENT_PROVIDER") or "codex").lower()
    complexity_value = complexity.value if isinstance(complexity, AgentComplexityLevel) else complexity
    selected_complexity = _trim(complexity_value or os.environ.get("CUSTOM_TOOL_AGENT_COMPLEXITY")).lower()
    claude_transport = _trim(os.environ.get("CLAUDE_PROVIDER") or "deepseek").lower()
    profile = (
        resolve_agent_profile(selected, selected_complexity, claude_transport_provider=claude_transport)
        if selected_complexity
        else None
    )
    capability_policy = capabilities
    if selected == "codex":
        return CodexSdkSkillHarness(
            cwd=cwd,
            timeout_seconds=int(os.environ.get("STOCK_AGENT_CUSTOM_TOOL_CODEX_TIMEOUT_SECONDS") or 300),
            hard_timeout_seconds=int(os.environ.get("STOCK_AGENT_CUSTOM_TOOL_CODEX_HARD_TIMEOUT_SECONDS") or 1800),
            model=_trim(os.environ.get("STOCK_AGENT_CUSTOM_TOOL_CODEX_MODEL") or (profile.model if profile else "gpt-5.6-terra")),
            reasoning_effort=_trim(
                os.environ.get("STOCK_AGENT_CUSTOM_TOOL_CODEX_REASONING")
                or (profile.reasoning_effort if profile else "medium")
            ),
            sandbox=_trim(os.environ.get("STOCK_AGENT_CUSTOM_TOOL_CODEX_SANDBOX") or "workspace-write"),
            complexity_level=profile.level.value if profile else "",
            capabilities=capability_policy,
        )
    if selected == "claude":
        return ClaudeSdkSkillHarness(
            cwd=cwd,
            timeout_seconds=int(os.environ.get("STOCK_AGENT_CUSTOM_TOOL_CLAUDE_TIMEOUT_SECONDS") or 300),
            hard_timeout_seconds=int(os.environ.get("STOCK_AGENT_CUSTOM_TOOL_CLAUDE_HARD_TIMEOUT_SECONDS") or 1800),
            model=_trim(
                os.environ.get("STOCK_AGENT_CUSTOM_TOOL_CLAUDE_MODEL")
                or (profile.model if profile else os.environ.get("CLAUDE_MODEL"))
            ),
            effort=_trim(
                os.environ.get("STOCK_AGENT_CUSTOM_TOOL_CLAUDE_EFFORT")
                or (profile.reasoning_effort if profile else "high")
            ),
            max_turns=int(
                os.environ.get("STOCK_AGENT_CUSTOM_TOOL_CLAUDE_MAX_TURNS")
                or (profile.max_turns if profile else 20)
            ),
            max_budget_usd=float(os.environ.get("STOCK_AGENT_CUSTOM_TOOL_CLAUDE_MAX_BUDGET_USD") or 0.0),
            thinking=profile.thinking if profile else "",
            complexity_level=profile.level.value if profile else "",
            capabilities=capability_policy,
        )
    raise ValueError(f"unsupported custom-tool agent provider: {selected or '-'}")
