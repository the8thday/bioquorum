"""Read project context and suggest a draft without publishing it across sessions."""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field, field_validator

from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, PermissionClass
from bioquorum.policy.secrets import redact
from bioquorum.tools.context import ToolContext
from bioquorum.tools.spec import ToolResult, ToolSpec

READ_NOTE = "read_project_note"
DRAFT_NOTE = "draft_project_note"
SAVE_NOTE = "save_project_note"
NOTE_TARGET = "project-note"


class NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoteText(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(
        max_length=6000, description="Complete proposed project note in plain Markdown."
    )

    @field_validator("body")
    @classmethod
    def no_credentials(cls, value: str) -> str:
        if redact(value) != value:
            raise ValueError("Remove credentials from the project note")
        return value


class DraftNote(NoteText):
    prior_revision: str | None = Field(
        description="Exact revision returned by read_project_note, or null."
    )


class SaveNote(NoteText):
    prior_revision: str | None = Field(default=None, max_length=26)
    draft_event_id: str | None = Field(default=None, max_length=26)


def _project(ctx: ToolContext) -> str:
    if not ctx.project_id:
        raise ValueError("No project is selected")
    return ctx.project_id


async def _read(args: NoArguments, ctx: ToolContext) -> ToolResult:
    current = ctx.store.project_note(_project(ctx))
    return ToolResult.text(
        json.dumps(
            {
                "revision": current.id if current else None,
                "body": current.payload["body"] if current else "",
                "scope": "Project background. Verify scientific results using separate evidence.",
            },
            ensure_ascii=False,
        )
    )


async def _draft(args: DraftNote, ctx: ToolContext) -> ToolResult:
    project_id = _project(ctx)
    current = ctx.store.project_note(project_id)
    if args.prior_revision != (current.id if current else None):
        return ToolResult.text(
            "Project note changed. Read it again before drafting.", is_error=True
        )
    event = ctx.store.append(
        ev.ProjectNoteDrafted(
            project_id=project_id, prior_revision=args.prior_revision, body=args.body
        ),
        session_id=ctx.session_id,
        actor=Actor.AGENT,
        causation_id=ctx.requested_event_id,
    )
    return ToolResult.text(f"Draft {event.id} is available for researcher review in this session.")


async def _save(args: SaveNote, ctx: ToolContext) -> ToolResult:
    if ctx.actor is not Actor.USER:
        return ToolResult.text("Only a researcher can save the project note.", is_error=True)
    project_id = _project(ctx)
    if args.draft_event_id:
        event = ctx.store.get(args.draft_event_id)
        if (
            event is None
            or event.session_id != ctx.session_id
            or not isinstance(draft := event.parsed(), ev.ProjectNoteDrafted)
            or draft.project_id != project_id
            or draft.prior_revision != args.prior_revision
        ):
            return ToolResult.text(
                "Project note draft not found for this revision and session.", is_error=True
            )
    saved = ctx.store.append(
        ev.ProjectNoteSaved(
            project_id=project_id,
            editor_session_id=ctx.session_id,
            prior_revision=args.prior_revision,
            body=args.body,
            draft_event_id=args.draft_event_id,
        ),
        session_id=None,
        actor=Actor.USER,
        causation_id=ctx.requested_event_id,
    )
    return ToolResult.text(saved.id)


def note_tools() -> tuple[ToolSpec, ToolSpec]:
    return (
        ToolSpec(
            READ_NOTE,
            "Read the current researcher-approved project note and revision.",
            _read,
            PermissionClass.READ_FS,
            args_model=NoArguments,
            target=lambda *_: NOTE_TARGET,
        ),
        ToolSpec(
            DRAFT_NOTE,
            "Suggest a complete replacement for the project note. "
            "The researcher must approve it before it applies to other sessions.",
            _draft,
            PermissionClass.WRITE_FS,
            args_model=DraftNote,
            target=lambda *_: NOTE_TARGET + ":draft",
        ),
    )


def save_note_tool() -> ToolSpec:
    return ToolSpec(
        SAVE_NOTE,
        "Save a project note explicitly edited by the researcher.",
        _save,
        PermissionClass.WRITE_FS,
        args_model=SaveNote,
        target=lambda *_: NOTE_TARGET,
    )
