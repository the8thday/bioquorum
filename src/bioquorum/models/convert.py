"""The only place where canonical messages meet Pydantic AI types (ADR-003, ARCHITECTURE.md §6.1).

Rules:
- Tool call IDs: every call gets a BioQuorum ULID on the way in, and that ULID is sent
  back as the provider's tool_call_id in later turns. Providers accept client-chosen IDs
  (verified for DeepSeek chat and OpenAI Responses as long as server item IDs are not
  replayed), so no ID mapping has to be persisted.
- Thinking: kept with the model *family* that produced it and replayed only to that
  family (DeepSeek rejects a turn whose reasoning_content is missing); dropped otherwise.
- Untrusted tool output is fenced (ADR-007). Images are placeholders until vision lands.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from pydantic_ai import messages as pa
from pydantic_ai.tools import ToolDefinition

from bioquorum.core.enums import MessageRole, Trust
from bioquorum.core.ids import new_id
from bioquorum.core.messages import (
    DataRefPart,
    ImageRefPart,
    Message,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolResultPart,
    fence_untrusted,
)


def tool_definitions(definitions: Sequence[dict[str, Any]]) -> list[ToolDefinition]:
    """Registry definitions (``ToolRegistry.definitions()``) -> Pydantic AI tool definitions."""
    return [
        ToolDefinition(
            name=d["name"],
            description=d["description"],
            parameters_json_schema=d["input_schema"],
        )
        for d in definitions
    ]


# ----------------------------------------------------------------- canonical -> provider


def _result_text(part: ToolResultPart) -> str:
    pieces: list[str] = []
    for item in part.content:
        if isinstance(item, TextPart):
            pieces.append(item.text)
        elif isinstance(item, ImageRefPart):
            pieces.append(f"[{item.mime} image {item.ref.uri}]")
        elif isinstance(item, DataRefPart):
            pieces.append(f"{item.summary}\n[data {item.ref.uri}]")
    text = "\n".join(pieces)
    if part.trust is Trust.UNTRUSTED:
        text = fence_untrusted(text)
    return f"ERROR\n{text}" if part.is_error else text


def _thinking_to_pa(part: ThinkingPart) -> pa.ThinkingPart:
    meta: dict[str, Any] = json.loads(part.opaque) if part.opaque else {}
    return pa.ThinkingPart(
        content=part.text or "",
        id=meta.get("id"),
        signature=meta.get("signature"),
        provider_name=meta.get("provider_name"),
        provider_details=meta.get("provider_details"),
    )


def to_provider(messages: Sequence[Message], *, family: str) -> list[pa.ModelMessage]:
    """Canonical history -> Pydantic AI messages for a model of ``family``."""
    out: list[pa.ModelMessage] = []
    pending: list[Any] = []  # request parts waiting to be flushed into one ModelRequest
    tool_names: dict[str, str] = {}

    def flush() -> None:
        if pending:
            out.append(pa.ModelRequest(parts=list(pending)))
            pending.clear()

    for msg in messages:
        if msg.role is MessageRole.SYSTEM:
            pending.append(pa.SystemPromptPart(content=msg.text()))
        elif msg.role is MessageRole.USER:
            pending.append(pa.UserPromptPart(content=msg.text()))
        elif msg.role is MessageRole.TOOL:
            for p in msg.parts:
                if isinstance(p, ToolResultPart):
                    pending.append(
                        pa.ToolReturnPart(
                            tool_name=tool_names.get(p.call_id, "unknown"),
                            content=_result_text(p),
                            tool_call_id=p.call_id,
                        )
                    )
        else:  # assistant
            flush()
            parts: list[Any] = []
            for p in msg.parts:
                if isinstance(p, ThinkingPart):
                    if p.provider == family:
                        parts.append(_thinking_to_pa(p))
                elif isinstance(p, TextPart):
                    parts.append(pa.TextPart(content=p.text))
                elif isinstance(p, ToolCallPart):
                    tool_names[p.call_id] = p.name
                    parts.append(pa.ToolCallPart(p.name, dict(p.args), tool_call_id=p.call_id))
            out.append(pa.ModelResponse(parts=parts))
    flush()
    return out


# ----------------------------------------------------------------- provider -> canonical


def from_provider(response: pa.ModelResponse, *, family: str) -> Message:
    """A provider response -> canonical assistant message with fresh BioQuorum call IDs."""
    parts: list[Any] = []
    for p in response.parts:
        if isinstance(p, pa.ThinkingPart):
            meta = {
                "id": p.id,
                "signature": p.signature,
                "provider_name": p.provider_name,
                "provider_details": p.provider_details,
            }
            parts.append(
                ThinkingPart(
                    provider=family, text=p.content or None, opaque=json.dumps(meta).encode()
                )
            )
        elif isinstance(p, pa.TextPart):
            if p.content:
                parts.append(TextPart(text=p.content))
        elif isinstance(p, pa.ToolCallPart):
            # Malformed JSON comes back as {'INVALID_JSON': ...}; invoke() rejects it as bad args.
            parts.append(ToolCallPart(call_id=new_id(), name=p.tool_name, args=p.args_as_dict()))
    return Message(role=MessageRole.ASSISTANT, parts=parts)
