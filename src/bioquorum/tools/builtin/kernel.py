"""``python_execute``: run code in the session's kernel and record a Run."""

from __future__ import annotations

from pydantic import BaseModel, Field

from bioquorum.core.enums import PermissionClass, Trust
from bioquorum.runtime.kernel import EndStatus
from bioquorum.tools.context import ToolContext
from bioquorum.tools.runs import execute_run
from bioquorum.tools.spec import ToolResult, ToolSpec


class PythonExecuteArgs(BaseModel):
    code: str = Field(description="Python code to run in the session's persistent kernel.")
    timeout_s: float = Field(default=600, gt=0, le=24 * 3600, description="Wall-clock limit.")


async def _python_execute(args: PythonExecuteArgs, ctx: ToolContext) -> ToolResult:
    record = await execute_run(ctx, args.code, language="python", timeout=args.timeout_s)
    return ToolResult(content=record.content, is_error=record.status is not EndStatus.OK)


PYTHON_EXECUTE = ToolSpec(
    name="python_execute",
    description=(
        "Execute Python code in a persistent Jupyter kernel for this session. Variables persist "
        "between calls. Large data stays in the kernel: print summaries (shape, head, describe), "
        "not whole tables. Figures are saved automatically. Returns a run_id you can cite."
    ),
    handler=_python_execute,
    args_model=PythonExecuteArgs,
    permission=PermissionClass.EXECUTE,
    # Kernel output can echo file contents, so it is not trusted (ADR-007).
    output_trust=Trust.UNTRUSTED,
    classification_floor=None,
    target=lambda _args, _ctx: "python",
)
