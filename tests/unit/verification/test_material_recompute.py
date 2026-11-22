from pathlib import Path

import pytest

from bioquorum.application import artifacts, materials, programs, recompute
from bioquorum.application.controls import ResearchControls
from bioquorum.application.workspace import init_project
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, ClaimKind, Classification
from bioquorum.core.hashing import code_hash
from bioquorum.core.ids import new_id
from bioquorum.core.refs import NamedValue, ValueRef
from bioquorum.core.status import StatusTrigger, VerificationStatus
from bioquorum.provenance.recompute import pending_reviews


@pytest.fixture
async def scenario(tmp_path: Path):
    c = ResearchControls(init_project(tmp_path, classification=Classification.PUBLIC))
    sid = c.new_session("Original")
    pid = (await programs.create(c, "Treatment comparison"))["id"]
    await programs.attach(c, pid, sid)

    async def upload(data):
        async def stream():
            yield data

        return await artifacts.upload(c, sid, "labels.csv", Classification.PUBLIC, stream())

    original = await upload(b"sample,label\na,treatment\nb,control\n")
    revised = await upload(b"sample,label\na,control\nb,treatment\n")
    v1 = await materials.register(c, pid, original["id"], "experimental_data", "Labels")
    rid, claim = new_id(), new_id()
    raw = c.ws.cas().put_file(tmp_path / original["path"])
    text = "Group difference was {{delta}}."
    events = [
        ev.RunStarted(
            run_id=rid,
            kernel_id="kernel",
            language="python",
            code="pass",
            code_hash=code_hash("pass"),
            isolation="bwrap",
            environment="test",
            input_paths=[original["path"]],
            inputs=[raw],
        ),
        ev.RunFinished(run_id=rid, run_key="a" * 64, values=[NamedValue(name="delta", value=5)]),
    ]
    answer = c.store.append_chain(events, session_id=sid, actor=Actor.TOOL)[-1]
    c.store.append(
        ev.ClaimExtracted(
            claim_id=claim,
            source_event_id=answer.id,
            text=text,
            kind=ClaimKind.NUMERIC,
            refs=[ValueRef(run_id=rid, name="delta")],
        ),
        session_id=sid,
        actor=Actor.SYSTEM,
    )
    c.store.append(
        ev.ClaimStatusChanged(
            claim_id=claim,
            from_status=VerificationStatus.UNVERIFIED,
            to_status=VerificationStatus.GROUNDED,
            trigger=StatusTrigger.BINDINGS_RESOLVED,
        ),
        session_id=sid,
        actor=Actor.SYSTEM,
    )
    return c, sid, pid, v1, revised, claim


@pytest.mark.anyio
async def test_superseding_flags_claim_and_estimates_local_run_without_changing_grade(scenario):
    c, _sid, pid, v1, revised, claim = scenario
    v2 = await materials.supersede(
        c, pid, v1["id"], revised["id"], "Corrected assignment", "Swapped two labels"
    )
    preview = recompute.preview(c, pid, v1["id"])
    assert preview["replacement_version_id"] == v2["id"]
    assert preview["estimated_model_usd"] == 0 and preview["can_recompute"]
    assert [item["id"] for item in preview["impact"]["claims"]] == [claim]
    assert pending_reviews(c.store.read(None), c.ws.project_id, pid) == {claim: {v1["id"]}}
    assert programs.show(c, pid)["claims"][0]["status"] == "grounded"
    with pytest.raises(ValueError):
        await recompute.start(c, pid, v1["id"], new_id())
    assert not recompute.preview(c, pid, v1["id"])["operations"]
    await materials.withdraw(c, pid, v2["id"], "All labels recalled")
    assert not recompute.preview(c, pid, v1["id"])["can_recompute"]


@pytest.mark.anyio
async def test_withdrawal_preserves_review_flag_without_replacement(scenario):
    c, _sid, pid, v1, _, claim = scenario
    await materials.withdraw(c, pid, v1["id"], "Recall")
    assert pending_reviews(c.store.read(None), c.ws.project_id, pid) == {claim: {v1["id"]}}
    assert not recompute.preview(c, pid, v1["id"])["can_recompute"]


@pytest.mark.anyio
async def test_failures_leave_original_review_open_and_explain_the_corrected_input(scenario):
    c, _sid, pid, v1, revised, claim = scenario
    v2 = await materials.supersede(
        c, pid, v1["id"], revised["id"], "Changed labels", "Supplier correction"
    )
    # Simulate a missing CAS blob after the recorded replacement.
    c.ws.cas().path(v2["hash"]).unlink()
    outcome = await recompute.start(c, pid, v1["id"], v2["id"])
    assert outcome["status"] == "failed"
    assert v2["hash"] in outcome["repair_prompt"]
    assert pending_reviews(c.store.read(None), c.ws.project_id, pid) == {claim: {v1["id"]}}
    assert len(programs.show(c, pid)["claims"]) == 1


@pytest.mark.anyio
async def test_crashed_recalculation_recovers_and_stale_replacement_is_rejected(scenario):
    c, _sid, pid, old, revised, claim = scenario
    new = await materials.supersede(
        c, pid, old["id"], revised["id"], "Correction", "Labels corrected"
    )
    target = c.new_session("Interrupted calculation")
    await programs.attach(c, pid, target)
    started = ev.RecomputeStarted(
        project_id=c.ws.project_id,
        program_id=pid,
        material_version_id=old["id"],
        replacement_version_id=new["id"],
        recompute_id=new_id(),
        session_id=target,
        run_ids=[],
        claim_ids=[claim],
    )
    c.store.append(started, session_id=None, actor=Actor.USER)
    with pytest.raises(ValueError):
        c.store.append(
            started.model_copy(update={"recompute_id": new_id()}), session_id=None, actor=Actor.USER
        )
    with pytest.raises(ValueError):
        await materials.withdraw(c, pid, new["id"], "Concurrent correction")
    await c.recover_verification()
    await c.recover_verification()
    (op,) = recompute.preview(c, pid, old["id"])["operations"]
    assert op["status"] == "failed" and "previous process" in op["reason"]
    assert pending_reviews(c.store.read(None), c.ws.project_id, pid) == {claim: {old["id"]}}
    await materials.withdraw(c, pid, new["id"], "Recalled")
    with pytest.raises(ValueError):
        c.store.append(
            started.model_copy(update={"recompute_id": new_id()}), session_id=None, actor=Actor.USER
        )


@pytest.mark.anyio
async def test_event_derived_review_for_an_older_material_change(scenario):
    from bioquorum.application.program_lineage import review_state

    c, _sid, pid, old, _revised, claim = scenario
    # Prior releases recorded the withdrawal even when no claim flag event existed.
    change = c.store.append(
        ev.MaterialWithdrawn(
            project_id=c.ws.project_id,
            program_id=pid,
            version_id=old["id"],
            reason="Supplier recall",
        ),
        session_id=None,
        actor=Actor.USER,
    )
    assert pending_reviews(c.store.read(None), c.ws.project_id, pid) == {}
    outstanding, source = review_state(c, pid)
    assert outstanding == {claim: {old["id"]}}
    assert source[claim, old["id"]] == change.id
