"""External MCP tools as ToolSpecs (ARCHITECTURE.md §6.4, ADR-005, ADR-007).

Every remote tool gets ``permission=external_mcp``, ``output_trust=untrusted`` and the
server's clearance as ``classification_floor``. Names are namespaced ``<server>__<tool>``
so two servers can expose tools with the same name.
"""

from __future__ import annotations

from typing import Any

from bioquorum.core.enums import DataKind, PermissionClass, Trust
from bioquorum.core.messages import ImageRefPart, ResultContent, TextPart
from bioquorum.mcp.client import McpConnection, RemoteImage, RemoteTool
from bioquorum.tools.context import ToolContext
from bioquorum.tools.registry import ToolRegistry
from bioquorum.tools.spec import ToolResult, ToolSpec

MAX_NAME = 64  # common limit across providers for tool names


def qualified_name(server: str, tool: str) -> str:
    return f"{server}__{tool}"[:MAX_NAME]


def remote_tool_spec(conn: McpConnection, tool: RemoteTool) -> ToolSpec:
    server = conn.config.name

    async def handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        result = await conn.call_tool(tool.name, args)
        content: list[ResultContent] = []
        for item in result.items:
            if isinstance(item, RemoteImage):
                if ctx.cas is None:
                    content.append(TextPart(text=f"[{item.mime} image, {len(item.data)} bytes]"))
                else:
                    ref = ctx.cas.put_bytes(item.data, kind=DataKind.FIGURE)
                    content.append(ImageRefPart(ref=ref, mime=item.mime))
            else:
                content.append(TextPart(text=item))
        return ToolResult(content=content, is_error=result.is_error)

    return ToolSpec(
        name=qualified_name(server, tool.name),
        description=f"[external MCP server '{server}'] {tool.description}".strip(),
        handler=handler,
        raw_input_schema=tool.input_schema,
        permission=PermissionClass.EXTERNAL_MCP,
        output_trust=Trust.UNTRUSTED,
        classification_floor=conn.config.clearance,
        target=lambda _args, _ctx: f"mcp:{server}",
    )


async def register_external(registry: ToolRegistry, conn: McpConnection) -> list[ToolSpec]:
    """List the server's tools and add them to ``registry``. Returns the new specs."""
    specs = [remote_tool_spec(conn, t) for t in await conn.list_tools()]
    for spec in specs:
        registry.register(spec)
    return specs
