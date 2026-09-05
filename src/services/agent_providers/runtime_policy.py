from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping


class AgentComplexityLevel(str, Enum):
    """Provider-neutral reasoning and latency classes."""

    FASTEST = "fastest"
    FAST = "fast"
    MID = "mid"
    HIGH = "high"


class WebSearchPolicy(str, Enum):
    """Web access stays independent from model/reasoning complexity."""

    DISABLED = "disabled"
    PROVIDER_DEFAULT = "provider_default"
    LIVE = "live"


@dataclass(frozen=True)
class AgentCapabilityPolicy:
    """Explicit external-capability surface for one agent run.

    An empty ``mcp_servers`` mapping means MCP is disabled. Server definitions
    remain provider-adapter input and never enter the business prompt.
    """

    web_search: WebSearchPolicy | str = WebSearchPolicy.DISABLED
    mcp_servers: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    mcp_allowed_tools: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        try:
            web_search = self.web_search if isinstance(self.web_search, WebSearchPolicy) else WebSearchPolicy(str(self.web_search))
        except ValueError as exc:
            raise ValueError(f"unsupported web search policy: {self.web_search}") from exc
        normalized_servers: dict[str, dict[str, Any]] = {}
        for raw_name, raw_config in dict(self.mcp_servers or {}).items():
            name = str(raw_name or "").strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
                raise ValueError(f"invalid MCP server name: {name or '-'}")
            if not isinstance(raw_config, Mapping):
                raise ValueError(f"MCP server config must be an object: {name}")
            config = dict(raw_config)
            if not str(config.get("command") or "").strip() and not str(config.get("url") or "").strip():
                raise ValueError(f"MCP server requires an explicit command or url: {name}")
            inline_secret_fields = {"env", "headers", "http_headers"} & set(config)
            if inline_secret_fields:
                fields = ", ".join(sorted(inline_secret_fields))
                raise ValueError(
                    f"MCP credentials must reference process environment names, not inline {fields}: {name}"
                )
            normalized_servers[name] = config
        allowed_tool_values = [str(item or "").strip() for item in self.mcp_allowed_tools if str(item or "").strip()]
        for server_name, config in normalized_servers.items():
            for tool_name in config.get("allowed_tools") or ():
                normalized_tool = str(tool_name or "").strip()
                if normalized_tool:
                    allowed_tool_values.append(f"mcp__{server_name}__{normalized_tool}")
        allowed_tools = tuple(dict.fromkeys(allowed_tool_values))
        if allowed_tools and not normalized_servers:
            raise ValueError("MCP tools cannot be allowed when no MCP servers are configured")
        if any(not item.startswith("mcp__") for item in allowed_tools):
            raise ValueError("MCP allowed tools must use the mcp__<server>__<tool> name")
        if normalized_servers and not allowed_tools:
            raise ValueError("configured MCP servers require an explicit allowed tool list")
        for tool_name in allowed_tools:
            parts = tool_name.split("__", 2)
            if len(parts) != 3 or parts[1] not in normalized_servers or not parts[2]:
                raise ValueError(f"MCP allowed tool references an unconfigured server: {tool_name}")
        object.__setattr__(self, "web_search", web_search)
        object.__setattr__(self, "mcp_servers", normalized_servers)
        object.__setattr__(self, "mcp_allowed_tools", allowed_tools)


@dataclass(frozen=True)
class ResolvedAgentProfile:
    level: AgentComplexityLevel
    provider: str
    model: str
    reasoning_effort: str
    max_turns: int
    thinking: str = ""


