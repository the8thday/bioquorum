"""Export a scoped, portable research record via the audited tool surface."""

from __future__ import annotations

import os
from collections.abc import Mapping
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

from bioquorum import __version__
from bioquorum.core import events as ev
from bioquorum.core.bundle_v1 import ManifestV1, PortableRun
from bioquorum.core.enums import Actor, PermissionClass
from bioquorum.core.hashing import canonical_json, env_lock_hash, sha256_file
from bioquorum.core.ids import new_id
from bioquorum.core.messages import Message, TextPart
from bioquorum.core.refs import DataRef
from bioquorum.provenance.manifest import build_manifest
from bioquorum.provenance.methods import methods_draft
from bioquorum.runtime.values import PRELUDE
from bioquorum.tools.context import ToolContext
from bioquorum.tools.replay import NoArguments, recorded_inputs, run_chain
from bioquorum.tools.spec import ToolResult, ToolSpec

SHELL = """#!/bin/sh
set -eu
cd -- "$(dirname -- "$0")"
"${PYTHON:-python3}" run.py --verify-only
if [ "${1:-}" = "--verify-only" ]; then
    exit 0
fi
if [ -f env/pixi.lock ]; then
    exec pixi run --locked --manifest-path env/pixi.toml python "$PWD/run.py"
fi
exec "${PYTHON:-python3}" run.py
"""


def _event_row(event: ev.Event) -> dict | None:
    payload = event.parsed()
    if isinstance(payload, ev.AssistantMessage | ev.UserMessage):
        # Provider reasoning, tool arguments and raw payloads are excluded.
        if isinstance(payload, ev.AssistantMessage) and payload.message.tool_calls():
            return None
        clean = Message(role=payload.message.role, parts=[TextPart(text=payload.message.text())])
        changes: dict[str, object] = {"message": clean}
        if isinstance(payload, ev.AssistantMessage):
            changes["provider_raw"] = None
        payload = payload.model_copy(update=changes)
    elif not isinstance(
        payload,
        ev.SessionCreated
        | ev.SessionForked
        | ev.PlanProposed
        | ev.PlanApproved
        | ev.ClaimExtracted
        | ev.ClaimStatusChanged
        | ev.ReviewFinding
        | ev.RerunFinished
        | ev.ReplicationFinished
        | ev.LiteratureFinished
        | ev.LineageObserved,
    ):
        return None
    return {
        "id": event.id,
        "session_id": event.session_id,
        "type": event.type,
        "actor": event.actor.value,
        "timestamp": event.ts.isoformat(),
        "payload": payload.model_dump(mode="json"),
    }


