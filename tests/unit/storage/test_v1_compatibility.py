from pathlib import Path
from zipfile import ZipFile

import pytest

from bioquorum.application import workspace
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor
from bioquorum.core.messages import Message
from bioquorum.storage.events import EventStore
from bioquorum.storage.rebuild import rebuild, snapshot

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "compat"


@pytest.mark.parametrize("minor", range(6, 13))
def test_historical_project_opens_rebuilds_and_accepts_a_follow_up(tmp_path, minor):
    with ZipFile(FIXTURES / f"v0.{minor}.zip") as archive:
        archive.extractall(tmp_path)
    ws = workspace.load(tmp_path, global_config=tmp_path / "unused.toml")
    db = ws.database()
    store = EventStore(db)
    original = list(store.iter_all())
    question = next(e for e in original if e.type == "turn_requested")
    assert all(e.parsed() for e in original)
    assert isinstance(turn := question.parsed(), ev.TurnRequested)
    assert turn.question == f"Continue v0.{minor} research"
    assert turn.attachments == []
    assert bool(turn.references) == (minor >= 11)
    types = {e.type for e in original}
    assert ("program_created" in types) == (minor >= 10)
    assert ("subtask_finished" in types) == (minor >= 12)
    assert "ollama" in ws.models_config().providers
    assert ws.name == f"v0.{minor}" and ws.classification.value == "public"
    before = snapshot(db)
    assert rebuild(db) == len(original)
    assert snapshot(db) == before
    assert list(store.iter_all()) == original
    added = store.append(
        ev.UserMessage(message=Message.user("Follow up")),
        session_id=question.session_id,
        actor=Actor.USER,
    )
    reopened = EventStore(
        workspace.load(tmp_path, global_config=tmp_path / "unused.toml").database()
    )
    assert reopened.get(added.id) == added


def test_future_event_version_is_not_interpreted_using_current_defaults():
    record = ev.Event.build(
        ev.TurnRequested(question="future"), session_id="s", seq=0, actor=Actor.USER
    )
    with pytest.raises(ValueError, match=r"Unsupported.*turn_requested"):
        record.model_copy(update={"schema_version": record.schema_version + 1}).parsed()
