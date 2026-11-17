from __future__ import annotations

import pytest

from bioquorum.storage.db import Database
from bioquorum.storage.events import EventStore


@pytest.fixture
def db() -> Database:
    return Database.memory()


@pytest.fixture
def store(db: Database) -> EventStore:
    return EventStore(db)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
