"""Read-only file tools. Paths are resolved (symlinks followed) before permission checks."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from bioquorum.core.enums import PermissionClass, Trust
from bioquorum.core.hashing import sha256_file
from bioquorum.runtime.summarize import truncate
from bioquorum.tools.context import ToolContext
from bioquorum.tools.spec import ToolResult, ToolSpec

MAX_READ_BYTES = 2_000_000
MAX_LIST = 500


def resolve(path: str, ctx: ToolContext) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = ctx.workdir / p
    return p.resolve()


class ReadFileArgs(BaseModel):
    path: str = Field(description="File path, absolute or relative to the project directory.")
    max_chars: int = Field(default=20_000, gt=0, le=200_000)


class ListFilesArgs(BaseModel):
    path: str = Field(default=".", description="Directory to list.")


async def _read_file(args: ReadFileArgs, ctx: ToolContext) -> ToolResult:
    p = resolve(args.path, ctx)
    if not p.is_file():
        return ToolResult.text(f"not a file: {p}", is_error=True)
    size = p.stat().st_size
    header = f"path: {p}\nsize: {size}\nsha256: {sha256_file(p)}"
    with p.open("rb") as fh:
        raw = fh.read(MAX_READ_BYTES)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return ToolResult.text(f"{header}\n(binary file; load it in python_execute instead)")
    return ToolResult.text(f"{header}\n---\n{truncate(text, args.max_chars)}")


async def _list_files(args: ListFilesArgs, ctx: ToolContext) -> ToolResult:
    p = resolve(args.path, ctx)
    if not p.is_dir():
        return ToolResult.text(f"not a directory: {p}", is_error=True)
    entries = sorted(p.iterdir(), key=lambda e: e.name)
    lines = [
        f"{e.name}/" if e.is_dir() else f"{e.name}\t{e.stat().st_size}" for e in entries[:MAX_LIST]
    ]
    if len(entries) > MAX_LIST:
        lines.append(f"... {len(entries) - MAX_LIST} more")
    return ToolResult.text(f"{p}\n" + "\n".join(lines))


READ_FILE = ToolSpec(
    name="read_file",
    description="Read a text file (truncated if large). Use python_execute for data files.",
    handler=_read_file,
    args_model=ReadFileArgs,
    permission=PermissionClass.READ_FS,
    output_trust=Trust.UNTRUSTED,
    target=lambda a, ctx: str(resolve(a.path, ctx)),
)

LIST_FILES = ToolSpec(
    name="list_files",
    description="List a directory: names, and sizes in bytes for files.",
    handler=_list_files,
    args_model=ListFilesArgs,
    permission=PermissionClass.READ_FS,
    output_trust=Trust.UNTRUSTED,
    target=lambda a, ctx: str(resolve(a.path, ctx)),
)
