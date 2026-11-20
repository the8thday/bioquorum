import pytest
from pydantic_ai import messages as pa
from pydantic_ai.models.function import FunctionModel

from bioquorum.application import demo, record
from bioquorum.core import events as ev
from bioquorum.models import ModelClient, Router


@pytest.mark.anyio
async def test_manual_compaction_preserves_evidence_and_next_turn_context(tmp_path):
    from bioquorum.agent.context import conversation
    from bioquorum.application import context_panel

    controls = demo.make_demo(tmp_path)
    sid = controls.new_session("Recorded evidence")
    turn = controls.submit(sid, demo.QUESTION, steps=demo.STEPS)
    await controls.decide_plan(sid, turn.id, approve=True)
    await controls.tasks[sid].task
    answer = record.snapshot(controls, sid)["answers"][0]
    ref = next(
        s["ref"]
        for p in answer["paragraphs"]
        for s in p["segments"]
        if s.get("ref", {}).get("type") == "quote"
    )
    original = record.binding(controls, sid, answer["id"], ref["call_id"], "quote")
    controls.demo = False
    controls.ws.config = {
        "providers": {"openai": {}},
        "roles": {"worker": "openai:fixture"},
        "models": {"openai:fixture": {"context_window": 8000}},
    }

    def summarize(messages, info):
        return pa.ModelResponse(parts=[pa.TextPart(content="Earlier literature search.")])

    controls.make_client = lambda ws, store: ModelClient(
        Router(ws.models_config()), store, factory=lambda _: FunctionModel(summarize)
    )
    before = context_panel.view(controls, sid)
    await context_panel.compact(controls, sid)
    after = context_panel.view(controls, sid)
    assert 0 < after["estimated_tokens"] < before["estimated_tokens"]
    assert before["message_counts"]["assistant"] > 0
    assert before["message_counts"]["tool"] > 0
    assert after["window"] == 8000
    assert after["summaries"][-1]["summary"] == "Earlier literature search."
    assert any(
        "Earlier literature search." in msg.text()
        for msg in conversation(controls.store.history(sid))
    )
    assert record.binding(controls, sid, answer["id"], ref["call_id"], "quote") == original
    assert sum(e.type == ev.ContextCompacted.event_type for e in controls.require_session(sid)) == 1
