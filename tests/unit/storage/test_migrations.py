from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import inspect, text

from bioquorum.core import events as ev
from bioquorum.core.enums import Actor
from bioquorum.storage.db import Database
from bioquorum.storage.events import EventStore
from bioquorum.storage.rebuild import rebuild, snapshot


def test_v1_upgrade_preserves_events_and_rebuilds_projection(tmp_path):
    path = tmp_path / "old.db"
    db = Database.at(path)
    store = EventStore(db)
    old = store.append(ev.SessionTitleSet(title="legacy"), session_id="S", actor=Actor.USER)
    with db.begin() as conn:
        conn.execute(text("DROP TABLE alembic_version"))
        conn.execute(text("UPDATE schema_meta SET value = '1' WHERE key = 'version'"))
        conn.execute(text("ALTER TABLE model_calls DROP COLUMN cost_known"))
    db.engine.dispose()

    with ThreadPoolExecutor(max_workers=2) as pool:
        opened = list(pool.map(Database.at, [path, path]))
    for migrated in opened:
        assert EventStore(migrated).read("S") == [old]
        assert "cost_known" in {
            c["name"] for c in inspect(migrated.engine).get_columns("model_calls")
        }
        before = snapshot(migrated)
        assert rebuild(migrated) == 1
        assert snapshot(migrated) == before
        migrated.engine.dispose()


def test_new_db_has_current_revision(db):
    with db.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0002"
