"""Researcher-only commands to register, replace, or withdraw program materials."""

from __future__ import annotations

from dataclasses import replace
from functools import partial
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, PermissionClass
from bioquorum.policy.classification import allows
from bioquorum.policy.secrets import redact
from bioquorum.storage.materials import find_upload
from bioquorum.tools.context import ToolContext
from bioquorum.tools.spec import ToolResult, ToolSpec

Description = Annotated[str, Field(min_length=1, max_length=1200)]


class ChangeMaterial(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    program_id: str

    @field_validator("source", "reason", check_fields=False)
    @classmethod
    def reject_credentials(cls, value: str) -> str:
        if redact(value) != value:
            raise ValueError("Remove credentials from material descriptions")
        return value


class RegisterMaterial(ChangeMaterial):
    file_id: str
    kind: ev.MaterialKind
    source: Description


class SupersedeMaterial(ChangeMaterial):
    file_id: str
    version_id: str
    source: Description
    reason: Description


class WithdrawMaterial(ChangeMaterial):
    version_id: str
    reason: Description


async def _change(
    args: RegisterMaterial | SupersedeMaterial | WithdrawMaterial,
    ctx: ToolContext[None],
    *,
    impacted_claims: tuple[str, ...] = (),
) -> ToolResult:
    if ctx.actor is not Actor.USER or not ctx.project_id:
        raise ValueError("Only a researcher can change project materials")
    project = ctx.project_id
    if isinstance(args, WithdrawMaterial):
        change = ev.MaterialWithdrawn(
            project_id=project,
            program_id=args.program_id,
            version_id=args.version_id,
            reason=args.reason,
        )
    else:
        upload_id, upload = find_upload(ctx.store.db, project, args.file_id)
        if not allows(upload.classification, ctx.classification):
            raise ValueError("The upload exceeds the project clearance")
        ctx.require_cas().verified_path(upload.ref.content_hash)
        if isinstance(args, RegisterMaterial):
            change = ev.MaterialRegistered(
                project_id=project,
                program_id=args.program_id,
                upload_event_id=upload_id,
                source=args.source,
                kind=args.kind,
            )
        else:
            change = ev.MaterialSuperseded(
                project_id=project,
                program_id=args.program_id,
                upload_event_id=upload_id,
                prior_version_id=args.version_id,
                source=args.source,
                reason=args.reason,
            )
    changes: list[ev.EventPayload] = [change]
    if isinstance(args, SupersedeMaterial | WithdrawMaterial):
        changes.extend(
            ev.ClaimReviewFlagged(
                project_id=project,
                program_id=args.program_id,
                material_version_id=args.version_id,
                claim_id=claim,
            )
            for claim in impacted_claims
        )
    records = ctx.store.append_chain(
        changes, session_id=None, actor=Actor.USER, causation_id=ctx.requested_event_id
    )
    return ToolResult.text(args.version_id if isinstance(args, WithdrawMaterial) else records[0].id)


def flag_affected(tool: ToolSpec[None], claim_ids: tuple[str, ...]) -> ToolSpec[None]:
    """Bind the event-derived scope before invoking a researcher material change."""
    return replace(tool, handler=partial(_change, impacted_claims=claim_ids))


REGISTER: ToolSpec[None] = ToolSpec(
    "register_material",
    "Register an uploaded file in a research line.",
    _change,
    PermissionClass.WRITE_FS,
    args_model=RegisterMaterial,
)
SUPERSEDE: ToolSpec[None] = ToolSpec(
    "supersede_material",
    "Replace the current material version with a recorded upload.",
    _change,
    PermissionClass.WRITE_FS,
    args_model=SupersedeMaterial,
)
WITHDRAW: ToolSpec[None] = ToolSpec(
    "withdraw_material",
    "Withdraw the current material version and record the reason.",
    _change,
    PermissionClass.WRITE_FS,
    args_model=WithdrawMaterial,
)
