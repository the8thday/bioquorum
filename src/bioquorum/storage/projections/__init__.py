"""Projections: derive entity tables from events (ADR-002).

``apply_all`` is called inside the same transaction as the event insert, and again by
``rebuild`` on an empty set of tables. Both paths must produce identical rows; a test
asserts this with randomly generated event streams.
"""

from __future__ import annotations

from sqlalchemy.engine import Connection

from bioquorum.core.events import Event
from bioquorum.storage.projections.handlers import HANDLERS


def apply_all(conn: Connection, event: Event) -> None:
    handler = HANDLERS.get(event.type)
    if handler is not None:
        handler(conn, event, event.parsed())


__all__ = ["HANDLERS", "apply_all"]
