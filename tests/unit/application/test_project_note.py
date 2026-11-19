import pytest

from bioquorum.application import project_memory, workspace
from bioquorum.application.controls import ResearchControls
from bioquorum.core import events as ev

pytestmark = pytest.mark.anyio


async def test_note_is_shared_between_sessions_and_stale_update_is_rejected(tmp_path):
    ws = workspace.init_project(tmp_path)
    ws.config = {"agent": {"project_notes": True}}
    controls = ResearchControls(ws)
    one = controls.new_session("First")
    two = controls.new_session("Second")
    initial = await project_memory.save(
        controls, one, project_memory.SaveNote(body="Name samples by donor.")
    )
    assert project_memory.snapshot(controls, two)["body"] == "Name samples by donor."
    assert project_memory.snapshot(ResearchControls(ws), two)["revision"] == initial["revision"]
    with pytest.raises(ValueError, match="changed"):
        await project_memory.save(controls, two, project_memory.SaveNote(body="Stale write."))
    assert project_memory.snapshot(controls, one)["body"] == "Name samples by donor."
    assert (
        len([e for e in controls.store.read(None) if isinstance(e.parsed(), ev.ProjectNoteSaved)])
        == 1
    )


async def test_disabled_note_has_no_authorized_write(tmp_path):
    ws = workspace.init_project(tmp_path)
    controls = ResearchControls(ws)
    sid = controls.new_session("First")
    with pytest.raises(LookupError):
        await project_memory.save(controls, sid, project_memory.SaveNote(body="No write."))


async def test_model_draft_stays_in_its_session_until_researcher_saves(tmp_path):
    from bioquorum.core.enums import Actor, PermissionClass, PermissionScope
    from bioquorum.policy.permissions import PermissionPolicy
    from bioquorum.review.evidence import Evidence
    from bioquorum.tools import ToolContext
    from bioquorum.tools.invoke import invoke
    from bioquorum.tools.project_note import DRAFT_NOTE, NOTE_TARGET, note_tools

    ws = workspace.init_project(tmp_path)
    ws.config = {"agent": {"project_notes": True}}
    controls = ResearchControls(ws)
    one, two = controls.new_session("Draft"), controls.new_session("Other")
    draft_tool = next(tool for tool in note_tools() if tool.name == DRAFT_NOTE)
    policy = PermissionPolicy()
    policy.grant(
        PermissionClass.WRITE_FS, NOTE_TARGET + ":draft", PermissionScope.SESSION, session_id=one
    )
    ctx = ToolContext(
        store=controls.store,
        session_id=one,
        permissions=policy,
        project_id=ws.project_id,
        actor=Actor.AGENT,
    )
    result = await invoke(draft_tool, {"body": "Working draft", "prior_revision": None}, ctx)
    assert not result.is_error
    assert project_memory.snapshot(controls, two) == {"revision": None, "body": "", "drafts": []}
    note = project_memory.snapshot(controls, one)
    assert note["body"] == "" and len(note["drafts"]) == 1
    draft = note["drafts"][0]["id"]
    with pytest.raises(ValueError, match="not found"):
        await project_memory.save(
            controls, two, project_memory.SaveNote(body="Wrong session", draft_event_id=draft)
        )
    await project_memory.save(
        controls, one, project_memory.SaveNote(body="Agreed convention", draft_event_id=draft)
    )
    assert project_memory.snapshot(controls, two)["body"] == "Agreed convention"
    assert not project_memory.snapshot(controls, one)["drafts"]
    assert not Evidence.from_events(controls.store.read(one)).sources


async def test_note_credentials_are_rejected_before_they_enter_tool_events(tmp_path):
    from bioquorum.policy.permissions import PermissionPolicy
    from bioquorum.tools import ToolContext
    from bioquorum.tools.invoke import invoke
    from bioquorum.tools.project_note import DRAFT_NOTE, note_tools

    ws = workspace.init_project(tmp_path)
    controls = ResearchControls(ws)
    sid = controls.new_session("Note")
    ctx = ToolContext(
        store=controls.store,
        session_id=sid,
        permissions=PermissionPolicy.allow_all(),
        project_id=ws.project_id,
    )
    draft = next(tool for tool in note_tools() if tool.name == DRAFT_NOTE)
    token = "sk-" + "a" * 24
    result = await invoke(draft, {"body": token, "prior_revision": None}, ctx)
    assert result.is_error
    assert all(token not in event.model_dump_json() for event in controls.store.read(sid))
