"""Recalculate a changed control assignment in a second isolated Python kernel."""

import pytest
from evals.v10_scenario_d import seed

from bioquorum.application import artifacts, materials, programs, recompute
from bioquorum.core import events as ev
from bioquorum.core.enums import Classification
from bioquorum.provenance.recompute import pending_reviews
from bioquorum.runtime.sandbox import bwrap_available, seatbelt_available

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


@pytest.mark.skipif(
    not (bwrap_available() or seatbelt_available()), reason="requires an isolated kernel"
)
async def test_two_swapped_labels_recompute_actual_measurements(tmp_path):
    c = await seed(tmp_path)
    pid = programs.list_programs(c)[0]["id"]
    before = programs.show(c, pid)
    sid, claim = before["sessions"][0]["id"], before["claims"][0]["id"]
    v1 = materials.catalog(c, pid)[0]
    from bioquorum.application.record import snapshot
    from bioquorum.core.enums import Actor
    from bioquorum.core.ids import new_id

    original_claim = next(
        p for e in c.store.read(sid) if isinstance(p := e.parsed(), ev.ClaimExtracted)
    )
    direction = original_claim.model_copy(
        update={"claim_id": new_id(), "text": "Control was lower, with mean {{control_mean}} AU."}
    )
    c.store.append(direction, session_id=sid, actor=Actor.REVIEWER)

    async def corrected():
        yield b"sample,group,signal\nS1,treated,3\nS2,control,8\n"

    file = await artifacts.upload(c, sid, "sample-labels.csv", Classification.PUBLIC, corrected())
    v2 = await materials.supersede(
        c, pid, v1["id"], file["id"], "Corrected labels", "Two sample labels swapped"
    )
    assert pending_reviews(c.store.read(None), c.ws.project_id, pid) == {
        claim: {v1["id"]},
        direction.claim_id: {v1["id"]},
    }
    assert all(
        row["needs_review"] for answer in snapshot(c, sid)["answers"] for row in answer["claims"]
    )
    report = await recompute.start(c, pid, v1["id"], v2["id"])
    assert report["status"] == "completed", report["reason"]
    pair = next(row for row in report["comparisons"] if row["old_claim_id"] == claim)
    assert pair["old_claim_id"] == claim and pair["new_claim_id"] != claim
    assert pair["new_status"] == "grounded"
    (value,) = pair["values"]
    assert (value["old_value"], value["new_value"], value["unit"]) == (3, 8, "AU")
    starts = [
        p for e in c.store.read(report["session_id"]) if isinstance(p := e.parsed(), ev.RunStarted)
    ]
    assert starts[0].inputs[0].content_hash == v2["hash"]
    assert pending_reviews(c.store.read(None), c.ws.project_id, pid) == {
        direction.claim_id: {v1["id"]}
    }
    saved = {row["id"]: row for answer in snapshot(c, sid)["answers"] for row in answer["claims"]}
    assert (
        not saved[claim]["needs_review"]
        and saved[claim]["replacement_session_id"] == report["session_id"]
    )
    assert saved[direction.claim_id]["needs_review"]
    assert (await recompute.start(c, pid, v1["id"], v2["id"]))["id"] == report["id"]


@pytest.mark.skipif(
    not (bwrap_available() or seatbelt_available()), reason="requires isolated kernels"
)
async def test_recompute_uses_regenerated_python_output_in_r(tmp_path):
    import os
    from pathlib import Path

    from bioquorum.application import workspace
    from bioquorum.application.controls import ResearchControls
    from bioquorum.core.enums import Actor, ClaimKind
    from bioquorum.core.ids import new_id
    from bioquorum.core.messages import Message
    from bioquorum.review.binding import ground_claim
    from bioquorum.review.evidence import Evidence
    from bioquorum.tools import default_registry, invoke

    prefix = Path(os.environ.get("BQ_R_PROJECT", "examples/v09")).resolve()
    if not (prefix / ".pixi/envs/default/bin/python").is_file():
        pytest.skip("A provisioned BQ_R_PROJECT is required")
    ws = workspace.init_project(tmp_path, classification=Classification.PUBLIC)
    ws.config["kernel"] = {"pixi": True, "macos_sandbox": True}
    (tmp_path / ".pixi").symlink_to(prefix / ".pixi", target_is_directory=True)
    for name in ("pixi.toml", "pixi.lock"):
        (tmp_path / name).write_bytes((prefix / name).read_bytes())
    c = ResearchControls(ws)
    sid = c.new_session("Two languages")
    pid = (await programs.create(c, "Bilingual correction"))["id"]
    await programs.attach(c, pid, sid)

    async def file(data):
        async def chunks():
            yield data

        return await artifacts.upload(c, sid, "signal.csv", Classification.PUBLIC, chunks())

    first = await file(b"x\n3\n")
    next_file = await file(b"x\n8\n")
    old = await materials.register(c, pid, first["id"], "experimental_data", "Recorded signal")
    ctx = workspace.tool_context(
        ws, c.store, sid, workspace.default_permissions(ws, allow=[], servers=[])
    )
    registry = default_registry()
    try:
        py = await invoke(
            registry.get("python_execute"),
            {
                "code": f"from pathlib import Path\nx = int(Path({first['path']!r}).read_text().splitlines()[1])\nPath('counts.csv').write_text('x\\n' + str(2*x) + '\\n')",
                "input_files": [first["path"]],
                "output_files": ["counts.csv"],
            },
            ctx,
        )
        assert not py.is_error, py.text()
        r = await invoke(
            registry.get("r_execute"),
            {
                "code": 'bq_value("signal", sum(read.csv("counts.csv")$x))',
                "input_files": ["counts.csv"],
            },
            ctx,
        )
        assert not r.is_error, r.text()
    finally:
        await ctx.require_kernels().shutdown_all()
    source = c.store.append(
        ev.AssistantMessage(message=Message.assistant("Signal was {{signal}}.")),
        session_id=sid,
        actor=Actor.AGENT,
    )
    refs = ground_claim("Signal was {{signal}}.", Evidence.from_events(c.store.history(sid))).refs
    c.store.append(
        ev.ClaimExtracted(
            claim_id=new_id(),
            source_event_id=source.id,
            text="Signal was {{signal}}.",
            kind=ClaimKind.NUMERIC,
            refs=list(refs),
        ),
        session_id=sid,
        actor=Actor.SYSTEM,
    )
    new = await materials.supersede(
        c, pid, old["id"], next_file["id"], "Revised signal", "Corrected measurement"
    )
    op = await recompute.start(c, pid, old["id"], new["id"])
    assert op["status"] == "completed", op["reason"]
    before, after = (
        op["comparisons"][0]["values"][0]["old_value"],
        op["comparisons"][0]["values"][0]["new_value"],
    )
    assert (before, after) == (6, 16)
    regenerated = [
        p for e in c.store.read(op["session_id"]) if isinstance(p := e.parsed(), ev.RunStarted)
    ]
    assert [p.language for p in regenerated] == ["python", "r"]
    (produced,) = [
        p.post_run_hash
        for e in c.store.read(op["session_id"])
        if isinstance(p := e.parsed(), ev.LineageObserved)
        and p.function == "declared_write"
        and p.path == "counts.csv"
    ]
    assert produced != new["hash"]
    assert [p.inputs[0].content_hash for p in regenerated] == [new["hash"], produced]
