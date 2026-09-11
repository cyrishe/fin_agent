from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

import anyio
from mcp import types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server

from src.services.finance_cc_system_tools import FinanceCcSystemTools, FinanceCcToolRuntime


_EXPOSED_TOOLS = frozenset(
    {
        "finance_query",
        "load_result",
        "read_finance_asset",
        "request_user_interaction",
        "save_finance_artifact",
        "run_dynamic_tool",
    }
)


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"DSH custom-tool context must be a JSON object: {path}")
    return payload


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    temporary.replace(path)


def _payload_from_sdk_result(result: Mapping[str, Any]) -> dict[str, Any]:
    for item in result.get("content") or []:
        if not isinstance(item, Mapping) or item.get("type") != "text":
            continue
        text = str(item.get("text") or "")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}
        return payload if isinstance(payload, dict) else {"value": payload}
    return {}


class CustomToolDshMcpBridge:
    """Expose the existing custom-tool application tools to one DSH worker.

    The coding tool is intentionally absent. DSH owns semantic orchestration;
    after a saved flow concludes the DSH turn, the parent process invokes the
    existing Codex implementation runner with the authoritative saved assets.
    """

    def __init__(
        self,
        *,
        context_path: Path,
        trace_path: Path,
        system_tools: FinanceCcSystemTools | None = None,
    ) -> None:
        self.context_path = context_path
        self.trace_path = trace_path
        self.system_tools = system_tools or FinanceCcSystemTools()
        self.tool_runtime = FinanceCcToolRuntime()
        self._context = self._read_context()
        self._context_revision = str(self._context.get("revision") or "")
        self._rebuild_tools()
        self._write_trace()

    @staticmethod
    def _owner_ids(context: Mapping[str, Any]) -> list[str]:
        return [
            str(item).strip()
            for item in context.get("owner_ids") or []
            if str(item).strip()
        ]

    @staticmethod
    def _tool_context(context: Mapping[str, Any]) -> dict[str, Any]:
        value = context.get("tool_context")
        return dict(value) if isinstance(value, Mapping) else {}

    def _read_context(self) -> dict[str, Any]:
        if not self.context_path.is_file():
            raise FileNotFoundError(
                f"DSH custom-tool context not found: {self.context_path}"
            )
        return _load_object(self.context_path)

    def _rebuild_tools(self) -> None:
        tools, _, tracker = self.system_tools.build_tools(
            owner_ids=self._owner_ids(self._context),
            tool_context=self._tool_context(self._context),
            runtime=self.tool_runtime,
        )
        rebuilt = {
            item.name: item for item in tools if item.name in _EXPOSED_TOOLS
        }
        missing = sorted(_EXPOSED_TOOLS - set(rebuilt))
        if missing:
            raise RuntimeError(
                f"missing custom-tool MCP tools: {', '.join(missing)}"
            )
        self.tools = rebuilt
        self.tracker = tracker

    def _sync_context(self) -> None:
        context = self._read_context()
        revision = str(context.get("revision") or "")
        if revision == self._context_revision and context == self._context:
            return
        self._context = context
        self._context_revision = revision
        self.tracker = self.tool_runtime.begin_turn(
            owner_ids=self._owner_ids(context),
            tool_context=self._tool_context(context),
        )
        self._write_trace()

    def _write_trace(self) -> None:
        _atomic_write(
            self.trace_path,
            {
                "revision": self._context_revision,
                "runtime_scope": self.tool_runtime.runtime_scope,
                "working_state": self.tool_runtime.working_state,
                "tracker": self.tracker,
            },
        )

    def list_tools(self) -> list[types.Tool]:
        self._sync_context()
        return [
            types.Tool(
                name=item.name,
                description=item.description,
                inputSchema=item.input_schema,
            )
            for item in self.tools.values()
        ]

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        try:
            self._sync_context()
            definition = self.tools.get(name)
            if definition is None:
                raise ValueError(f"unknown custom-tool tool: {name}")
            result = await definition.handler(dict(arguments or {}))
            return _payload_from_sdk_result(result)
        finally:
            self._write_trace()


def create_server(bridge: CustomToolDshMcpBridge) -> Server:
    server = Server("fin-agent-custom-tool", version="1.0")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return bridge.list_tools()

    @server.call_tool()
    async def call_tool(
        name: str,
        arguments: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return await bridge.call_tool(name, arguments)

    return server


async def _run() -> None:
    context_path = Path(os.environ["FIN_AGENT_DSH_CONTEXT_PATH"]).resolve()
    trace_path = Path(os.environ["FIN_AGENT_DSH_TRACE_PATH"]).resolve()
    bridge = CustomToolDshMcpBridge(
        context_path=context_path,
        trace_path=trace_path,
    )
    server = create_server(bridge)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="fin-agent-custom-tool",
                server_version="1.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


def main() -> None:
    anyio.run(_run)


if __name__ == "__main__":
    main()
