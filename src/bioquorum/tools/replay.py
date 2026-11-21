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


def recorded_writes(events: Iterable[ev.Event]) -> dict[str, dict[str, str]]:
    writes: dict[str, dict[str, str]] = {}
    for event in events:
        if event.type != ev.LineageObserved.event_type:
            continue
        p = event.parsed()
        if (
            isinstance(p, ev.LineageObserved)
            and p.function == "declared_write"
            and p.direction == "output"
            and p.post_run_hash
        ):
            writes.setdefault(p.run_id, {})[p.path] = p.post_run_hash
    return writes


def dependency_chain(events: Sequence[ev.Event], target: ev.RunStarted) -> list[ev.RunStarted]:
    """Follow kernel state and prior same-path, same-content file producers."""
    writes = recorded_writes(events)
    latest: dict[str, tuple[str, str]] = {}
    parents: dict[str, list[str]] = {}
    ordered = []
    for event in events:
        p = event.parsed()
        if isinstance(p, ev.RunFinished) and p.run_id in parents:
            for name, digest in writes.get(p.run_id, {}).items():
                latest[name] = (p.run_id, digest)
        elif isinstance(p, ev.RunStarted) and p.verification_run == target.verification_run:
            ordered.append(p)
            parents[p.run_id] = [
                latest[name][0]
                for name, ref in zip(p.input_paths, p.inputs, strict=False)
                if name in latest
                and latest[name][1] == ref.content_hash
                and latest[name][0] in parents
            ]
            if p.run_id == target.run_id:
                break
    by_id = {p.run_id: p for p in ordered}
    if target.run_id not in by_id:
        raise ValueError("The target run is missing from the recorded history")
    needed: set[str] = set()
    pending = [target.run_id]
    while pending:
        rid = pending.pop()
        if rid not in needed:
            for cell in run_chain(events, by_id[rid]):
                if cell.run_id not in needed:
                    needed.add(cell.run_id)
                    pending.extend(parents[cell.run_id])
    return [cell for cell in ordered if cell.run_id in needed]


def external_inputs(
    chain: Sequence[ev.RunStarted], writes: dict[str, dict[str, str]]
) -> dict[str, DataRef]:
    produced: dict[str, str] = {}
    inputs: dict[str, DataRef] = {}
    for cell in chain:
        for path, ref in recorded_inputs([cell]).items():
            if produced.get(path) != ref.content_hash:
                if (
                    path in inputs and inputs[path].content_hash != ref.content_hash
                ) or path in produced:
                    raise ValueError("An input path changed outside the recorded calculation")
                inputs[path] = ref
        produced.update(writes.get(cell.run_id, {}))
    return inputs


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


def replay_spec(
    chain: Sequence[ev.RunStarted], writes: dict[str, dict[str, str]] | None = None
) -> ToolSpec:
    writes = writes or {}

    async def handler(args: NoArguments, ctx: ToolContext) -> ToolResult:
        stage_inputs(ctx.workdir, ctx.require_cas(), external_inputs(chain, writes))
        runs = []
        previous: dict[str, str] = {}
        for start in chain:
            kernel = await ctx.require_kernels().get(ctx.session_id, start.language)
            if start.language in previous and previous[start.language] != start.kernel_id:
                await kernel.restart()
            previous[start.language] = start.kernel_id
            if (
                kernel.isolation != start.isolation
                or getattr(kernel, "environment", None) != start.environment
            ):
                return ToolResult.text(
                    "Recorded isolation or environment is unavailable", is_error=True
                )
            for name, ref in recorded_inputs([start]).items():
                if sha256_file(ctx.workdir / name) != ref.content_hash:
                    return ToolResult.text(
                        "Recreated input differs from its recorded hash", is_error=True
                    )
            result = await execute_run(
                ctx,
                start.code,
                language=start.language,
                inputs=start.inputs,
                input_paths=start.input_paths,
                output_paths=tuple(writes.get(start.run_id, {})),
                timeout=600,
            )
            if result.status.value != "ok":
                return ToolResult.text("A recorded cell failed in the fresh kernel", is_error=True)
            for name, digest in writes.get(start.run_id, {}).items():
                path = ctx.workdir / input_path(name)
                if path.resolve() != path or not path.is_file() or sha256_file(path) != digest:
                    return ToolResult.text(
                        "Recreated output differs from its recorded hash", is_error=True
                    )
            runs.append(result.run_id)
        return ToolResult.text(json.dumps(runs))

    return ToolSpec(
        "replay_recorded_run",
        "Rerun an explicitly chosen recorded analysis in a fresh kernel.",
        handler,
        PermissionClass.EXECUTE,
        args_model=NoArguments,
        target=lambda *_: "recorded-calculation",
    )
