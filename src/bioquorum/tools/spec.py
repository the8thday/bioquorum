"""Tool definitions (ARCHITECTURE.md §6.4, ADR-005).

A ToolSpec is declared once and feeds both exits: the agent's tool list and the MCP
server's tool list. Built-in tools give a Pydantic ``args_model`` (validated by invoke);
wrapped external MCP tools only carry the server's JSON Schema.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Generic

from pydantic import BaseModel

from bioquorum.core.enums import Classification, PermissionClass, Trust
from bioquorum.core.messages import ResultContent, TextPart
from bioquorum.tools.context import SessionId, ToolContext


@dataclass
class ToolResult:
    content: list[ResultContent] = field(default_factory=list)
    is_error: bool = False

    @classmethod
    def text(cls, text: str, *, is_error: bool = False) -> ToolResult:
        return cls(content=[TextPart(text=text)], is_error=is_error)


Handler = Callable[[Any, ToolContext[SessionId]], Awaitable[ToolResult]]
"""``(args, ctx) -> ToolResult``. ``args`` is an ``args_model`` instance, or a dict."""

TargetFn = Callable[[Any, ToolContext[SessionId]], str]
"""``(args, ctx) -> target``; must resolve exactly what the handler will touch."""


@dataclass(frozen=True)
class ToolSpec(Generic[SessionId]):  # noqa: UP046 - Python 3.12 needs TypeVar for defaults.
    name: str
    description: str
    handler: Handler[SessionId]
    permission: PermissionClass
    output_trust: Trust = Trust.UNTRUSTED
    classification_floor: Classification | None = None
    """Most sensitive project classification whose data may reach this tool's destination.
    ``None``: data never leaves the machine (see policy.classification)."""
    args_model: type[BaseModel] | None = None
    raw_input_schema: dict[str, Any] | None = None
    target: TargetFn[SessionId] | None = None
    """What the permission is about (a path, a host, ...). Defaults to the tool name."""

    def __post_init__(self) -> None:
        if (self.args_model is None) == (self.raw_input_schema is None):
            raise ValueError(f"{self.name}: give exactly one of args_model / raw_input_schema")
        if self.output_trust is Trust.TRUSTED and self.permission in (
            PermissionClass.NETWORK,
            PermissionClass.CONNECTOR,
            PermissionClass.EXTERNAL_MCP,
        ):
            raise ValueError(f"{self.name}: remote content can never be trusted (ADR-007)")

    @property
    def input_schema(self) -> dict[str, Any]:
        if self.args_model is not None:
            return self.args_model.model_json_schema()
        assert self.raw_input_schema is not None
        return self.raw_input_schema

    def parse_args(self, args: dict[str, Any]) -> Any:
        return self.args_model.model_validate(args) if self.args_model is not None else args

    def target_of(self, parsed_args: Any, ctx: ToolContext[SessionId]) -> str:
        return self.target(parsed_args, ctx) if self.target is not None else self.name
