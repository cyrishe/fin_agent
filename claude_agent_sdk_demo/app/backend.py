from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from .config import Settings
from .contracts import BackendRequest, RESEARCH_OUTPUT_SCHEMA
from .event_stream import BackendEvent
from .normalizer import ClaudeEventNormalizer
from .permissions import ToolAuditLog, ToolPolicy, build_hooks
from .sdk_tools import build_demo_mcp_server


class AgentBackend(Protocol):
    async def stream(self, request: BackendRequest) -> AsyncIterator[BackendEvent]: ...


class ClaudeAgentBackend:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def stream(self, request: BackendRequest) -> AsyncIterator[BackendEvent]:
        from claude_agent_sdk import ClaudeSDKClient

        options = self.build_options(request)
        normalizer = ClaudeEventNormalizer()
        async with ClaudeSDKClient(options=options) as client:
            await client.query(request.question)
            async for message in client.receive_response():
                for event in normalizer.normalize(message):
                    yield event

    def build_options(self, request: BackendRequest):
        """Build options separately so configuration can be tested without a model call."""
        from claude_agent_sdk import ClaudeAgentOptions

        server, custom_tool_names = build_demo_mcp_server(self.settings)
        builtin_tools = ["Skill"]
        if request.enable_web_search and self.settings.web_search_backend == "builtin":
            builtin_tools.append("WebSearch")
        allowed_tools = [name for name in builtin_tools if name != "Skill"] + custom_tool_names
        if not request.enable_web_search:
            allowed_tools = [name for name in allowed_tools if not name.endswith("__web_search")]
        policy = ToolPolicy(frozenset(["Skill", *allowed_tools]))
        audit = ToolAuditLog()
        prompt = self.settings.system_prompt_path.read_text(encoding="utf-8")
        options = ClaudeAgentOptions(
            tools=builtin_tools,
            allowed_tools=allowed_tools,
            disallowed_tools=[
                "Bash",
                "Edit",
                "Write",
                "NotebookEdit",
                "Task",
                "AskUserQuestion",
            ],
            system_prompt=prompt,
            mcp_servers={"demo": server},
            strict_mcp_config=True,
            permission_mode="dontAsk",
            cwd=str(self.settings.workspace_dir),
            setting_sources=["project"],
            skills=request.skill_names,
            include_partial_messages=True,
            max_turns=self.settings.max_turns,
            max_budget_usd=self.settings.max_budget_usd,
            model=self.settings.model,
            effort=self.settings.effort or None,
            resume=request.session_id,
            output_format=(
                {"type": "json_schema", "schema": RESEARCH_OUTPUT_SCHEMA}
                if request.output_mode == "research_json"
                else None
            ),
            env=self.settings.provider_env(),
            hooks=build_hooks(policy, audit),
        )
        return options
