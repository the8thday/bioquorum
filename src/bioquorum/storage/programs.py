"""Read-only research program and session membership projections."""

from sqlalchemy import and_, literal_column, or_, select

from bioquorum.core.events import Event
from bioquorum.storage import db
from bioquorum.storage.events import _from_row


def list_programs(database: db.Database, project_id: str) -> list[dict]:
    query = (
        select(db.programs).where(db.programs.c.project_id == project_id).order_by(db.programs.c.id)
    )
    with database.connect() as conn:
        return [dict(row) for row in conn.execute(query).mappings()]


def show(database: db.Database, project_id: str, program_id: str) -> dict:
    program = select(db.programs).where(
        db.programs.c.id == program_id, db.programs.c.project_id == project_id
    )
    sessions = (
        select(db.sessions)
        .join(db.program_sessions, db.program_sessions.c.id == db.sessions.c.id)
        .where(
            db.program_sessions.c.program_id == program_id, db.sessions.c.project_id == project_id
        )
        .order_by(db.sessions.c.id)
    )
    with database.connect() as conn:
        conn.exec_driver_sql("BEGIN")
        entry = conn.execute(program).mappings().one_or_none()
        if entry is None:
            raise LookupError("Program not found")
        return {**entry, "sessions": [dict(row) for row in conn.execute(sessions).mappings()]}


def _program_events(project_id: str, program_id: str):
    membership = (
        select(db.program_sessions.c.id)
        .join(db.sessions, db.sessions.c.id == db.program_sessions.c.id)
        .where(
            db.program_sessions.c.program_id == program_id, db.sessions.c.project_id == project_id
        )
    )
    table = db.events
    return or_(
        table.c.session_id.in_(membership),
        and_(
            table.c.session_id.is_(None),
            table.c.payload["project_id"].as_string() == project_id,
            table.c.payload["program_id"].as_string() == program_id,
        ),
    )


def head(database: db.Database, project_id: str, program_id: str) -> str | None:
    with database.connect() as conn:
        return conn.scalar(
            select(db.events.c.id)
            .where(_program_events(project_id, program_id))
            .order_by(literal_column("rowid").desc())
            .limit(1)
        )


def event_log(database: db.Database, project_id: str, program_id: str) -> list[Event]:
    """Read member and program event streams in commit order, never other programs' records."""
    with database.connect() as conn:
        query = (
            select(db.events)
            .where(_program_events(project_id, program_id))
            .order_by(literal_column("rowid"))
        )
        return [_from_row(row) for row in conn.execute(query)]


def record(database: db.Database, project_id: str, program_id: str, event_id: str) -> Event:
    with database.connect() as conn:
        query = select(db.events).where(
            db.events.c.id == event_id, _program_events(project_id, program_id)
        )
        row = conn.execute(query).one_or_none()
        if row is None:
            raise LookupError("Record not found in this program")
        return _from_row(row)


def for_session(database: db.Database, project_id: str, session_id: str) -> str | None:
    query = (
        select(db.programs.c.id)
        .join(db.program_sessions, db.program_sessions.c.program_id == db.programs.c.id)
        .where(db.programs.c.project_id == project_id, db.program_sessions.c.id == session_id)
    )
    with database.connect() as conn:
        return conn.scalar(query)
