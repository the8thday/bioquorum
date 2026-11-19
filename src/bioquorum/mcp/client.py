"""Client for external MCP servers (ARCHITECTURE.md §6.4).

Speaks MCP via the official SDK and hands back provider-neutral values. Wrapping remote
tools as ToolSpecs happens one layer up in ``tools/external.py``: this package sits below
``tools`` and may not import it.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
from collections.abc import Callable
from contextlib import AbstractContextManager, AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

import httpx2
from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from mcp.types import CONNECTION_CLOSED, INVALID_REQUEST

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
    isolated: bool = False
    read_paths: tuple[str, ...] = ()
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
        if type(self.isolated) is not bool or (self.isolated and (not self.command or self.env)):
            raise ValueError(
                "Offline MCP isolation requires a stdio command with no custom environment"
            )

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
            isolated=data.get("isolated", False),
            read_paths=tuple(data.get("read_paths", ())),
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


@dataclass(frozen=True)
class StdioLaunch:
    argv: tuple[str, ...]
    cwd: str
    env: dict[str, str]


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

    def __init__(
        self,
        config: ServerConfig,
        *,
        target: Any = None,
        outbound_proxy: str | None = None,
        isolated_stdio: Callable[[], AbstractContextManager[StdioLaunch]] | None = None,
    ) -> None:
        self.config = config
        self._outbound_proxy = outbound_proxy
        self._isolated_stdio = isolated_stdio if target is None and config.replay is None else None
        if config.replay is None and target is None:
            if config.isolated and isolated_stdio is None:
                raise ValueError("Offline MCP isolation is unavailable")
            if config.command and outbound_proxy and not config.isolated:
                raise ValueError(
                    "The outbound proxy requires stdio MCP commands to run offline in isolation"
                )
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
        self._target = target
        self._lock = asyncio.Lock()
        self._owner: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._client: Client | None = None
        self._active = False

    def _new_client(self, target: Any) -> Client:
        return Client(target, read_timeout_seconds=self.config.timeout_s, mode=self.config.mode)

    async def _keep_open(self, ready: asyncio.Future[Client], stop: asyncio.Event) -> None:
        # SDK transports own AnyIO task groups. Enter and exit them in the same task,
        # independently of which caller observes a disconnect or starts the next request.
        try:
            async with AsyncExitStack() as stack:
                target = self._target
                if self._isolated_stdio is not None:
                    prepared = stack.enter_context(self._isolated_stdio())
                    target = StdioServerParameters(
                        command=prepared.argv[0],
                        args=list(prepared.argv[1:]),
                        env=prepared.env,
                        cwd=prepared.cwd,
                    )
                if self._outbound_proxy is not None and isinstance(target, str):
                    http = await stack.enter_async_context(
                        httpx2.AsyncClient(
                            proxy=self._outbound_proxy,
                            trust_env=False,
                            timeout=httpx2.Timeout(self.config.timeout_s),
                        )
                    )
                    target = streamable_http_client(target, http_client=http)
                client = await stack.enter_async_context(self._new_client(target))
                ready.set_result(client)
                await stop.wait()
        except Exception as exc:
            if not ready.done():
                ready.set_exception(exc)

    async def _close(self) -> None:
        self._client = None
        if self._owner is not None:
            self._stop.set()
            await self._owner
            self._owner = None

    async def _connected(self) -> Client:
        async with self._lock:
            if not self._active:
                raise RuntimeError("MCP connection is not open")
            if self._client is not None and self._owner is not None and not self._owner.done():
                return self._client
            await self._close()
            ready = asyncio.get_running_loop().create_future()
            self._stop = asyncio.Event()
            self._owner = asyncio.create_task(self._keep_open(ready, self._stop))
            try:
                client = await asyncio.shield(ready)
            except BaseException:
                self._owner.cancel()
                await asyncio.gather(self._owner, return_exceptions=True)
                self._owner = None
                if ready.done() and not ready.cancelled():
                    ready.exception()
                raise
            self._client = client
            return client

    async def _invalidate(self, client: Client, exc: Exception) -> None:
        closed = isinstance(exc, MCPError) and (
            exc.code == CONNECTION_CLOSED
            or (exc.code == INVALID_REQUEST and exc.message == "Session terminated")
        )
        if closed or isinstance(exc, ConnectionError | httpx2.TransportError):
            async with self._lock:
                if self._client is client:
                    self._client = None

    async def __aenter__(self) -> McpConnection:
        if self._active:
            raise RuntimeError("MCP connection is already open")
        self._active = True
        try:
            await self._connected()
        except BaseException:
            self._active = False
            raise
        return self

    async def __aexit__(self, *exc: object) -> None:
        async with self._lock:
            self._active = False
            await self._close()

    async def list_tools(self) -> list[RemoteTool]:
        client = await self._connected()
        tools: list[RemoteTool] = []
        cursor: str | None = None
        while True:
            try:
                page = await client.list_tools(cursor=cursor)
            except Exception as exc:
                await self._invalidate(client, exc)
                raise
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
        client = await self._connected()
        try:
            result = await client.call_tool(name, args)
        except Exception as exc:
            # A lost reply gives no guarantee the remote tool did not run. Leave
            # this call failed in the audit trail and reconnect for the next call.
            await self._invalidate(client, exc)
            raise
        return _convert(result)
