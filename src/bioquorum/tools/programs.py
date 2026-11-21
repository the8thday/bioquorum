"""Researcher-controlled program events, outside the agent tool registry."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, PermissionClass
from bioquorum.core.ids import new_id
from bioquorum.policy.secrets import redact
from bioquorum.tools.context import ToolContext
from bioquorum.tools.spec import ToolResult, ToolSpec


class Name(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]

    @field_validator("name")
    @classmethod
    def safe_name(cls, value: str) -> str:
        if redact(value) != value:
            raise ValueError("Remove credentials from the program name")
        return value


class ProgramId(BaseModel):
    model_config = ConfigDict(extra="forbid")
    program_id: str


class Rename(Name, ProgramId):
    pass


class Attach(ProgramId):
    session_id: str


def _project(ctx: ToolContext[None]) -> str:
    if ctx.actor is not Actor.USER or not ctx.project_id:
        raise ValueError("Select a project to change its programs as a researcher")
    return ctx.project_id


def _append(
    ctx: ToolContext[None], payload: ev.ProgramCreated | ev.ProgramUpdated | ev.SessionAttached
) -> ToolResult:
    ctx.store.append(payload, session_id=None, actor=ctx.actor, causation_id=ctx.requested_event_id)
    return ToolResult.text(payload.program_id)


async def _create(args: Name, ctx: ToolContext[None]) -> ToolResult:
    return _append(
        ctx, ev.ProgramCreated(project_id=_project(ctx), program_id=new_id(), name=args.name)
    )


async def _rename(args: Rename, ctx: ToolContext[None]) -> ToolResult:
    return _append(
        ctx, ev.ProgramUpdated(project_id=_project(ctx), program_id=args.program_id, name=args.name)
    )


async def _close(args: ProgramId, ctx: ToolContext[None]) -> ToolResult:
    return _append(
        ctx, ev.ProgramUpdated(project_id=_project(ctx), program_id=args.program_id, closed=True)
    )


async def _attach(args: Attach, ctx: ToolContext[None]) -> ToolResult:
    return _append(
        ctx,
        ev.SessionAttached(
            project_id=_project(ctx), program_id=args.program_id, session_id=args.session_id
        ),
    )


async def _new_session(args: Rename, ctx: ToolContext[None]) -> ToolResult:
    project_id = _project(ctx)
    sid = new_id()
    ctx.store.append_streams(
        [
            (sid, ev.SessionCreated(project_id=project_id, title=args.name)),
            (
                None,
                ev.SessionAttached(
                    project_id=project_id, program_id=args.program_id, session_id=sid
                ),
            ),
        ],
        actor=ctx.actor,
        causation_id=ctx.requested_event_id,
    )
    return ToolResult.text(sid)


NEW_SESSION = ToolSpec[None](
    "create_program_session",
    "Start a session in this research line.",
    _new_session,
    PermissionClass.WRITE_FS,
    args_model=Rename,
)


CREATE = ToolSpec[None](
    "create_program",
    "Create a research program.",
    _create,
    PermissionClass.WRITE_FS,
    args_model=Name,
)
RENAME = ToolSpec[None](
    "rename_program",
    "Rename a research program.",
    _rename,
    PermissionClass.WRITE_FS,
    args_model=Rename,
)
CLOSE = ToolSpec[None](
    "close_program",
    "Close a research program.",
    _close,
    PermissionClass.WRITE_FS,
    args_model=ProgramId,
)
ATTACH = ToolSpec[None](
    "attach_session",
    "Attach a session to a research program.",
    _attach,
    PermissionClass.WRITE_FS,
    args_model=Attach,
)
