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
