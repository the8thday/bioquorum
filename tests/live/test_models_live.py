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
