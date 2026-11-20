"""Real providers through our own ModelClient: tool call -> tool result -> answer.

Checks the two provider quirks convert.py relies on: DeepSeek needs its reasoning
replayed, and OpenAI Responses accepts our ULIDs as tool call IDs.
Run: uv run pytest -m live tests/live/test_models_live.py
"""

import os

import pytest

from bioquorum.core.enums import Classification, ModelRole, Trust
from bioquorum.core.messages import Message, TextPart, ToolResultPart
from bioquorum.models import ModelClient, ModelsConfig, Router

pytestmark = [pytest.mark.live, pytest.mark.anyio]
TOOLS = [
    {
        "name": "python_execute",
        "description": "Run Python and return stdout.",
        "input_schema": {
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
        },
    }
]


@pytest.mark.parametrize(
    ("spec", "key"),
    [("deepseek:deepseek-v4-pro", "DEEPSEEK_API_KEY"), ("openai:gpt-6.1-sol", "OPENAI_API_KEY")],
)
async def test_two_turn_tool_roundtrip(store, spec, key):
    if not os.environ.get(key):
        pytest.skip(f"{key} not set")
    cfg = ModelsConfig.from_dict(
        {"providers": {"openai": {}, "deepseek": {}}, "roles": {"worker": spec}}
    )
    client = ModelClient(Router(cfg), store)
    history = [
        Message.system("Use the python_execute tool for any arithmetic. Be terse."),
        Message.user("What is 1234 * 5678?"),
    ]
    first = await client.complete(
        ModelRole.WORKER, history, TOOLS, session_id="S", classification=Classification.PUBLIC
    )
    (call,) = first.message.tool_calls()
    assert call.name == "python_execute"
    history += [
        first.message,
        Message.tool(
            ToolResultPart(
                call_id=call.call_id, content=[TextPart(text="7006652")], trust=Trust.UNTRUSTED
            )
        ),
    ]
    second = await client.complete(
        ModelRole.WORKER, history, TOOLS, session_id="S", classification=Classification.PUBLIC
    )
    assert "7006652" in second.message.text().replace(",", "")
    assert [e.type for e in store.read("S")].count("model_call_finished") == 2


async def test_workbench_switches_openai_deepseek_openai(tmp_path, monkeypatch):
    """Exercise a single real workbench session across both provider protocols."""
    from bioquorum.application import record
    from bioquorum.application.controls import ResearchControls
    from bioquorum.application.workspace import init_project

    missing = [name for name in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY") if not os.getenv(name)]
    if missing:
        pytest.skip("Missing in this terminal: " + ", ".join(missing))
    monkeypatch.delenv("BQ_MODEL_CASSETTE", raising=False)
    ws = init_project(tmp_path, classification=Classification.PUBLIC)
    first = "openai:gpt-6.1-sol"
    second = "deepseek:deepseek-v4-pro"
    ws.config = {
        "providers": {"openai": {}, "deepseek": {}},
        "roles": {"worker": first, "reviewer": first},
        "models": {second: {}},
    }
    controls = ResearchControls(ws)
    sid = controls.new_session("Live family switch")
    for spec, prompt in zip(
        (first, second, first),
        (
            "Reply with the word cedar.",
            "What word did I ask you to say? Reply with that one word.",
            "Repeat the word from the first turn, in one word.",
        ),
        strict=True,
    ):
        controls.submit(sid, prompt, worker_model=spec)
        await controls.tasks[sid].task
        snapshot = record.snapshot(controls, sid)
        assert snapshot["turns"][-1]["status"] == "completed"
        assert snapshot["answers"][-1]["model"] == spec
        assert not [
            event for event in controls.require_session(sid) if event.type == "model_call_failed"
        ]
