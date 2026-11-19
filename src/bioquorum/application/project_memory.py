"""Shared researcher controls for project notes."""

from bioquorum.application.controls import ResearchControls
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, PermissionClass, PermissionScope
from bioquorum.policy.permissions import PermissionPolicy
from bioquorum.tools import ToolContext
from bioquorum.tools.invoke import invoke
from bioquorum.tools.project_note import NOTE_TARGET, SaveNote, save_note_tool

__all__ = ["SaveNote", "save", "snapshot"]


def snapshot(controls: ResearchControls, sid: str) -> dict:
    if not controls.ws.agent_options().project_notes:
        raise LookupError("Project notes are disabled")
    events = controls.require_session(sid)
    current = controls.store.project_note(controls.ws.project_id)
    revision = current.id if current else None
    drafts = [
        {"id": event.id, "body": draft.body}
        for event in events
        if isinstance(draft := event.parsed(), ev.ProjectNoteDrafted)
        and draft.project_id == controls.ws.project_id
        and draft.prior_revision == revision
    ]
    return {
        "revision": revision,
        "body": current.payload["body"] if current else "",
        "drafts": drafts[-10:][::-1],
    }


async def save(controls: ResearchControls, sid: str, edit: SaveNote) -> dict:
    snapshot(controls, sid)
    policy = PermissionPolicy()
    policy.grant(PermissionClass.WRITE_FS, NOTE_TARGET, PermissionScope.SESSION, session_id=sid)
    ctx = ToolContext(
        store=controls.store,
        session_id=sid,
        permissions=policy,
        project_id=controls.ws.project_id,
        classification=controls.ws.classification,
        actor=Actor.USER,
        workdir=controls.ws.root,
    )
    result = await invoke(save_note_tool(), edit.model_dump(mode="json"), ctx)
    if result.is_error:
        raise ValueError(result.text())
    return snapshot(controls, sid)
