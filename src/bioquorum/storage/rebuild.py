"""Rebuild every projection table from the event log, and snapshot tables for comparison."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from bioquorum.storage import db as schema
from bioquorum.storage.db import Database
from bioquorum.storage.events import EventStore
from bioquorum.storage.projections import apply_all


def snapshot(database: Database) -> dict[str, list[dict[str, Any]]]:
    """Every projection table as sorted lists of row dicts (for equality tests)."""
    out: dict[str, list[dict[str, Any]]] = {}
    with database.connect() as conn:
        for table in schema.PROJECTION_TABLES:
            rows = [dict(r._mapping) for r in conn.execute(select(table))]
            rows.sort(key=lambda r: r["id"])
            out[table.name] = rows
    return out


def rebuild(database: Database) -> int:
    """Truncate projections and replay all events. Returns the number of events replayed."""
    store = EventStore(database)
    events = list(store.iter_all())
    with database.begin() as conn:
        for table in schema.PROJECTION_TABLES:
            conn.execute(table.delete())
        for ev in events:
            apply_all(conn, ev)
    return len(events)
