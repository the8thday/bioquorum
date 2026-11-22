"""Replay preserved Python/R cell chains against corrected, hash-verified inputs."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from tempfile import TemporaryDirectory

from bioquorum.core import events as ev
from bioquorum.core.enums import PermissionClass
from bioquorum.core.hashing import code_hash, sha256_file
from bioquorum.core.refs import DataRef
from bioquorum.core.verification import input_path
from bioquorum.runtime.manager import KernelManager, jupyter_factory
from bioquorum.runtime.sandbox import SandboxSpec
from bioquorum.tools.context import ToolContext
from bioquorum.tools.replay import (
    NoArguments,
    external_inputs,
    recorded_inputs,
    recorded_writes,
    stage_inputs,
)
from bioquorum.tools.runs import execute_run
from bioquorum.tools.spec import ToolResult, ToolSpec


@dataclass
class CellChain:
    cells: list[ev.RunStarted]
    writes: dict[str, dict[str, str]]
    # External inputs produced by a specific preceding session, keyed by input path.
    producers: dict[str, str] = field(default_factory=dict)


def corrected_replay(
    chains: list[CellChain],
    old_hash: str,
    replacement: DataRef,
    python: Path | None,
    sandbox: SandboxSpec,
    completed: dict[str, str],
    required_path: str | None = None,
) -> ToolSpec:
    """Run only after human authorization; changed intermediates receive fresh CAS references."""

    async def replay(args: NoArguments, ctx: ToolContext) -> ToolResult:
        cas = ctx.require_cas()
        outputs: dict[tuple[str, str], DataRef] = {}
        used_new_input = False
        for chain in chains:
            external = external_inputs(chain.cells, chain.writes)
            staged = {}
            for name, ref in external.items():
                if producer := chain.producers.get(name):
                    staged[name] = outputs[(producer, name)]
                elif ref.content_hash == old_hash and (
                    required_path is None or name == required_path
                ):
                    staged[name] = replacement
                    used_new_input = True
                else:
                    staged[name] = ref
            with TemporaryDirectory(prefix="bq-recompute-") as tmp:
                root = Path(tmp).resolve()
                stage_inputs(root, cas, staged)
                spec = replace(
                    sandbox,
                    workdir=root,
                    network=False,
                    hidden=(),
                    read_only=tuple(root / name for name in staged),
                )
                manager = KernelManager(
                    jupyter_factory(
                        root,
                        sandbox=spec,
                        require_isolation=True,
                        python_executable=python,
                        observe_lineage=True,
                    )
                )
                local = replace(ctx, workdir=root, kernels=manager)
                previous: dict[str, str] = {}
                current = dict(staged)
                try:
                    for cell in chain.cells:
                        if (
                            cell.synthetic
                            or cell.language not in ("python", "r")
                            or code_hash(cell.code) != cell.code_hash
                        ):
                            raise ValueError(
                                "Only recorded, non-synthetic Python/R cells can be recalculated"
                            )
                        if not cell.environment or cell.env_lock_hash != ctx.env_lock_hash:
                            raise ValueError("The recorded calculation environment is unavailable")
                        kernel = await manager.get(ctx.session_id, cell.language)
                        if cell.language in previous and previous[cell.language] != cell.kernel_id:
                            await kernel.restart()
                        previous[cell.language] = cell.kernel_id
                        if (
                            kernel.isolation not in ("seatbelt", "bwrap")
                            or getattr(kernel, "environment", None) != cell.environment
                        ):
                            raise ValueError(
                                "The required isolated kernel environment is unavailable"
                            )
                        refs = []
                        for name in recorded_inputs([cell]):
                            path = root / input_path(name)
                            if (
                                path.resolve() != path
                                or not path.is_file()
                                or sha256_file(path) != current[name].content_hash
                            ):
                                raise ValueError(
                                    "A recalculated input no longer matches its recorded producer"
                                )
                            refs.append(current[name])
                        writes = chain.writes.get(cell.run_id, {})
                        result = await execute_run(
                            local,
                            cell.code,
                            language=cell.language,
                            inputs=refs,
                            input_paths=cell.input_paths,
                            output_paths=list(writes),
                            timeout=600,
                        )
                        if result.status.value != "ok":
                            raise ValueError(
                                f"Cell {cell.run_id} failed in the new session; see its recorded kernel result"
                            )
                        completed[cell.run_id] = result.run_id
                        if writes:
                            observations = ctx.store.read(
                                ctx.session_id, types=(ev.LineageObserved.event_type,)
                            )
                            produced = recorded_writes(observations).get(result.run_id, {})
                            if produced.keys() != writes.keys():
                                raise ValueError("A declared intermediate output was not recorded")
                            for name, digest in produced.items():
                                content = cas.verified_path(digest)
                                output_ref = DataRef(
                                    uri=cas.uri(digest),
                                    content_hash=digest,
                                    size=content.stat().st_size,
                                )
                                current[name] = output_ref
                                outputs[cell.run_id, name] = output_ref
                finally:
                    await manager.shutdown_all()
        if not used_new_input:
            raise ValueError(
                "The selected cell chains have no declared external input matching the corrected material"
            )
        return ToolResult.text("Recalculated the recorded cell chains.")

    return ToolSpec(
        "recompute_recorded_cells",
        "Rerun the selected recorded cells using corrected material.",
        replay,
        PermissionClass.EXECUTE,
        args_model=NoArguments,
    )
