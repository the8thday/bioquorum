"""Private replay commands. They are never exposed to the worker's tool registry."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import BaseModel

from bioquorum.core import events as ev
from bioquorum.core.enums import PermissionClass
from bioquorum.core.hashing import sha256_file
from bioquorum.core.refs import DataRef
from bioquorum.core.verification import input_path
from bioquorum.provenance.cas import ContentStore
from bioquorum.tools.context import ToolContext
from bioquorum.tools.runs import execute_run
from bioquorum.tools.spec import ToolResult, ToolSpec


def recorded_inputs(runs: Iterable[ev.RunStarted]) -> dict[str, DataRef]:
    refs: dict[str, DataRef] = {}
    for run in runs:
        if len(run.input_paths) != len(run.inputs):
            raise ValueError("The original run does not record the locations of its inputs")
        for name, ref in zip(run.input_paths, run.inputs, strict=True):
            name = input_path(name).as_posix()
            if ref.weak_hash:
                raise ValueError("The recorded input has no strong hash")
            if name in refs and refs[name].content_hash != ref.content_hash:
                raise ValueError("The recorded runs used different versions of the same input path")
            refs[name] = ref
    return refs


def run_chain(events: Iterable[ev.Event], target: ev.RunStarted) -> list[ev.RunStarted]:
    """Reconstruct the completed calls that populated the target's kernel."""
    chain = []
    successful: set[str] = set()
    reached = False
    for event in events:
        payload = event.parsed()
        if isinstance(payload, ev.RunFinished):
            successful.add(payload.run_id)
        elif (
            not reached
            and isinstance(payload, ev.RunStarted)
            and payload.kernel_id == target.kernel_id
            and payload.verification_run == target.verification_run
        ):
            chain.append(payload)
            reached = payload.run_id == target.run_id
    if not reached or any(run.run_id not in successful for run in chain):
        raise ValueError("A prior cell in the kernel chain is incomplete or failed")
    return chain


def stage_inputs(root: Path, cas: ContentStore, refs: dict[str, DataRef]) -> None:
    for name, ref in refs.items():
        path = root / input_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Stream large inputs and verify the exact bytes copied before kernel startup.
        with TemporaryDirectory(dir=root) as staging:
            from shutil import copyfile

            candidate = Path(staging) / "input"
            copyfile(cas.path(ref.content_hash), candidate)
            if candidate.stat().st_size != ref.size or sha256_file(candidate) != ref.content_hash:
                raise ValueError("A recorded input failed its integrity check")
            candidate.replace(path)


class NoArguments(BaseModel):
    pass


def replay_spec(chain: Sequence[ev.RunStarted]) -> ToolSpec:
    async def handler(args: NoArguments, ctx: ToolContext) -> ToolResult:
        stage_inputs(ctx.workdir, ctx.require_cas(), recorded_inputs(chain))
        runs = []
        for start in chain:
            result = await execute_run(
                ctx,
                start.code,
                language="python",
                inputs=start.inputs,
                input_paths=start.input_paths,
                timeout=600,
            )
            if result.status.value != "ok":
                return ToolResult.text("A recorded cell failed in the fresh kernel", is_error=True)
            runs.append(result.run_id)
        return ToolResult.text(json.dumps(runs))

    return ToolSpec(
        "replay_recorded_run",
        "Rerun an explicitly chosen recorded analysis in a fresh kernel.",
        handler,
        PermissionClass.EXECUTE,
        args_model=NoArguments,
        target=lambda *_: "python",
    )
