import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from typer.testing import CliRunner

from bioquorum.application import programs, workspace
from bioquorum.application.controls import ResearchControls
from bioquorum.cli.main import app
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, ClaimKind
from bioquorum.core.status import StatusTrigger, VerificationStatus
from bioquorum.storage.rebuild import rebuild, snapshot


@pytest.mark.anyio
async def test_project_stream_current_claims_and_cli_survive_rebuild(tmp_path):
    controls = ResearchControls(workspace.init_project(tmp_path))
    assert programs.list_programs(controls) == []
    line = await programs.create(controls, "Study A")
    pid = line["id"]
    assert line["sessions"] == line["claims"] == []
    sid = controls.new_session("Analysis")
    other = controls.new_session("Unrelated")
    await programs.attach(controls, pid, sid)
    await programs.rename(controls, pid, "Corrected study")
    for session_id in (sid, other):
        controls.store.append(
            ev.ClaimExtracted(
                claim_id=session_id,
                source_event_id="observations",
                text="Observation",
                kind=ClaimKind.NUMERIC,
            ),
            session_id=session_id,
            actor=Actor.SYSTEM,
        )
    controls.store.append(
        ev.ClaimStatusChanged(
            claim_id=sid,
            from_status=VerificationStatus.UNVERIFIED,
            to_status=VerificationStatus.GROUNDED,
            trigger=StatusTrigger.BINDINGS_RESOLVED,
        ),
        session_id=sid,
        actor=Actor.SYSTEM,
    )
    result = await programs.close(controls, pid)
    assert result["status"] == "closed"
    assert [r["id"] for r in result["sessions"]] == [sid]
    assert [(r["id"], r["status"]) for r in result["claims"]] == [(sid, "grounded")]
    with pytest.raises(ValueError, match="closed"):
        await programs.attach(controls, pid, other)
    project_events = controls.store.read(None)
    commands = [
        e
        for e in project_events
        if e.type in {"program_created", "program_updated", "session_attached"}
    ]
    assert len(commands) == 4
    assert all(
        e.actor is Actor.USER
        and e.causation_id in {c.id for c in project_events if c.type == "tool_call_requested"}
        for e in commands
    )
    before = snapshot(controls.store.db)
    rebuild(controls.store.db)
    assert snapshot(controls.store.db) == before
    assert programs.show(controls, pid) == result
    cli = CliRunner()
    listing = cli.invoke(app, ["program", "list", "-C", str(tmp_path)])
    assert listing.exit_code == 0 and json.loads(listing.stdout)[0]["id"] == pid
    detail = cli.invoke(app, ["program", "show", pid, "-C", str(tmp_path)])
    assert detail.exit_code == 0 and json.loads(detail.stdout) == result
    assert cli.invoke(app, ["program", "show", "missing", "-C", str(tmp_path)]).exit_code == 1


@pytest.mark.anyio
async def test_cross_project_and_duplicate_membership(tmp_path):
    controls = ResearchControls(workspace.init_project(tmp_path))
    line = (await programs.create(controls, "First"))["id"]
    other = (await programs.create(controls, "Second"))["id"]
    sid = controls.new_session("Selected")
    await programs.attach(controls, line, sid)
    await programs.attach(controls, line, sid)
    with pytest.raises(ValueError, match="already"):
        await programs.attach(controls, other, sid)
    controls.store.append(
        ev.SessionCreated(project_id="elsewhere", title="External"),
        session_id="outside",
        actor=Actor.USER,
    )
    with pytest.raises(ValueError, match="not found"):
        await programs.attach(controls, line, "outside")
    controls.ws.project_id = "elsewhere"
    assert programs.list_programs(controls) == []
    with pytest.raises(LookupError):
        programs.show(controls, line)
    with pytest.raises(ValueError, match="not found"):
        await programs.rename(controls, line, "Cross-project")
    controls.ws.project_id = "unregistered"
    with pytest.raises(ValueError, match="not found"):
        await programs.create(controls, "Unknown project")


