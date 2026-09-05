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

from src.scenarios.financial_qa.tools import FinanceDataQueryCcTools


_EXPOSED_TOOLS = frozenset(
    {"read_finance_catalog", "finance_query", "load_finance_result"}
)


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"DSH finance context must be a JSON object: {path}")
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


class FinanceDshMcpBridge:
    """Expose the existing financial query handlers through one DSH MCP child."""

    def __init__(
        self,
        *,
        context_path: Path,
        trace_path: Path,
        system_tools: FinanceDataQueryCcTools | None = None,
    ) -> None:
        self.context_path = context_path
        self.trace_path = trace_path
        self.system_tools = system_tools or FinanceDataQueryCcTools()
        self.tool_runtime = self.system_tools.create_runtime()
        self._context = self._read_context()
        self._context_revision = str(self._context.get("revision") or "")
        self._expected_catalog_revision = str(
            self._context.get("finance_catalog_revision") or ""
        )
        self._catalog_revision_value = ""
        if not self._expected_catalog_revision:
            self._expected_catalog_revision = self._current_catalog_revision()
        self._rebuild_tools()
        self._write_trace()

    def _current_catalog_revision(self) -> str:
        revision_reader = getattr(
            self.system_tools.finance_catalog,
            "catalog_revision",
            None,
        )
        return str(revision_reader() or "") if callable(revision_reader) else ""

    def _rebuild_tools(self) -> None:
        tools, _, tracker = self.system_tools.build_tools(
            owner_ids=self._owner_ids(self._context),
            tool_context=self._tool_context(self._context),
            runtime=self.tool_runtime,
        )
        rebuilt_tools = {
            item.name: item
            for item in tools
            if item.name in _EXPOSED_TOOLS
        }
        missing = sorted(_EXPOSED_TOOLS - set(rebuilt_tools))
        if missing:
            raise RuntimeError(f"missing finance MCP tools: {', '.join(missing)}")
        pinned_catalog_revision = str(
            tracker.get("finance_catalog_revision") or ""
        )
        if (
            self._expected_catalog_revision
            and pinned_catalog_revision
            and pinned_catalog_revision != self._expected_catalog_revision
        ):
            raise RuntimeError(
                "finance catalog changed while rebuilding DSH tools"
            )
        self.tools = rebuilt_tools
        self.tracker = tracker
        self._catalog_revision_value = pinned_catalog_revision

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
                f"DSH finance context not found: {self.context_path}"
            )
        return _load_object(self.context_path)

    def _sync_context(self) -> None:
        context = self._read_context()
        revision = str(context.get("revision") or "")
        context_changed = (
            revision != self._context_revision or context != self._context
        )
        current_catalog_revision = self._current_catalog_revision()
        expected_catalog_revision = (
            str(context.get("finance_catalog_revision") or "")
            if context_changed
            else self._expected_catalog_revision
        )
        if not expected_catalog_revision:
            expected_catalog_revision = current_catalog_revision
        if (
            expected_catalog_revision
            and current_catalog_revision != expected_catalog_revision
        ):
            raise RuntimeError(
                "finance catalog changed during the active agent turn"
            )
        catalog_changed = (
            current_catalog_revision != self._catalog_revision_value
        )
        if not context_changed and not catalog_changed:
            return
        if context_changed:
            self._context = context
            self._context_revision = revision
            self._expected_catalog_revision = expected_catalog_revision
        if catalog_changed:
            self._rebuild_tools()
        else:
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
                "finance_catalog_revision": self._catalog_revision_value,
                "runtime_scope": self.tool_runtime.runtime_scope,
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
            try:
                self._sync_context()
            except RuntimeError as exc:
                if "finance catalog changed" not in str(exc):
                    raise
                return {"error": str(exc)}
            definition = self.tools.get(name)
            if definition is None:
                raise ValueError(f"unknown finance tool: {name}")
            result = await definition.handler(dict(arguments or {}))
            if self._current_catalog_revision() != self._expected_catalog_revision:
                return {
                    "error": "finance catalog changed during the active agent turn"
                }
            return _payload_from_sdk_result(result)
        finally:
            self._write_trace()


def create_server(bridge: FinanceDshMcpBridge) -> Server:
    server = Server("fin-agent-finance", version="1.0")

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
    bridge = FinanceDshMcpBridge(
        context_path=context_path,
        trace_path=trace_path,
    )
    server = create_server(bridge)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="fin-agent-finance",
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
