from __future__ import annotations

import base64

import mcp_types as types
import pytest
from mcp.server.mcpserver import MCPServer

PNG = b"\x89PNG\r\n\x1a\nfake"


@pytest.fixture
def fake_ot() -> MCPServer:
    """Stands in for a remote database MCP server (Open Targets, BioMCP, ...)."""
    s = MCPServer("fake-ot")

    @s.tool()
    def target_info(symbol: str) -> str:
        """Look up a drug target."""
        return f"{symbol}: ENSG00000162594"

    @s.tool()
    def plot() -> list[types.ImageContent]:
        """Return an image."""
        return [
            types.ImageContent(
                type="image", data=base64.b64encode(PNG).decode(), mime_type="image/png"
            )
        ]

    @s.tool()
    def broken() -> str:
        """Always fails."""
        raise RuntimeError("upstream 500")

    return s
