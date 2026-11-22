from bioquorum.application.controls import ResearchControls
from bioquorum.application.record import snapshot
from bioquorum.application.workspace import init_project
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor
from bioquorum.core.messages import Message
from bioquorum.core.refs import NamedValue


def test_paged_answers_bind_to_values_visible_at_their_own_event(tmp_path):
    controls = ResearchControls(init_project(tmp_path))
    sid = controls.new_session("Repeated measurement")
    for i in range(4):
        controls.store.append_chain(
            [
                ev.UserMessage(message=Message.user(f"Question {i}")),
                ev.RunStarted(run_id=f"r{i}", kernel_id="k", code="", code_hash="0" * 64),
                ev.RunFinished(
                    run_id=f"r{i}", run_key="fixture", values=[NamedValue(name="total", value=i)]
                ),
                ev.AssistantMessage(message=Message.assistant("Current: {{total}}.")),
            ],
            session_id=sid,
            actor=Actor.USER,
        )
    full = snapshot(controls, sid)
    page = snapshot(controls, sid, turn_limit=2)
    assert page["has_earlier"]
    assert page["answers"] == full["answers"][-2:]
    assert page["questions"] == full["questions"][-2:]
    assert [a["paragraphs"][0]["segments"][1]["text"] for a in full["answers"]] == [
        "0",
        "1",
        "2",
        "3",
    ]
