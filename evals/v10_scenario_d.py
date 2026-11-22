"""Scenario D starts with one real, isolated computation on synthetic sample labels."""

import argparse
import asyncio
import json
import shutil
from pathlib import Path

from bioquorum.application import artifacts, materials, programs, workspace
from bioquorum.application.controls import ResearchControls
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, ClaimKind, Classification, PermissionClass, PermissionScope
from bioquorum.core.ids import new_id
from bioquorum.core.messages import Message
from bioquorum.core.status import StatusTrigger, VerificationStatus
from bioquorum.policy.permissions import PermissionPolicy
from bioquorum.review.binding import ground_claim
from bioquorum.review.evidence import Evidence
from bioquorum.tools.builtin.kernel import PYTHON_EXECUTE
from bioquorum.tools.invoke import invoke

ASSETS = Path(__file__).resolve().parent / "assets" / "v10-scenario-d"


async def seed(root: Path) -> ResearchControls:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise ValueError("Choose a new or empty project directory.")
    ws = workspace.init_project(
        root, name="Label correction study", classification=Classification.PUBLIC
    )
    (root / workspace.CONFIG_FILE).write_text(
        "[kernel]\nmacos_sandbox = true\nnetwork = false\npixi = false\n", encoding="utf-8"
    )
    ws.config["kernel"] = {"macos_sandbox": True, "network": False, "pixi": False}
    ws.kernel_isolation()
    deliveries = root / "deliveries"
    for version in ("original", "corrected", "renamed-column"):
        folder = deliveries / version
        folder.mkdir(parents=True)
        shutil.copyfile(ASSETS / (version + ".csv"), folder / "sample-labels.csv")
    controls = ResearchControls(ws)
    sid = controls.new_session("Original control analysis")
    pid = (await programs.create(controls, "Control signal after label correction"))["id"]
    await programs.attach(controls, pid, sid)

    async def original():
        yield (deliveries / "original" / "sample-labels.csv").read_bytes()

    uploaded = await artifacts.upload(
        controls, sid, "sample-labels.csv", Classification.PUBLIC, original()
    )
    await materials.register(
        controls, pid, uploaded["id"], "experimental_data", "Original synthetic labels"
    )
    name = uploaded["path"]
    code = (
        f"import csv\nwith open({name!r}) as sample_file:\n"
        "    rows = list(csv.DictReader(sample_file))\n"
        "control = [float(row['signal']) for row in rows if row['group'] == 'control']\n"
        "bq.value('control_mean', sum(control) / len(control), unit='AU')"
    )
    policy = PermissionPolicy()
    policy.grant(PermissionClass.EXECUTE, "python", PermissionScope.SESSION, session_id=sid)
    policy.grant(
        PermissionClass.READ_FS, str(ws.root / name), PermissionScope.SESSION, session_id=sid
    )
    ctx = workspace.tool_context(ws, controls.store, sid, policy)
    try:
        result = await invoke(PYTHON_EXECUTE, {"code": code, "input_files": [name]}, ctx)
        if result.is_error:
            raise RuntimeError(result.text())
    finally:
        await ctx.require_kernels().shutdown_all()
    history = controls.store.history(sid)
    value = next(p for e in history if isinstance(p := e.parsed(), ev.RunFinished))
    if value.values[0].value != 3:
        raise ValueError("Unexpected baseline control signal.")
    text = "Control mean was {{control_mean}} AU."
    binding = ground_claim(text, Evidence.from_events(history))
    if binding.state != "grounded":
        raise ValueError("The baseline claim did not bind to the kernel result.")
    answer = controls.store.append(
        ev.AssistantMessage(message=Message.assistant(text)), session_id=sid, actor=Actor.AGENT
    )
    claim = ev.ClaimExtracted(
        claim_id=new_id(),
        kind=ClaimKind.NUMERIC,
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
    return controls


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project", required=True, type=Path, help="New or empty project directory"
    )
    args = parser.parse_args()
    root = args.project.resolve()
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        parser.error("Choose a new or empty project directory.")
    controls = asyncio.run(seed(root))
    program = programs.list_programs(controls)[0]
    record = programs.show(controls, program["id"])
    print(
        json.dumps(
            {
                "project": str(root),
                "program_id": program["id"],
                "session_id": record["sessions"][0]["id"],
                "baseline_claim": record["claims"][0]["text"],
                "corrected_file": "deliveries/corrected/sample-labels.csv",
                "incompatible_file": "deliveries/renamed-column/sample-labels.csv",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
