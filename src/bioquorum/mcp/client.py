"""Client for external MCP servers (ARCHITECTURE.md §6.4).

Speaks MCP via the official SDK and hands back provider-neutral values. Wrapping remote
tools as ToolSpecs happens one layer up in ``tools/external.py``: this package sits below
``tools`` and may not import it.
"""

from __future__ import annotations

import base64
import json
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from mcp import Client, StdioServerParameters

from bioquorum.core.enums import Classification

_NAME_OK = re.compile(r"[^A-Za-z0-9_-]")


@dataclass(frozen=True)
class ServerConfig:
    """One external MCP server, as written in ``bioquorum.toml`` under ``[mcp.servers.<name>]``.

    ``clearance`` is the most sensitive project classification whose data may be sent to
    this server (see policy.classification). Remote servers default to ``public``: a
    query string can reveal what a confidential project is working on. Set ``local`` for
    servers that run entirely on this machine.
    """

    name: str
    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] | None = None
    url: str | None = None
    clearance: Classification | None = Classification.PUBLIC
    timeout_s: float = 60.0
    replay: str | None = None
    """Path to a recorded cassette. When set, the live server is never contacted."""
    protocol: str | None = None
    """MCP version negotiation: ``legacy`` (initialize handshake) or ``auto`` (probe first).
    Default: ``legacy`` for stdio servers, since some (e.g. BioMCP 0.9) crash after the
    ``auto`` probe; ``auto`` for URLs."""

    @property
    def mode(self) -> str:
        if self.protocol is not None:
            return self.protocol
        return "legacy" if self.command is not None else "auto"

    def __post_init__(self) -> None:
        if self.command is not None and self.url is not None:
            raise ValueError(f"mcp server '{self.name}': give only one of command / url")
        if self.command is None and self.url is None and self.replay is None:
            raise ValueError(f"mcp server '{self.name}': needs command, url or replay")
        if _NAME_OK.search(self.name):
            raise ValueError(f"mcp server name '{self.name}' may only use [A-Za-z0-9_-]")

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> ServerConfig:
        clearance = data.get("clearance", "public")
        return cls(
            name=name,
            command=data.get("command"),
            args=tuple(data.get("args", ())),
            env=data.get("env"),
            url=data.get("url"),
            clearance=None if clearance == "local" else Classification(clearance),
            timeout_s=float(data.get("timeout_s", 60.0)),
            replay=data.get("replay"),
            protocol=data.get("protocol"),
        )


@dataclass(frozen=True)
class RemoteTool:
    server: str
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class RemoteImage:
    mime: str
    data: bytes


@dataclass
class RemoteResult:
    items: list[str | RemoteImage] = field(default_factory=list)
    is_error: bool = False

    def text(self) -> str:
        return "\n".join(i for i in self.items if isinstance(i, str))

    def images(self) -> list[RemoteImage]:
        return [i for i in self.items if isinstance(i, RemoteImage)]


def _convert(result: Any) -> RemoteResult:
    out = RemoteResult(is_error=bool(result.is_error))
    for block in result.content:
        kind = getattr(block, "type", None)
        if kind == "text":
            out.items.append(block.text)
        elif kind == "image":
            out.items.append(RemoteImage(mime=block.mime_type, data=base64.b64decode(block.data)))
        elif kind == "resource":
            res = block.resource
            text = getattr(res, "text", None)
            out.items.append(text if text is not None else f"[binary resource {res.uri}]")
        elif kind == "resource_link":
            out.items.append(f"[resource {getattr(block, 'uri', '')}]")
        else:
            out.items.append(f"[unsupported MCP content: {kind}]")
    if not out.items and result.structured_content is not None:
        out.items.append(json.dumps(result.structured_content, ensure_ascii=False))
    return out


class McpConnection:
    """One live connection. Use as ``async with McpConnection(cfg) as conn``.

    ``target`` overrides the config's transport; tests pass an in-process server.
    """

    def __init__(self, config: ServerConfig, *, target: Any = None) -> None:
        self.config = config
        if target is None and config.replay is not None:
            from bioquorum.mcp.replay import Cassette, replay_server

            target = replay_server(Cassette.load(config.replay))
        if target is None:
            if config.url is not None:
                target = config.url
            else:
                assert config.command is not None
                target = StdioServerParameters(
                    command=config.command, args=list(config.args), env=config.env
                )
        self._client = Client(target, read_timeout_seconds=config.timeout_s, mode=config.mode)
        self._stack: AsyncExitStack | None = None

    async def __aenter__(self) -> McpConnection:
        self._stack = AsyncExitStack()
        await self._stack.enter_async_context(self._client)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None

    async def list_tools(self) -> list[RemoteTool]:
        tools: list[RemoteTool] = []
        cursor: str | None = None
        while True:
            page = await self._client.list_tools(cursor=cursor)
            tools.extend(
                RemoteTool(
                    server=self.config.name,
                    name=t.name,
                    description=t.description or "",
                    input_schema=dict(t.input_schema),
                )
                for t in page.tools
            )
            cursor = page.next_cursor
            if not cursor:
                return tools

    async def call_tool(self, name: str, args: dict[str, Any]) -> RemoteResult:
        result = await self._client.call_tool(name, args)
        return _convert(result)
