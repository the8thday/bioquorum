"""Canonical message format (ARCHITECTURE.md §5.4, ADR-003).

The event log and the model context share these types. Provider-specific types
(Pydantic AI, Anthropic SDK, ...) are converted to and from this format only inside
``bioquorum.models.convert``.
"""

from __future__ import annotations

import base64
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer

from bioquorum.core.enums import MessageRole, Trust
from bioquorum.core.refs import DataRef


def _b64_decode(value: Any) -> Any:
    return base64.b64decode(value) if isinstance(value, str) else value


OpaqueBytes = Annotated[
    bytes,
    BeforeValidator(_b64_decode),
    PlainSerializer(
        lambda b: base64.b64encode(b).decode("ascii"), return_type=str, when_used="json"
    ),
]
"""Raw bytes in Python, base64 in JSON. For provider payloads nobody is allowed to parse."""


class _Part(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TextPart(_Part):
    type: Literal["text"] = "text"
    text: str


class ThinkingPart(_Part):
    """Provider reasoning. ``opaque`` is replayable only to the same provider."""

    type: Literal["thinking"] = "thinking"
    provider: str
    opaque: OpaqueBytes | None = None
    text: str | None = None


class ToolCallPart(_Part):
    type: Literal["tool_call"] = "tool_call"
    call_id: str = Field(description="BioQuorum-assigned ULID; provider IDs stay in models/")
    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class ImageRefPart(_Part):
    type: Literal["image_ref"] = "image_ref"
    ref: DataRef
    mime: str
    thumbnail_ref: DataRef | None = None


class DataRefPart(_Part):
    type: Literal["data_ref"] = "data_ref"
    ref: DataRef
    summary: str


ResultContent = Annotated[TextPart | ImageRefPart | DataRefPart, Field(discriminator="type")]


class ToolResultPart(_Part):
    type: Literal["tool_result"] = "tool_result"
    call_id: str
    content: list[ResultContent] = Field(default_factory=list)
    trust: Trust = Trust.UNTRUSTED
    is_error: bool = False


Part = Annotated[
    TextPart | ThinkingPart | ToolCallPart | ToolResultPart | ImageRefPart | DataRefPart,
    Field(discriminator="type"),
]


class Message(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    role: MessageRole
    parts: list[Part]

    @classmethod
    def system(cls, text: str) -> Message:
        return cls(role=MessageRole.SYSTEM, parts=[TextPart(text=text)])

    @classmethod
    def user(cls, text: str) -> Message:
        return cls(role=MessageRole.USER, parts=[TextPart(text=text)])

    @classmethod
    def assistant(cls, text: str) -> Message:
        return cls(role=MessageRole.ASSISTANT, parts=[TextPart(text=text)])

    @classmethod
    def tool(cls, result: ToolResultPart) -> Message:
        return cls(role=MessageRole.TOOL, parts=[result])

    def text(self) -> str:
        """Concatenated text of all TextParts (thinking excluded)."""
        return "".join(p.text for p in self.parts if isinstance(p, TextPart))

    def tool_calls(self) -> list[ToolCallPart]:
        return [p for p in self.parts if isinstance(p, ToolCallPart)]

    def has_untrusted_content(self) -> bool:
        return any(isinstance(p, ToolResultPart) and p.trust is Trust.UNTRUSTED for p in self.parts)
