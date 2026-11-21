"""Project-scoped reads of researcher uploads and material versions."""

from sqlalchemy import select

from bioquorum.core import events as ev
from bioquorum.core.enums import Actor
from bioquorum.storage import db
from bioquorum.storage.db import Database


def registered_uploads(project_id: str):
    """Select only user uploads whose event and originating session agree on the project."""
    events = db.events
    return (
        select(events.c.id, events.c.payload)
        .join(db.sessions, db.sessions.c.id == events.c.session_id)
        .where(
            events.c.type == ev.FileRegistered.event_type,
            events.c.actor == Actor.USER.value,
            db.sessions.c.project_id == project_id,
            events.c.payload["project_id"].as_string() == project_id,
        )
    )


def find_upload(database: Database, project_id: str, file_id: str) -> tuple[str, ev.FileRegistered]:
    query = registered_uploads(project_id).where(
        db.events.c.payload["file_id"].as_string() == file_id
    )
    with database.connect() as conn:
        row = conn.execute(query).one_or_none()
    if row is None:
        raise LookupError("Upload not found in this project")
    return row.id, ev.FileRegistered.model_validate(row.payload)


def list_versions(database: Database, project_id: str, program_id: str) -> list[dict]:
    t = db.material_versions
    with database.connect() as conn:
        exists = conn.execute(
            select(db.programs.c.id).where(
                db.programs.c.project_id == project_id, db.programs.c.id == program_id
            )
        ).scalar_one_or_none()
        if exists is None:
            raise LookupError("Research line not found in this project")
        rows = conn.execute(
            select(t)
            .where(t.c.project_id == project_id, t.c.program_id == program_id)
            .order_by(t.c.material_id, t.c.version)
        ).mappings()
        return [dict(row) for row in rows]


def version(database: Database, project_id: str, program_id: str, version_id: str) -> dict:
    t = db.material_versions
    with database.connect() as conn:
        row = (
            conn.execute(
                select(t).where(
                    t.c.project_id == project_id, t.c.program_id == program_id, t.c.id == version_id
                )
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        raise LookupError("Material version not found in this research line")
    return dict(row)
