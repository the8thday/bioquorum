import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from bioquorum.storage.db import Database
from bioquorum.storage.events import EventStore
from bioquorum.storage.rebuild import rebuild, snapshot
from tests.unit.storage.streams import generate


def _check(seed: int) -> None:
    db = Database.memory()
    store = EventStore(db)
    generate(store, seed)
    live = snapshot(db)
    assert any(live[k] for k in live), "stream produced no projections"
    n = rebuild(db)
    assert n == store.count()
    assert snapshot(db) == live


@pytest.mark.parametrize("seed", range(10))
def test_rebuild_equals_live_projection(seed):
    _check(seed)


@settings(max_examples=25, deadline=None)
@given(st.integers(min_value=0, max_value=2**31))
def test_rebuild_equals_live_projection_hypothesis(seed):
    _check(seed)


def test_rebuild_is_idempotent():
    db = Database.memory()
    generate(EventStore(db), 7)
    rebuild(db)
    first = snapshot(db)
    rebuild(db)
    assert snapshot(db) == first


def test_rebuild_reserves_writer_before_reading_log(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from sqlalchemy import event

    from bioquorum.core import events as ev
    from bioquorum.core.enums import Actor

    db = Database.at(tmp_path / "record.db")
    writer = Database.at(tmp_path / "record.db")
    db.create_all()
    generate(EventStore(db), 3)
    project_id = snapshot(db)["projects"][0]["id"]
    reserved, attempting = Event(), Event()

    @event.listens_for(db.engine, "after_cursor_execute")
    def during_rebuild(conn, cursor, statement, parameters, context, executemany):
        if statement == "BEGIN IMMEDIATE":
            reserved.set()
            assert attempting.wait(5)

    @event.listens_for(writer.engine, "before_cursor_execute")
    def during_write(conn, cursor, statement, parameters, context, executemany):
        if statement == "BEGIN IMMEDIATE":
            attempting.set()

    with ThreadPoolExecutor() as pool:
        job = pool.submit(rebuild, db)
        assert reserved.wait(5)
        EventStore(writer).append(
            ev.SessionCreated(project_id=project_id, title="Concurrent"),
            session_id="extra",
            actor=Actor.USER,
        )
        job.result()
    assert any(row["id"] == "extra" for row in snapshot(db)["sessions"])
