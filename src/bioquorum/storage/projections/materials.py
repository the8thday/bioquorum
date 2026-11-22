"""Derive material versions and reject stale changes in the event transaction."""

from sqlalchemy import select, update
from sqlalchemy.engine import Connection

from bioquorum.core import events as ev
from bioquorum.core.enums import Actor
from bioquorum.storage import db
from bioquorum.storage.materials import registered_uploads


def apply_material(conn: Connection, event: ev.Event, p: ev.MaterialChange) -> None:
    if event.actor is not Actor.USER or event.session_id is not None:
        raise ValueError("Only a researcher can change project materials")
    status = conn.execute(
        select(db.programs.c.status).where(
            db.programs.c.id == p.program_id, db.programs.c.project_id == p.project_id
        )
    ).scalar_one_or_none()
    if status != "active":
        raise ValueError("An active research line in this project is required")

    t = db.material_versions
    if isinstance(p, ev.MaterialRegistered):
        material_id, number, kind = event.id, 1, p.kind
        reason = None
    elif isinstance(p, ev.MaterialSuperseded | ev.MaterialWithdrawn):
        version_id = p.version_id if isinstance(p, ev.MaterialWithdrawn) else p.prior_version_id
        previous = (
            conn.execute(
                select(t).where(
                    t.c.id == version_id,
                    t.c.project_id == p.project_id,
                    t.c.program_id == p.program_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        if previous is None or previous["status"] != "active":
            raise ValueError("This material version is no longer active in this research line")
        if isinstance(p, ev.MaterialWithdrawn):
            conn.execute(
                update(t)
                .where(t.c.id == version_id)
                .values(status="withdrawn", withdrawal_reason=p.reason)
            )
            return
        conn.execute(update(t).where(t.c.id == version_id).values(status="superseded"))
        material_id, number, kind = (
            previous["material_id"],
            previous["version"] + 1,
            previous["kind"],
        )
        reason = p.reason
    else:
        raise TypeError("Unknown material change")

    upload_row = conn.execute(
        registered_uploads(p.project_id).where(db.events.c.id == p.upload_event_id)
    ).one_or_none()
    if upload_row is None:
        raise ValueError("Upload not found in this project")
    upload = ev.FileRegistered.model_validate(upload_row.payload)
    duplicate = conn.execute(
        select(t.c.id).where(t.c.program_id == p.program_id, t.c.file_id == upload.file_id)
    ).scalar_one_or_none()
    if duplicate is not None:
        raise ValueError("This upload is already registered in the research line")
    conn.execute(
        t.insert().values(
            id=event.id,
            material_id=material_id,
            project_id=p.project_id,
            program_id=p.program_id,
            version=number,
            file_id=upload.file_id,
            name=upload.filename,
            hash=upload.ref.content_hash,
            classification=upload.classification.value,
            kind=kind,
            source=p.source,
            reason=reason,
            status="active",
            registered_at=event.ts.isoformat(),
            registered_by=event.actor.value,
        )
    )
