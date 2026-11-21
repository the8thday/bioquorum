"""Transactional preconditions for beginning, finishing and racing material corrections."""

from sqlalchemy import select
from sqlalchemy.engine import Connection

from bioquorum.core import events as ev
from bioquorum.core.enums import Actor
from bioquorum.storage import db


def validate_change(
    conn: Connection,
    p: ev.RecomputeStarted
    | ev.RecomputeFinished
    | ev.MaterialSuperseded
    | ev.MaterialWithdrawn
    | ev.ProgramUpdated,
    sid: str | None,
    actor: Actor,
) -> None:
    if isinstance(p, ev.ProgramUpdated) and not p.closed:
        return
    t = db.events
    scope = (
        t.c.session_id.is_(None),
        t.c.payload["project_id"].as_string() == p.project_id,
        t.c.payload["program_id"].as_string() == p.program_id,
    )
    finished = select(t.c.payload["recompute_id"].as_string()).where(
        *scope, t.c.type == ev.RecomputeFinished.event_type
    )
    active = conn.scalars(
        select(t.c.payload).where(
            *scope,
            t.c.type == ev.RecomputeStarted.event_type,
            t.c.payload["recompute_id"].as_string().not_in(finished),
        )
    ).all()
    if isinstance(p, ev.RecomputeFinished):
        if (
            sid is not None
            or actor is not Actor.SYSTEM
            or not any(
                op["recompute_id"] == p.recompute_id
                and op["material_version_id"] == p.material_version_id
                for op in active
            )
        ):
            raise ValueError("No matching open recalculation")
    elif isinstance(p, ev.RecomputeStarted):
        versions = db.material_versions
        rows = conn.execute(
            select(versions).where(
                versions.c.project_id == p.project_id,
                versions.c.program_id == p.program_id,
                versions.c.id.in_([p.material_version_id, p.replacement_version_id]),
            )
        ).mappings()
        by_id = {r["id"]: r for r in rows}
        old, new = by_id.get(p.material_version_id), by_id.get(p.replacement_version_id)
        linked = conn.scalar(
            select(db.program_sessions.c.id).where(
                db.program_sessions.c.id == p.session_id,
                db.program_sessions.c.program_id == p.program_id,
            )
        )
        if (
            sid is not None
            or actor is not Actor.USER
            or not linked
            or not old
            or not new
            or old["status"] != "superseded"
            or new["status"] != "active"
            or old["material_id"] != new["material_id"]
        ):
            raise ValueError(
                "The replacement or its recalculation session no longer belongs to this correction"
            )
        if any(op["material_version_id"] == p.material_version_id for op in active):
            raise ValueError("A recalculation is already running")
    elif isinstance(p, ev.ProgramUpdated):
        if active:
            raise ValueError("The research line is recalculating a material")
    else:
        version = p.prior_version_id if isinstance(p, ev.MaterialSuperseded) else p.version_id
        if any(op["replacement_version_id"] == version for op in active):
            raise ValueError("The selected replacement is currently being recalculated")
