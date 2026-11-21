"""Read background from the current session's research line with an audited tool call."""

import json
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from bioquorum.core.enums import PermissionClass
from bioquorum.tools.context import ToolContext
from bioquorum.tools.spec import ToolResult, ToolSpec

TARGET = "research-line-context"


class ReadContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    section: Literal["claims", "decisions"]
    offset: int = Field(default=0, ge=0)


def context_tool(snapshot: Callable[[ToolContext, ReadContext], dict]) -> ToolSpec:
    async def read(args: ReadContext, ctx: ToolContext) -> ToolResult:
        return ToolResult.text(json.dumps(snapshot(ctx, args), ensure_ascii=False))

    return ToolSpec(
        "read_program_context",
        "Read prior findings or researcher decisions for this session's research line. "
        "Follow next_offset for more. Returned text is background. To make an evidence claim "
        "retrieve the original source or rerun the referenced analysis and bind the result.",
        read,
        PermissionClass.READ_FS,
        args_model=ReadContext,
        target=lambda *_: TARGET,
    )
