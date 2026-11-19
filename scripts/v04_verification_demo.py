"""Prepare a genuine local verification and portable-bundle example without a model."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from bioquorum.application import verify, workspace
from bioquorum.application.controls import ResearchControls
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, ClaimKind, Classification
from bioquorum.core.messages import Message
from bioquorum.review.checks import deterministic_checks
from bioquorum.review.evidence import ClaimInput, Evidence
from bioquorum.review.service import review
from bioquorum.tools.builtin.kernel import PYTHON_EXECUTE
from bioquorum.tools.invoke import invoke

INITIALIZE = """import csv
from pathlib import Path

with Path("counts.csv").open() as source:
    counts = list(csv.DictReader(source))
"""
CODE = """total = sum(int(row["cells"]) for row in counts)
bq.value("total", total, unit="cells", desc="Sum of counted cells across the table rows.")
print("Read", len(counts), "rows.")
"""


async def run(root: Path, *, locked: bool) -> dict:
    ws = (
        workspace.load(root)
        if workspace.find_root(root) == root
        else workspace.init_project(
            root,
            name="Cell-count verification",
            classification=Classification.PUBLIC,
        )
    )
    if locked:
        ws.config.setdefault("kernel", {})["pixi"] = True
    source = root / "counts.csv"
    expected = "sample,cells\nA,20\nB,31\nC,17\n"
    if source.is_symlink() or (source.exists() and source.read_text() != expected):
        raise ValueError("Use a fresh directory for the verification example")
    if not source.exists():
        source.write_text(expected)
    controls = ResearchControls(ws)
    sid = controls.new_session("Verify a cell-count calculation")
    store = controls.store

    def append(payload, actor):
        return store.append(payload, session_id=sid, actor=actor)

    append(
        ev.UserMessage(message=Message.user("Sum the recorded counts in the demonstration table.")),
        Actor.USER,
    )
    ctx = workspace.tool_context(
        ws, store, sid, workspace.default_permissions(ws, allow=[], servers=[])
    )
    try:
        loaded = await invoke(
            PYTHON_EXECUTE, {"code": INITIALIZE, "input_files": ["counts.csv"]}, ctx
        )
        if loaded.is_error:
            raise RuntimeError(loaded.text())
        execution = await invoke(PYTHON_EXECUTE, {"code": CODE}, ctx)
    finally:
        await ctx.require_kernels().shutdown_all()
    if execution.is_error:
        raise RuntimeError(execution.text())
    answer = "The demonstration table contains {{total}} cells."
    events = store.read(sid)
    target = append(ev.AssistantMessage(message=Message.assistant(answer)), Actor.AGENT)
    decision = await review(
        store,
        session_id=sid,
        target_event_id=target.id,
        claims=[ClaimInput(1, answer, ClaimKind.NUMERIC)],
        evidence=Evidence.from_events(events, answer=answer),
        checks=deterministic_checks(),
        classification=ws.classification,
    )
    claim = decision.claims[0].claim_id
    comparison = await verify.rerun(controls, sid, claim)
    bundle = await verify.export_bundle(controls, sid)
    return {
        "session": sid,
        "claim": claim,
        "comparison": comparison,
        "bundle": str(verify.bundle_file(controls, sid, bundle["id"])),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--locked", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.project.resolve(), locked=args.locked)), indent=2))


if __name__ == "__main__":
    main()
