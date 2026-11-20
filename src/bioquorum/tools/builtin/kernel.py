"""``python_execute``: run code in the session's kernel and record a Run."""

from __future__ import annotations

from os.path import abspath
from pathlib import Path

from pydantic import BaseModel, Field

from bioquorum.core import events as ev
from bioquorum.core.enums import PermissionClass, Trust
from bioquorum.core.hashing import sha256_file
from bioquorum.core.messages import DataRefPart
from bioquorum.core.refs import DataRef
from bioquorum.policy.permissions import PermissionRequest
from bioquorum.runtime.kernel import EndStatus
from bioquorum.tools.builtin.download import DOWNLOAD_DIR, DOWNLOAD_FILE
from bioquorum.tools.context import ToolContext
from bioquorum.tools.runs import execute_run
from bioquorum.tools.source_stage import SOURCE_DIRECTORY, SOURCE_MARKER, SOURCE_STAGE_NAME
from bioquorum.tools.spec import ToolResult, ToolSpec


class PythonExecuteArgs(BaseModel):
    code: str = Field(description="Python code to run in the session's persistent kernel.")
    timeout_s: float = Field(default=600, gt=0, le=24 * 3600, description="Wall-clock limit.")
    input_files: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Declared ordinary project files or paths returned by download_file.",
    )


def _file_inputs(args: PythonExecuteArgs, ctx: ToolContext) -> tuple[list[DataRef], list[str]]:
    if not args.input_files:
        return [], []
    staged = {}
    markers = {DOWNLOAD_FILE.name: ", downloaded from ", SOURCE_STAGE_NAME: SOURCE_MARKER}
    for event in ctx.store.read(ctx.session_id):
        if event.type != ev.ToolCallFinished.event_type:
            continue
        payload = event.parsed()
        if (
            isinstance(payload, ev.ToolCallFinished)
            and payload.tool in markers
            and not payload.result.is_error
        ):
            for part in payload.result.content:
                if isinstance(part, DataRefPart):
                    path, marker, _ = part.summary.partition(markers[payload.tool])
                    if marker:
                        staged[path] = part.ref
    uploaded = {
        p.path: p.ref
        for e in ctx.store.history(ctx.session_id)
        if e.type == ev.FileRegistered.event_type and isinstance(p := e.parsed(), ev.FileRegistered)
    }
    root = ctx.workdir.resolve()
    refs: list[DataRef] = []
    paths: list[str] = []
    for path in dict.fromkeys(args.input_files):
        resolved = (root / path).resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(f"Declared input must be in this project: {path!r}")
        relative = resolved.relative_to(root)
        if relative.as_posix() == "bioquorum.toml" or any(
            part.startswith(".") for part in relative.parts
        ):
            raise ValueError(
                f"Declared input cannot be project configuration or hidden state: {path!r}"
            )
        requested = Path(abspath(root / path))
        expected_upload = uploaded.get(relative.as_posix())
        if requested.is_relative_to(root) and requested.relative_to(root).as_posix() in uploaded:
            if requested != resolved:
                raise ValueError("Uploaded input differs from its registered content")
            expected_upload = uploaded[requested.relative_to(root).as_posix()]
        area = relative.parts[0] if relative.parts else ""
        if area in (DOWNLOAD_DIR, SOURCE_DIRECTORY):
            ref = staged.get(relative.as_posix())
            if ref is None:
                kind = "download" if area == DOWNLOAD_DIR else "source staging event"
                raise ValueError(f"No recorded {kind} in this session for {path!r}")
            if not resolved.is_file() or sha256_file(resolved) != ref.content_hash:
                kind = "download" if area == DOWNLOAD_DIR else "source"
                raise ValueError(f"Declared input differs from its recorded {kind}: {path!r}")
        else:
            decision = ctx.permissions.lookup(
                PermissionRequest(
                    PermissionClass.READ_FS,
                    str(resolved),
                    ctx.session_id,
                    ctx.project_id,
                )
            )
            if decision is None or not decision.granted:
                raise ValueError(f"Permission to read the declared input was not granted: {path!r}")
            if not resolved.is_file():
                raise ValueError(f"Declared input is not a file: {path!r}")
            ref = ctx.require_cas().put_file(resolved)
        if expected_upload and ref.content_hash != expected_upload.content_hash:
            raise ValueError("Uploaded input differs from its registered content")
        refs.append(ref)
        paths.append(relative.as_posix())
    return refs, paths


async def _python_execute(args: PythonExecuteArgs, ctx: ToolContext) -> ToolResult:
    inputs, paths = _file_inputs(args, ctx)
    record = await execute_run(
        ctx, args.code, language="python", timeout=args.timeout_s, inputs=inputs, input_paths=paths
    )
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
