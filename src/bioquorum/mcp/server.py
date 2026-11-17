"""BioQuorum as an MCP server (ARCHITECTURE.md §1 principle 4, ADR-005).

This module knows MCP, not BioQuorum tools: it receives the tool definitions (from
``ToolRegistry.definitions()``) and one ``call`` coroutine (which goes through
``tools.invoke()``), both wired up by the CLI. That keeps the layer rule intact:
``mcp`` sits below ``tools``.
"""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import mcp_types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from bioquorum.core.messages import UNTRUSTED_NOTICE


@dataclass(frozen=True)
class OutImage:
    mime: str
    data: bytes


@dataclass
class OutResult:
    items: list[str | OutImage] = field(default_factory=list)
    is_error: bool = False


CallFn = Callable[[str, dict[str, Any]], Awaitable[OutResult]]


def build_server(
    definitions: Sequence[dict[str, Any]],
    call: CallFn,
    *,
    name: str = "bioquorum",
    version: str = "",
) -> Server[Any]:
    tools = [
        types.Tool(name=d["name"], description=d["description"], input_schema=d["input_schema"])
        for d in definitions
    ]
    known = {t.name for t in tools}

    async def on_list_tools(_ctx: Any, _params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tools)

    async def on_call_tool(_ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        if params.name not in known:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Unknown tool '{params.name}'")],
                is_error=True,
            )
        out = await call(params.name, dict(params.arguments or {}))
        content: list[Any] = []
        for item in out.items:
            if isinstance(item, OutImage):
                content.append(
                    types.ImageContent(
                        type="image",
                        data=base64.b64encode(item.data).decode("ascii"),
                        mime_type=item.mime,
                    )
                )
            else:
                content.append(types.TextContent(type="text", text=item))
        return types.CallToolResult(content=content, is_error=out.is_error)

    return Server(
        name,
        version=version,
        instructions=UNTRUSTED_NOTICE,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


async def serve_stdio(server: Server[Any]) -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())