def test_session_attachment_is_atomic_under_racing_writers(tmp_path):
    ws = workspace.init_project(tmp_path)
    controls = ResearchControls(ws)
    lines = [asyncio.run(programs.create(controls, name))["id"] for name in ("First", "Second")]
    sid = controls.new_session("One session")

    def attach(line):
        try:
            asyncio.run(programs.attach(ResearchControls(ws), line, sid))
            return True
        except ValueError as error:
            assert "already" in str(error)
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attach, lines)) == [False, True]
    assert sum(len(programs.show(controls, line)["sessions"]) for line in lines) == 1


@pytest.mark.anyio
async def test_current_claims_use_last_completed_review_and_answer_time_evidence(tmp_path):
    from bioquorum.core.hashing import code_hash
    from bioquorum.core.messages import Message
    from bioquorum.core.refs import NamedValue

    controls = ResearchControls(workspace.init_project(tmp_path))
    pid = (await programs.create(controls, "Tracking"))["id"]
    sid = controls.new_session("Reviewed twice")
    await programs.attach(controls, pid, sid)

    def append(payload, cause=None):
        return controls.store.append(
            payload, session_id=sid, actor=Actor.REVIEWER, causation_id=cause
        )

    def calculated(rid, value):
        append(
            ev.RunStarted(
                run_id=rid,
                kernel_id="kernel",
                language="python",
                code="calculation",
                code_hash=code_hash("calculation"),
            )
        )
        append(
            ev.RunFinished(run_id=rid, run_key=rid, values=[NamedValue(name="count", value=value)])
        )

    calculated("original", 11)
    target = append(
        ev.AssistantMessage(message=Message.assistant("There were {{count}} samples."))
    ).id
    calculated("later", 19)
    for label in ("old", "current", "unfinished"):
        review = append(
            ev.ReviewStarted(review_id=label, reviewer_model="local", target_event_id=target)
        )
        append(
            ev.ClaimExtracted(
                claim_id=label,
                source_event_id=target,
                text="There were {{count}} samples.",
                kind=ClaimKind.NUMERIC,
            ),
            review.id,
        )
        if label != "unfinished":
            append(ev.ReviewFinished(review_id=label, finding_count=0), review.id)
    claims = programs.show(controls, pid)["claims"]
    assert [(row["id"], row["text"]) for row in claims] == [("current", "There were 11 samples.")]


@pytest.mark.anyio
async def test_invalid_or_sensitive_program_names_do_not_reach_domain_events(tmp_path):
    controls = ResearchControls(workspace.init_project(tmp_path))
    credential = "sk-" + "x" * 48
    for invalid in ("  ", "A" * 161, credential):
        with pytest.raises(ValueError):
            await programs.create(controls, invalid)
    assert programs.list_programs(controls) == []
    assert all(credential not in event.model_dump_json() for event in controls.store.iter_all())
    assert all(event.type != "program_created" for event in controls.store.read(None))


@pytest.mark.anyio
async def test_session_materials_returns_empty_for_inherited_claim_outside_program(tmp_path):
    from bioquorum.application.program_lineage import session_materials
    from bioquorum.core.messages import Message

    controls = ResearchControls(workspace.init_project(tmp_path))
    parent = controls.new_session("Unattached parent")
    answer = controls.store.append(
        ev.AssistantMessage(message=Message.assistant("Baseline finding.")),
        session_id=parent,
        actor=Actor.AGENT,
    )
    controls.store.append(
        ev.ClaimExtracted(
            claim_id="parent_claim",
            source_event_id=answer.id,
            text="Baseline finding.",
            kind=ClaimKind.NUMERIC,
        ),
        session_id=parent,
        actor=Actor.REVIEWER,
    )
    child = controls.fork(parent, "Attached fork")
    pid = (await programs.create(controls, "Follow-up line"))["id"]
    await programs.attach(controls, pid, child)
    assert session_materials(controls, child, "parent_claim") == []
