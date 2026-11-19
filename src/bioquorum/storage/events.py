"""Append-only event store. The only write path into the database (ADR-002)."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import func, select
from sqlalchemy.engine import Connection, Row

from bioquorum.core import events as ev
from bioquorum.core.enums import Actor
from bioquorum.core.events import Event, EventPayload
from bioquorum.storage import db as schema
from bioquorum.storage.db import Database
from bioquorum.storage.projections import apply_all


class EventStore:
    def __init__(self, database: Database) -> None:
        self.db = database

    # ------------------------------------------------------------- write

    def append(
        self,
        payload: EventPayload,
        *,
        session_id: str | None,
        actor: Actor,
        causation_id: str | None = None,
    ) -> Event:
        """Append one event, assign its per-session ``seq`` and update projections atomically."""
        with self.db.begin() as conn:
            if conn.dialect.name == "sqlite":
                conn.exec_driver_sql("BEGIN IMMEDIATE")
            if isinstance(payload, ev.ProjectNoteSaved):
                current = self._note(conn, payload.project_id)
                if actor is not Actor.USER or session_id is not None:
                    raise ValueError("Project note revisions require a direct researcher action")
                if payload.prior_revision != (current.id if current else None):
                    raise ValueError("The project note changed. Reload it before saving.")
            seq = self._next_seq(conn, session_id)
            event = Event.build(
                payload, session_id=session_id, seq=seq, actor=actor, causation_id=causation_id
            )
            conn.execute(schema.events.insert().values(**_to_row(event)))
            apply_all(conn, event)
            return event

    @staticmethod
    def _note(conn: Connection, project_id: str) -> Event | None:
        query = (
            select(schema.events)
            .where(
                schema.events.c.session_id.is_(None),
                schema.events.c.type == ev.ProjectNoteSaved.event_type,
                schema.events.c.payload["project_id"].as_string() == project_id,
            )
            .order_by(schema.events.c.seq.desc())
            .limit(1)
        )
        row = conn.execute(query).first()
        return _from_row(row) if row else None

    def project_note(self, project_id: str) -> Event | None:
        with self.db.connect() as conn:
            return self._note(conn, project_id)

    @staticmethod
    def _next_seq(conn: Connection, session_id: str | None) -> int:
        q = select(func.max(schema.events.c.seq))
        q = (
            q.where(schema.events.c.session_id.is_(None))
            if session_id is None
            else q.where(schema.events.c.session_id == session_id)
        )
        current = conn.execute(q).scalar()
        return 0 if current is None else current + 1

    # ------------------------------------------------------------- read

    def read(
        self, session_id: str | None, from_seq: int = 0, to_seq: int | None = None
    ) -> list[Event]:
        q = select(schema.events).where(schema.events.c.seq >= from_seq)
        q = (
            q.where(schema.events.c.session_id.is_(None))
            if session_id is None
            else q.where(schema.events.c.session_id == session_id)
        )
        if to_seq is not None:
            q = q.where(schema.events.c.seq <= to_seq)
        q = q.order_by(schema.events.c.seq)
        with self.db.connect() as conn:
            return [_from_row(r) for r in conn.execute(q)]

    def history(self, session_id: str) -> list[Event]:
        """Include a fork's immutable ancestry, with distinct local display positions."""
        own = self.read(session_id)
        segment = own
        segments: list[list[Event]] = []
        seen = {session_id}
        while segment and isinstance(fork := segment[0].parsed(), ev.SessionForked):
            if fork.parent_session_id in seen:
                raise ValueError("Cyclic session ancestry")
            seen.add(fork.parent_session_id)
            segment = self.read(fork.parent_session_id, to_seq=fork.forked_at_seq)
            segments.append(segment)
        prefix = [
            event
            for segment in reversed(segments)
            for event in segment
            if event.type != ev.ContextCompacted.event_type
        ]
        inherited = [
            event.model_copy(update={"seq": index - len(prefix)})
            for index, event in enumerate(prefix)
        ]
        return [*inherited, *own]

    def list_sessions(self, project_id: str) -> list[dict]:
        query = (
            select(schema.sessions)
            .where(schema.sessions.c.project_id == project_id)
            .order_by(schema.sessions.c.id.desc())
        )
        with self.db.connect() as conn:
            return [dict(row) for row in conn.execute(query).mappings()]

    def iter_all(self) -> Iterator[Event]:
        """All events in global replay order: by id (monotonic ULID, see core.ids)."""
        q = select(schema.events).order_by(schema.events.c.id)
        with self.db.connect() as conn:
            for r in conn.execute(q):
                yield _from_row(r)

    def count(self) -> int:
        with self.db.connect() as conn:
            return conn.execute(select(func.count()).select_from(schema.events)).scalar_one()

    def get(self, event_id: str) -> Event | None:
        with self.db.connect() as conn:
            row = conn.execute(select(schema.events).where(schema.events.c.id == event_id)).first()
            return _from_row(row) if row else None


def _to_row(ev: Event) -> dict:
    return {
        "id": ev.id,
        "session_id": ev.session_id,
        "seq": ev.seq,
        "ts": ev.ts.isoformat(),
        "type": ev.type,
        "schema_version": ev.schema_version,
        "actor": ev.actor.value,
        "causation_id": ev.causation_id,
        "payload": ev.payload,
    }


def _from_row(row: Row) -> Event:
    return Event(
        id=row.id,
        session_id=row.session_id,
        seq=row.seq,
        ts=row.ts,
        type=row.type,
        schema_version=row.schema_version,
        actor=Actor(row.actor),
        causation_id=row.causation_id,
        payload=row.payload,
    )
