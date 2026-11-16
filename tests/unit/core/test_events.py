import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from bioquorum.core import events as ev
from bioquorum.core.enums import Actor
from bioquorum.core.events import (
    Event,
    EventPayload,
    event_types,
    payload_class,
    register,
    upgrader,
)
from bioquorum.core.ids import id_time
from bioquorum.core.messages import Message

ARCH = Path(__file__).resolve().parents[3] / "docs" / "ARCHITECTURE.md"


def test_every_type_in_architecture_table_is_registered():
    text = ARCH.read_text(encoding="utf-8")
    section = text.split("### 5.3")[1].split("### 5.4")[0]
    documented = set(re.findall(r"`([a-z_]+)`", section))
    assert documented, "could not parse §5.3"
    missing = documented - set(event_types())
    extra = set(event_types()) - documented
    assert not missing, f"documented but not registered: {missing}"
    assert not extra, f"registered but not documented in ARCHITECTURE.md §5.3: {extra}"


def test_build_and_parse_round_trip():
    payload = ev.UserMessage(message=Message.user("hi"))
    e = Event.build(payload, session_id="s", seq=3, actor=Actor.USER)
    assert e.type == "user_message" and e.schema_version == 1
    assert e.ts == id_time(e.id)
    assert e.parsed() == payload


def test_payloads_are_frozen_and_strict():
    p = ev.PlanApproved(plan_id="p")
    with pytest.raises(ValidationError):
        p.plan_id = "q"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ev.PlanApproved(plan_id="p", extra_field=1)  # type: ignore[call-arg]


def test_duplicate_registration_fails():
    with pytest.raises(ValueError):

        @register
        class Dup(EventPayload):
            event_type = "plan_approved"


def test_schema_upgrade_path():
    @register
    class WidgetV1(EventPayload):
        event_type = "test_widget"
        schema_version = 1
        name: str

    @register
    class WidgetV2(EventPayload):
        event_type = "test_widget"
        schema_version = 2
        label: str

    @upgrader("test_widget", 1)
    def _up(d):
        return {"label": d["name"]}

    e = Event.build(WidgetV1(name="n"), session_id="s", seq=0, actor=Actor.SYSTEM)
    assert e.schema_version == 1
    parsed = e.parsed()
    assert isinstance(parsed, WidgetV2) and parsed.label == "n"
    assert payload_class("test_widget") is WidgetV2
    # cleanup so other tests see the documented set only
    ev._REGISTRY.pop("test_widget")
    ev._UPGRADERS.pop(("test_widget", 1))
