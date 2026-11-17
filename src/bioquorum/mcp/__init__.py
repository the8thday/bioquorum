"""MCP client and server (ARCHITECTURE.md §6.4). Only this package imports the ``mcp`` SDK."""

from bioquorum.mcp.client import McpConnection, RemoteImage, RemoteResult, RemoteTool, ServerConfig
from bioquorum.mcp.server import OutImage, OutResult, build_server, serve_stdio

__all__ = [
    "McpConnection",
    "OutImage",
    "OutResult",
    "RemoteImage",
    "RemoteResult",
    "RemoteTool",
    "ServerConfig",
    "build_server",
    "serve_stdio",
]
