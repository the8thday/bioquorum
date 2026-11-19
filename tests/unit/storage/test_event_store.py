import pytest
from sqlalchemy.exc import IntegrityError

from bioquorum.core import events as ev
from bioquorum.core.enums import Actor
from bioquorum.core.messages import Message


def test_seq_is_per_session_and_contiguous(store):
    a = store.append(ev.SessionCreated(project_id="p", title="a"), session_id="A", actor=Actor.USER)
    b = store.append(ev.SessionCreated(project_id="p", title="b"), session_id="B", actor=Actor.USER)
    a2 = store.append(ev.UserMessage(message=Message.user("hi")), session_id="A", actor=Actor.USER)
    assert (a.seq, b.seq, a2.seq) == (0, 0, 1)
    assert [e.seq for e in store.read("A")] == [0, 1]
    assert store.count() == 3


def test_project_level_events_have_null_session(store):
    e = store.append(
        ev.ProjectCreated(project_id="p", name="n", root_dir="/"), session_id=None, actor=Actor.USER
    )
    assert e.session_id is None and e.seq == 0
    assert [x.id for x in store.read(None)] == [e.id]


def test_read_round_trips_payload_and_causation(store):
    first = store.append(
        ev.UserMessage(message=Message.user("q")), session_id="S", actor=Actor.USER
    )
    second = store.append(
        ev.AssistantMessage(message=Message.assistant("a")),
        session_id="S",
        actor=Actor.AGENT,
        causation_id=first.id,
    )
    got = store.read("S")
    assert got == [first, second]
    assert got[1].causation_id == first.id
    assert got[1].parsed().message.text() == "a"
    assert store.get(first.id) == first
    assert store.get("nope") is None


def test_iter_all_is_in_insertion_order_across_sessions(store):
    ids = []
    for i in range(30):
        sid = "A" if i % 2 else "B"
        ids.append(
            store.append(
                ev.UserMessage(message=Message.user(str(i))), session_id=sid, actor=Actor.USER
            ).id
        )
    assert [e.id for e in store.iter_all()] == ids


def test_events_are_append_only(db, store):
    from sqlalchemy import insert

    from bioquorum.storage import db as schema

    e = store.append(ev.PlanApproved(plan_id="p"), session_id="S", actor=Actor.USER)
    with pytest.raises(IntegrityError), db.begin() as conn:
        conn.execute(
            insert(schema.events).values(
                id="X" * 26,
                session_id="S",
                seq=e.seq,
                ts="t",
                type="plan_approved",
                schema_version=1,
                actor="user",
                payload={},
            )
        )


def test_caused_events_are_atomic_when_later_projection_fails(store, monkeypatch):
    from bioquorum.storage import events

    apply = events.apply_all

    def fail_last(conn, event):
        if event.type == "assistant_message":
            raise ValueError("projection failed")
        apply(conn, event)

    monkeypatch.setattr(events, "apply_all", fail_last)
    question = ev.UserMessage(message=Message.user("question"))
    answer = ev.AssistantMessage(message=Message.assistant("answer"))
    with pytest.raises(ValueError, match="projection failed"):
        store.append_chain([question, answer], session_id="S", actor=Actor.SYSTEM)
    assert store.read("S") == []
