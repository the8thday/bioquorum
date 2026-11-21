import pytest

from bioquorum.application import settings, workspace
from bioquorum.application.controls import ResearchControls


def test_minimal_profile_reloads_and_editing_a_switch_can_leave_it(tmp_path):
    ws = workspace.init_project(tmp_path)
    controls = ResearchControls(ws)
    saved = settings.save(
        controls, settings.SettingsEdit(revision=None, patch={"profile": {"name": "minimal"}})
    )
    assert saved["profile"]["name"] == "minimal"
    assert not workspace.load(tmp_path).agent_options().binding
    changed = settings.save(
        controls,
        settings.SettingsEdit(
            revision=saved["revision"],
            patch={"profile": {"name": "custom"}, "agent": {"coverage": True}},
        ),
    )
    assert changed["agent"]["coverage"]
    assert workspace.load(tmp_path).agent_options().coverage


def test_invalid_profile_does_not_create_an_event(tmp_path):
    ws = workspace.init_project(tmp_path)
    controls = ResearchControls(ws)
    with pytest.raises(ValueError):
        settings.save(
            controls, settings.SettingsEdit(revision=None, patch={"profile": {"name": "made-up"}})
        )
    assert settings.snapshot(controls)["revision"] is None


def test_followup_requires_a_recorded_first_pass_item_with_references_off(tmp_path):
    from bioquorum.application.first_pass import sections, valid_followup
    from bioquorum.core import events as ev
    from bioquorum.core.enums import Actor
    from bioquorum.core.messages import Message

    controls = ResearchControls(workspace.init_project(tmp_path))
    sid = controls.new_session("First pass")

    def add(payload, actor):
        return controls.store.append(payload, session_id=sid, actor=actor)

    add(ev.UserMessage(message=Message.user("Assess the data.")), Actor.USER)
    add(ev.FirstPassStarted(), Actor.SYSTEM)
    body = "Judgment: Sparse data.\nStrongest counterevidence: One contrary result.\n**Disagreement:**\nWhich population matters?\nNext step: Search the registry."
    answer = add(ev.AssistantMessage(message=Message.assistant(body)), Actor.AGENT)
    items = sections(body)
    assert items["Disagreement"] == "Which population matters?"
    history = controls.store.history(sid)
    assert valid_followup(
        history, ev.TurnReference(kind="answer", id=answer.id, excerpt=items["Next step"])
    )
    assert not valid_followup(
        history, ev.TurnReference(kind="answer", id=answer.id, excerpt="Sparse data.")
    )


def test_first_pass_headings_must_be_whole_names():
    from bioquorum.application.first_pass import sections

    assert sections("Judgmental wording.\nNext steps: Check.") == {}


def test_reply_records_which_clarification_the_researcher_answered(tmp_path):
    from bioquorum.core import events as ev
    from bioquorum.core.enums import Actor
    from bioquorum.core.messages import Message

    ws = workspace.init_project(tmp_path)
    ws.config["agent"] = {"first_pass": True}
    controls = ResearchControls(ws)
    sid = controls.new_session("Cohort scope")

    def add(payload, actor):
        return controls.store.append(payload, session_id=sid, actor=actor)

    add(ev.UserMessage(message=Message.user("Which cohort?")), Actor.USER)
    add(ev.FirstPassStarted(), Actor.SYSTEM)
    ambiguity = add(
        ev.ClarificationRequested(question="Which age?", assumption="Adults."), Actor.AGENT
    )
    reply = controls.submit(
        sid,
        "Use adults.",
        steps=["Check age"],
        references=[ev.TurnReference(kind="activity", id=ambiguity.id)],
    )
    assert controls.store.read(sid, types=["clarification_answered"])[0].payload == {
        "request_id": ambiguity.id,
        "turn_id": reply.id,
    }


def test_new_projects_record_the_recommendation_when_evidence_is_available(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "RECOMMENDED", None)
    before = workspace.init_project(tmp_path / "before")
    monkeypatch.setattr(workspace, "RECOMMENDED", {"binding": True, "coverage": True})
    after = workspace.init_project(tmp_path / "after")
    assert after.profile_name == "recommended" and after.agent_options().coverage
    monkeypatch.setattr(workspace, "RECOMMENDED", {"binding": True, "coverage": False})
    assert workspace.load(after.root).agent_options().coverage
    assert workspace.load(before.root).profile_name == "custom"


def test_minimal_profile_wins_over_conflicting_file_settings(tmp_path):
    ws = workspace.init_project(tmp_path)
    ws.config = {"profile": {"name": "minimal"}, "agent": {"skills": True, "coverage": True}}
    assert not ws.agent_options().skills and not ws.agent_options().coverage


def test_explicit_project_switch_overrides_new_project_defaults(tmp_path):
    workspace.init_project(tmp_path)
    (tmp_path / workspace.CONFIG_FILE).write_text("[agent]\ncoverage = true\n")
    ws = workspace.load(tmp_path)
    state = settings.snapshot(ResearchControls(ws))
    assert state["profile"]["name"] == "custom"
    assert state["agent"]["coverage"] and state["agent"]["binding"]
    assert state["revision"] is None


def test_v1_project_reopens_with_its_original_file_settings(tmp_path):
    import json

    from bioquorum.core import events as ev
    from bioquorum.core.enums import Actor
    from bioquorum.storage.db import Database
    from bioquorum.storage.events import EventStore

    state = tmp_path / workspace.STATE_DIR
    state.mkdir()
    meta = {"project_id": "legacy", "name": "Legacy project", "classification": "internal"}
    (state / "project.json").write_text(json.dumps(meta))
    store = EventStore(Database.at(state / "bioquorum.db"))
    event = store.append(
        ev.ProjectCreatedV1.model_validate({**meta, "root_dir": str(tmp_path)}),
        actor=Actor.USER,
        session_id=None,
    )
    parsed = event.parsed()
    assert isinstance(parsed, ev.ProjectCreated) and parsed.recommended_agent is None
    (tmp_path / workspace.CONFIG_FILE).write_text("[agent]\nbinding = false\n")
    loaded = workspace.load(tmp_path)
    assert loaded.profile_name == "custom" and not loaded.agent_options().binding
