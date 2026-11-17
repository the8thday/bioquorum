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


@pytest.fixture(autouse=True)
def _isolated_global_config(tmp_path_factory, monkeypatch):
    """Never read the developer's ~/.bioquorum/config.toml in tests."""
    monkeypatch.setenv("BQ_CONFIG", str(tmp_path_factory.mktemp("cfg") / "config.toml"))
