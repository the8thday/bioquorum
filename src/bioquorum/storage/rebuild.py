"""Rebuild every projection table from the event log, and snapshot tables for comparison."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from bioquorum.storage import db as schema
from bioquorum.storage.db import Database
from bioquorum.storage.events import EventStore
from bioquorum.storage.projections import HANDLERS, apply_all


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
    """Truncate projections and replay all events. Returns the total number of events in the log."""
    with database.begin() as conn:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        for table in schema.PROJECTION_TABLES:
            conn.execute(table.delete())
        count = conn.scalar(select(func.count()).select_from(schema.events)) or 0
        for ev in EventStore.replay_rows(conn, types=tuple(HANDLERS)):
            apply_all(conn, ev)
    return count
