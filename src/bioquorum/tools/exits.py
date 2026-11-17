"""The MCP exit: serve the registry to an MCP host (Claude Code, opencode, ...).

Each call goes through ``invoke()``, the same chokepoint the agent uses (ADR-006), and
untrusted text is fenced before it leaves (ADR-007).
"""

from __future__ import annotations

from typing import Any

from bioquorum.core.enums import Trust
from bioquorum.core.messages import (
    DataRefPart,
    ImageRefPart,
    TextPart,
    ToolResultPart,
    fence_untrusted,
)
from bioquorum.mcp.server import CallFn, OutImage, OutResult
from bioquorum.provenance.cas import ContentStore
from bioquorum.tools.context import ToolContext
from bioquorum.tools.invoke import invoke_by_name
from bioquorum.tools.registry import ToolRegistry


def to_out(part: ToolResultPart, cas: ContentStore | None) -> OutResult:
    def text(t: str) -> str:
        return fence_untrusted(t) if part.trust is Trust.UNTRUSTED else t

    out = OutResult(is_error=part.is_error)
    for item in part.content:
        if isinstance(item, TextPart):
            out.items.append(text(item.text))
        elif isinstance(item, ImageRefPart):
            if cas is not None and cas.has(item.ref.content_hash):
                out.items.append(OutImage(mime=item.mime, data=cas.get(item.ref)))
            else:
                out.items.append(f"[{item.mime} image {item.ref.uri}]")
        elif isinstance(item, DataRefPart):
            out.items.append(text(f"{item.summary}\n[data {item.ref.uri}]"))
    return out


def mcp_call_fn(registry: ToolRegistry, ctx: ToolContext) -> CallFn:
    async def call(name: str, args: dict[str, Any]) -> OutResult:
        part = await invoke_by_name(registry, name, args, ctx)
        return to_out(part, ctx.cas)

    return call