def export_spec(project_name: str, evidence_sources: Mapping[str, str]) -> ToolSpec:
    async def handler(args: NoArguments, ctx: ToolContext) -> ToolResult:
        event_log = ctx.store.history(ctx.session_id)
        manifest = build_manifest(
            event_log,
            project_name=project_name,
            classification=ctx.classification,
            tool_version=__version__,
        )
        ends: dict[str, ev.RunFinished] = {}
        starts: dict[str, ev.RunStarted] = {}
        claims = []
        for event in event_log:
            payload = event.parsed()
            if isinstance(payload, ev.RunStarted):
                starts[payload.run_id] = payload
            elif isinstance(payload, ev.RunFinished):
                ends[payload.run_id] = payload
            elif isinstance(payload, ev.ClaimExtracted):
                claims.append(payload.model_dump(mode="json"))
        selected = []
        records = []
        blobs: dict[str, DataRef] = {}
        bundle_id = new_id()
        cas = ctx.require_cas()
        exports = cas.root.parent / "exports"
        exports.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="bq-export-", dir=exports) as tmp:
            root = Path(tmp)
            draft = root / "bundle"
            draft.mkdir()

            def write(name: str, content: bytes) -> None:
                dest = draft / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(content)

            accepted: set[str] = set()
            for run in manifest.runs:
                start = starts[run.run_id]
                end = ends.get(run.run_id)
                if not end or run.language != "python":
                    continue
                try:
                    chain = run_chain(event_log, start)
                    if not {p.run_id for p in chain[:-1]}.issubset(accepted):
                        continue
                    inputs = recorded_inputs(chain)
                except ValueError:
                    continue  # retain its claims, but omit incomplete replay records
                selected.append(run)
                accepted.add(run.run_id)
                write(run.code_path, start.code.encode())
                for ref in [
                    *inputs.values(),
                    *run.outputs,
                    *([end.stdout_ref] if end.stdout_ref else []),
                ]:
                    blobs[ref.content_hash] = ref
                records.append(
                    PortableRun(
                        run_id=start.run_id,
                        inputs={path: ref.content_hash for path, ref in inputs.items()},
                        stdout_sha256=end.stdout_ref.content_hash if end.stdout_ref else None,
                        environment=start.environment,
                        synthetic=start.synthetic,
                        preceding_runs=[p.run_id for p in chain[:-1]],
                    )
                )
            if not selected and not evidence_sources:
                raise ValueError("The session has no exportable runs or retrieved evidence")

            pixi = None
            locks = {run.env_lock_hash for run in selected}
            if selected and locks != {None}:
                manifest_file, lock_file = ctx.workdir / "pixi.toml", ctx.workdir / "pixi.lock"
                if len(locks) != 1 or not manifest_file.is_file() or not lock_file.is_file():
                    raise ValueError("The selected runs do not share an available pixi environment")
                lock = lock_file.read_bytes()
                if env_lock_hash(lock) not in locks:
                    raise ValueError("The pixi lock differs from the recorded environment")
                config = manifest_file.read_bytes()
                # Remote environment manifests can themselves carry secrets or setup commands.
                # This archive exports the user's local, explicit project lock.
                write("env/pixi.toml", config)
                write("env/pixi.lock", lock)
                pixi = "env/pixi.lock"

            copied_artifacts = []
            for index, artifact in enumerate(manifest.artifacts):
                path = cas.path(artifact.content_hash)
                if not path.is_file() or sha256_file(path) != artifact.content_hash:
                    continue
                internal = f"artifacts/{index:04d}-{artifact.content_hash}"
                write(internal, path.read_bytes())
                copied_artifacts.append(artifact.model_copy(update={"path": internal}))
            write("review/claims.json", canonical_json(claims))
            write("review/sources.json", canonical_json(dict(evidence_sources)))
            write(
                "Methods.md", methods_draft(event_log, selected, records, evidence_sources).encode()
            )
            rows = (_event_row(event) for event in event_log)
            write(
                "conversation.jsonl",
                b"".join(canonical_json(row) + b"\n" for row in rows if row is not None),
            )
            write("values.py", PRELUDE.encode())
            runner = files("bioquorum.runtime").joinpath("portable_runner.py").read_bytes()
            write("run.py", runner)
            write("rerun.sh", SHELL.encode())
            (draft / "rerun.sh").chmod(0o755)
            overview = (
                "# BioQuorum research bundle\n\n"
                "Use `sh rerun.sh --verify-only` to check file integrity. `Methods.md` contains "
                "an editable draft generated from the research record.\n\n"
            )
            replay = (
                "Review the archived code before running it. For trusted public bundles, run "
                "`sh rerun.sh`. The locked route requires pixi; the unlocked route requires "
                "a Python interpreter with jupyter_client and ipykernel. Set `PYTHON` to select it. "
                "The comparison reports environment identity separately.\n"
                if selected
                else "No computational runs are included. The archive supports integrity checks.\n"
            )
            write(
                "README.md", (overview + replay + "\nShare with authorized recipients.\n").encode()
            )

            metadata = {
                p.relative_to(draft).as_posix(): sha256_file(p)
                for p in draft.rglob("*")
                if p.is_file()
            }
            for ref in blobs.values():
                path = cas.path(ref.content_hash)
                if sha256_file(path) != ref.content_hash:
                    raise ValueError("An exported data object failed its integrity check")
                metadata["objects/" + ref.content_hash] = ref.content_hash
            payload = manifest.model_dump(
                mode="json", exclude={"spec_version", "runs", "artifacts", "files"}
            )
            packed = ManifestV1(
                **payload,
                runs=selected,
                artifacts=copied_artifacts,
                files=metadata,
                run_records=records,
                pixi_lock_path=pixi,
            )
            write("manifest.json", packed.model_dump_json(indent=2).encode())
            filename = f"{bundle_id}.zip"
            temp_zip = root / "record.zip"
            with ZipFile(temp_zip, "w", compression=ZIP_DEFLATED) as archive:
                for path in sorted(draft.rglob("*")):
                    if path.is_file():
                        archive.write(path, path.relative_to(draft).as_posix())
                for ref in blobs.values():
                    archive.write(cas.path(ref.content_hash), "objects/" + ref.content_hash)
            os.chmod(temp_zip, 0o600)
            temp_zip.replace(exports / filename)
            manifest_hash = sha256_file(draft / "manifest.json")
        ctx.store.append(
            ev.BundleExported(bundle_id=bundle_id, path=filename, manifest_hash=manifest_hash),
            session_id=ctx.session_id,
            actor=Actor.USER,
            causation_id=ctx.requested_event_id,
        )
        return ToolResult.text(bundle_id)

    return ToolSpec(
        "export_research_bundle",
        "Export the selected session as a research package.",
        handler,
        PermissionClass.WRITE_FS,
        args_model=NoArguments,
        target=lambda *_: "research-bundle",
    )
