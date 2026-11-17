"""Record and replay external MCP servers (ARCHITECTURE.md §6.8, ROADMAP v0.1 risks).

A cassette holds a server's tool list and recorded calls. ``replay_server`` serves a
cassette in-process, so tests run offline and demos survive a flaky upstream. Calls are
matched on (tool, canonical JSON of args); anything unrecorded returns an error result
rather than silently going to the network.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import mcp_types as types
from mcp.server import Server

from bioquorum.mcp.client import McpConnection, RemoteImage, RemoteResult, RemoteTool

FORMAT = 1


def _key(tool: str, args: dict[str, Any]) -> str:
    return tool + "\x00" + json.dumps(args, sort_keys=True, separators=(",", ":"))


def _dump_result(r: RemoteResult) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for it in r.items:
        if isinstance(it, RemoteImage):
            items.append({"image": {"mime": it.mime, "data": base64.b64encode(it.data).decode()}})
        else:
            items.append({"text": it})
    return {"items": items, "is_error": r.is_error}


@dataclass
class Cassette:
    server: str
    tools: list[dict[str, Any]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    recorded_at: str = ""
    source: str = ""

    @classmethod
    def load(cls, path: str | Path) -> Cassette:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("format") != FORMAT:
            raise ValueError(f"{path}: unsupported cassette format {data.get('format')}")
        return cls(
            server=data["server"],
            tools=data["tools"],
            calls=data["calls"],
            recorded_at=data.get("recorded_at", ""),
            source=data.get("source", ""),
        )

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "format": FORMAT,
            "server": self.server,
            "source": self.source,
            "recorded_at": self.recorded_at,
            "tools": self.tools,
            "calls": self.calls,
        }
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def add_call(self, tool: str, args: dict[str, Any], result: RemoteResult) -> None:
        key = _key(tool, args)
        self.calls = [c for c in self.calls if _key(c["tool"], c["args"]) != key]
        self.calls.append({"tool": tool, "args": args, "result": _dump_result(result)})


async def record(
    conn: McpConnection, calls: list[tuple[str, dict[str, Any]]], *, into: Cassette | None = None
) -> Cassette:
    """Fetch the live tool list and run ``calls``, collecting everything into a cassette."""
    tools: list[RemoteTool] = await conn.list_tools()
    cassette = into or Cassette(server=conn.config.name)
    cassette.tools = [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in tools
    ]
    # Command basename only: absolute paths would leak the recorder's username into the repo.
    cmd = Path(conn.config.command).name if conn.config.command else ""
    cassette.source = conn.config.url or " ".join([cmd, *conn.config.args])
    cassette.recorded_at = datetime.now(UTC).isoformat(timespec="seconds")
    for tool, args in calls:
        cassette.add_call(tool, args, await conn.call_tool(tool, args))
    return cassette


def replay_server(cassette: Cassette) -> Server[Any]:
    tools = [
        types.Tool(name=t["name"], description=t["description"], input_schema=t["input_schema"])
        for t in cassette.tools
    ]
    recorded = {_key(c["tool"], c["args"]): c["result"] for c in cassette.calls}

    async def on_list_tools(_ctx: Any, _params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tools)

    async def on_call_tool(_ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        result = recorded.get(_key(params.name, dict(params.arguments or {})))
        if result is None:
            msg = f"replay: no recorded response for {params.name} with these arguments"
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=msg)], is_error=True
            )
        content: list[Any] = []
        for item in result["items"]:
            if "image" in item:
                img = item["image"]
                content.append(
                    types.ImageContent(type="image", data=img["data"], mime_type=img["mime"])
                )
            else:
                content.append(types.TextContent(type="text", text=item["text"]))
        return types.CallToolResult(content=content, is_error=result["is_error"])

    return Server(
        f"replay:{cassette.server}", on_list_tools=on_list_tools, on_call_tool=on_call_tool
    )