def agent_capability_policy_from_env() -> AgentCapabilityPolicy:
    """Load the explicit provider capability boundary for the main runtime.

    MCP configuration is optional and file based so credentials never enter
    prompts or environment values containing JSON.
    """
    web_search = str(os.environ.get("CUSTOM_TOOL_AGENT_WEB_SEARCH") or "disabled").strip().lower()
    config_path = str(os.environ.get("CUSTOM_TOOL_AGENT_MCP_CONFIG_PATH") or "").strip()
    if not config_path:
        return AgentCapabilityPolicy(web_search=web_search)
    path = Path(config_path).expanduser()
    if not path.is_file():
        raise ValueError(f"agent MCP config file not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid agent MCP config JSON: {path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError(f"agent MCP config must be a JSON object: {path}")
    servers = payload.get("mcp_servers") if "mcp_servers" in payload else payload
    if not isinstance(servers, Mapping):
        raise ValueError(f"agent MCP mcp_servers must be a JSON object: {path}")
    allowed = payload.get("mcp_allowed_tools", ()) if "mcp_servers" in payload else ()
    if not isinstance(allowed, (list, tuple)):
        raise ValueError(f"agent MCP mcp_allowed_tools must be an array: {path}")
    return AgentCapabilityPolicy(
        web_search=web_search,
        mcp_servers=servers,
        mcp_allowed_tools=tuple(str(item) for item in allowed),
    )


_CODEX_PROFILES = {
    AgentComplexityLevel.FASTEST: ("gpt-5.6-luna", "low", 4),
    AgentComplexityLevel.FAST: ("gpt-5.6-terra", "high", 12),
    AgentComplexityLevel.MID: ("gpt-5.6-terra", "medium", 20),
    AgentComplexityLevel.HIGH: ("gpt-5.6-sol", "high", 32),
}

_DEEPSEEK_CLAUDE_PROFILES = {
    AgentComplexityLevel.FASTEST: ("deepseek-chat", "low", 4, "disabled"),
    AgentComplexityLevel.FAST: ("deepseek-chat", "high", 20, ""),
    AgentComplexityLevel.MID: ("deepseek-chat", "medium", 20, ""),
    AgentComplexityLevel.HIGH: ("deepseek-reasoner", "high", 32, ""),
}

_DASHSCOPE_CLAUDE_PROFILES = {
    AgentComplexityLevel.FASTEST: ("deepseek-v4-flash", "low", 4, "disabled"),
    AgentComplexityLevel.FAST: ("deepseek-v4-flash", "high", 20, ""),
    AgentComplexityLevel.MID: ("deepseek-v4-pro", "medium", 20, ""),
    AgentComplexityLevel.HIGH: ("deepseek-v4-pro", "high", 32, ""),
}

_ANTHROPIC_CLAUDE_PROFILES = {
    AgentComplexityLevel.FASTEST: ("haiku", "low", 4, "disabled"),
    AgentComplexityLevel.FAST: ("haiku", "high", 12, ""),
    AgentComplexityLevel.MID: ("sonnet", "medium", 20, ""),
    AgentComplexityLevel.HIGH: ("opus", "high", 32, ""),
}


def resolve_agent_profile(
    provider: str,
    level: AgentComplexityLevel | str,
    *,
    claude_transport_provider: str = "deepseek",
) -> ResolvedAgentProfile:
    """Translate the stable four-level contract into provider parameters."""

    normalized_provider = str(provider or "").strip().lower()
    try:
        normalized_level = level if isinstance(level, AgentComplexityLevel) else AgentComplexityLevel(str(level))
    except ValueError as exc:
        raise ValueError(f"unsupported agent complexity level: {level}") from exc
    if normalized_provider == "codex":
        model, effort, max_turns = _CODEX_PROFILES[normalized_level]
        return ResolvedAgentProfile(normalized_level, normalized_provider, model, effort, max_turns)
    if normalized_provider == "claude":
        transport = str(claude_transport_provider or "deepseek").strip().lower()
        if transport == "deepseek":
            profiles = _DEEPSEEK_CLAUDE_PROFILES
        elif transport == "dashscope":
            profiles = _DASHSCOPE_CLAUDE_PROFILES
        else:
            profiles = _ANTHROPIC_CLAUDE_PROFILES
        model, effort, max_turns, thinking = profiles[normalized_level]
        return ResolvedAgentProfile(normalized_level, normalized_provider, model, effort, max_turns, thinking)
    raise ValueError(f"unsupported agent provider: {normalized_provider or '-'}")
