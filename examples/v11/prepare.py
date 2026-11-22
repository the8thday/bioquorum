"""Build a public, offline research line from the deposited 1N8Z files."""

import argparse
import asyncio
import gzip
import json
from pathlib import Path

from bioquorum.application import artifacts, materials, program_record, programs, workspace
from bioquorum.application.controls import ResearchControls
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, ClaimKind, Classification, PermissionClass, PermissionScope
from bioquorum.core.hashing import sha256_bytes
from bioquorum.core.ids import new_id
from bioquorum.core.messages import Message
from bioquorum.core.status import StatusTrigger, VerificationStatus
from bioquorum.policy.permissions import PermissionPolicy
from bioquorum.review.binding import ground_claim
from bioquorum.review.evidence import Evidence
from bioquorum.tools.builtin.files import READ_FILE
from bioquorum.tools.builtin.kernel import PYTHON_EXECUTE
from bioquorum.tools.invoke import invoke

EXAMPLE = Path(__file__).resolve().parent
QUESTION = "Where does the trastuzumab Fab contact HER2 in the deposited structure?"
OBSERVATIONS = (
    (
        ClaimKind.NUMERIC,
        "At {{contact_cutoff}} Å, {{fab_sites}} Fab residues contact {{her2_sites}} receptor residues, forming {{residue_pairs}} distinct residue pairs.",
    ),
    (
        ClaimKind.CITATION,
        'The original paper locates binding near the membrane [@PMID:12610629 "Herceptin binds to the juxtamembrane region of HER2"].',
    ),
)


def checked_inputs():
    folder = EXAMPLE / "data"
    for item in json.loads((folder / "manifest.json").read_text()):
        path = folder / item["name"]
        data = (
            gzip.decompress(path.with_suffix(".cif.gz").read_bytes())
            if path.suffix == ".cif"
            else path.read_bytes()
        )
        if len(data) != item["bytes"] or sha256_bytes(data) != item["sha256"]:
            raise ValueError(f"Input snapshot mismatch: {path.name}")
        yield item, data


async def chunks(data):
    yield data


def record_observations(controls, sid):
    evidence = Evidence.from_events(controls.store.history(sid))
    bound = [(kind, text, ground_claim(text, evidence)) for kind, text in OBSERVATIONS]
    if any(binding.state != "grounded" for _, _, binding in bound):
        raise ValueError(f"Evidence binding failed: {bound}")
    message = Message.assistant("\n\n".join(text for _, text, _ in bound))
    answer = controls.store.append(
        ev.AssistantMessage(message=message), session_id=sid, actor=Actor.AGENT
    )
    for kind, text, binding in bound:
        claim = ev.ClaimExtracted(
            claim_id=new_id(),
            kind=kind,
            source_event_id=answer.id,
            text=text,
            refs=list(binding.refs),
        )
        controls.store.append(claim, session_id=sid, actor=Actor.REVIEWER)
        controls.store.append(
            ev.ClaimStatusChanged(
                claim_id=claim.claim_id,
                from_status=VerificationStatus.UNVERIFIED,
                to_status=VerificationStatus.GROUNDED,
                trigger=StatusTrigger.BINDINGS_RESOLVED,
            ),
            session_id=sid,
            actor=Actor.REVIEWER,
        )


async def seed(root: Path):
    inputs = list(checked_inputs())
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise ValueError("Choose a new or empty project directory.")
    ws = workspace.init_project(
        root, name="Trastuzumab and HER2", classification=Classification.PUBLIC
    )
    (root / workspace.CONFIG_FILE).write_text(
        "[kernel]\nmacos_sandbox = true\nnetwork = false\npixi = false\n\n[agent]\nreferences = true\n"
    )
    ws = workspace.load(root)
    ws.kernel_isolation()
    controls = ResearchControls(ws)
    pid = (await programs.create(controls, "Trastuzumab Fab binding to HER2"))["id"]
    sid = await programs.new_session(controls, pid, "1N8Z interface and primary evidence")
    paths = {}
    policy = PermissionPolicy()
    for item, data in inputs:
        file = await artifacts.upload(
            controls, sid, item["name"], Classification.PUBLIC, chunks(data)
        )
        await materials.register(controls, pid, file["id"], item["kind"], item["source"])
        paths[item["name"]] = file["path"]
        policy.grant(
            PermissionClass.READ_FS,
            str(root / file["path"]),
            PermissionScope.SESSION,
            session_id=sid,
        )
    policy.grant(PermissionClass.EXECUTE, "python", PermissionScope.SESSION, session_id=sid)
    ctx = workspace.tool_context(ws, controls.store, sid, policy)
    store = controls.store
    request = store.append(ev.TurnRequested(question=QUESTION), session_id=sid, actor=Actor.USER)
    store.append(ev.TurnStarted(turn_id=request.id), session_id=sid, actor=Actor.SYSTEM)
    store.append(ev.UserMessage(message=Message.user(QUESTION)), session_id=sid, actor=Actor.USER)
    try:
        source = await invoke(READ_FILE, {"path": paths["primary-source.md"]}, ctx)
        if source.is_error:
            raise RuntimeError(source.text())
        cif, fasta = paths["1N8Z.cif"], paths["1N8Z.fasta"]
        code = (EXAMPLE / "interface.py").read_text() + (
            f"\ncounts = analyze(Path({cif!r}), Path({fasta!r}), Path('.'))\n"
            "bq.value('contact_cutoff', CUTOFF, unit='angstrom')\n"
            "for name, count in counts.items():\n    bq.value(name, count)\n"
        )
        result = await invoke(PYTHON_EXECUTE, {"code": code, "input_files": [cif, fasta]}, ctx)
        if result.is_error:
            raise RuntimeError(result.text())
        record_observations(controls, sid)
    except Exception:
        store.append(
            ev.TurnFinished(turn_id=request.id, outcome="failed"),
            session_id=sid,
            actor=Actor.SYSTEM,
        )
        raise
    finally:
        await ctx.require_kernels().shutdown_all()
    store.append(
        ev.TurnFinished(turn_id=request.id, outcome="completed"), session_id=sid, actor=Actor.SYSTEM
    )
    handoff = program_record.handoff(controls, pid)
    return {
        "program": pid,
        "session": sid,
        "claims": [c["text"] for c in programs.show(controls, pid)["claims"]],
        "handoff_snapshot": handoff["snapshot_event_id"],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    print(json.dumps(asyncio.run(seed(parser.parse_args().project)), indent=2, ensure_ascii=False))
