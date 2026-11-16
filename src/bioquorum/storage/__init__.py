"""Event store and projections (ARCHITECTURE.md §5, ADR-002).

Only this package may import SQLAlchemy. The ``events`` table is the single source of
truth; every other table is a projection rebuilt by :func:`bioquorum.storage.rebuild.rebuild`.
"""

from bioquorum.storage.db import Database
from bioquorum.storage.events import EventStore

__all__ = ["Database", "EventStore"]
