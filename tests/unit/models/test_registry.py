"""Provider route settings exercised over a local mock transport."""

import json
from base64 import b64decode, b64encode

import httpx2
import pytest
from openai import AsyncOpenAI
from pydantic_ai.providers.openai import OpenAIProvider

from bioquorum.core.enums import Classification, DataKind, MessageRole, ModelRole
from bioquorum.core.hashing import sha256_bytes
from bioquorum.core.messages import (
    ImageRefPart,
    Message,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolResultPart,
)
from bioquorum.core.refs import DataRef
from bioquorum.models import ModelClient, ModelsConfig, Router
from bioquorum.models import registry as model_registry


@pytest.mark.anyio
@pytest.mark.parametrize("first_reasoned", [False, True])
async def test_deepseek_tool_continuation_passes_the_required_reasoning_field(
    store, monkeypatch, first_reasoned
):
    observed = []

    def response(request):
        observed.extend(json.loads(request.content)["messages"])
        return httpx2.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 1,
                "model": "deepseek-flash",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "done"},
                    }
                ],
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(response)) as http:

        def provider(**kwargs):
            return OpenAIProvider(openai_client=AsyncOpenAI(http_client=http, **kwargs))

        monkeypatch.setattr(model_registry, "OpenAIProvider", provider)
        config = ModelsConfig.from_dict(
            {
                "providers": {"deepseek": {"base_url": "https://deepseek.test", "api_key": "none"}},
                "roles": {"worker": "deepseek:deepseek-flash"},
            }
        )
        messages = [Message.user("Calculate the requested quantity.")]
        recorded = "recorded reasoning" if first_reasoned else ""
        initial_parts = [ThinkingPart(provider="deepseek", text=recorded)] if first_reasoned else []
        for call_id, parts in [("call-a", initial_parts), ("call-b", [])]:
            call = ToolCallPart(name="python_execute", call_id=call_id, args={"code": "print(3)"})
            messages.append(Message(role=MessageRole.ASSISTANT, parts=[*parts, call]))
            messages.append(
                Message.tool(ToolResultPart(call_id=call_id, content=[TextPart(text="3")]))
            )
        await ModelClient(Router(config), store).complete(
            ModelRole.WORKER,
            messages,
            [
                {
                    "name": "python_execute",
                    "description": "Run Python",
                    "input_schema": {"type": "object"},
                }
            ],
            session_id="S",
            classification=Classification.PUBLIC,
        )

    tool_turns = [msg for msg in observed if msg.get("tool_calls")]
    assert [msg["reasoning_content"] for msg in tool_turns] == [recorded, ""]


@pytest.mark.anyio
@pytest.mark.parametrize("api", ["chat", "responses"])
async def test_openai_adapter_sends_a_tool_figure_as_an_image(store, monkeypatch, api):
    observed = []
    png = b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p1sAAAAASUVORK5CYII="
    )

    def response(request):
        observed.append(json.loads(request.content))
        if api == "responses":
            return httpx2.Response(
                200,
                json={
                    "id": "resp_image",
                    "object": "response",
                    "created_at": 1,
                    "status": "completed",
                    "model": "local-vision",
                    "output": [
                        {
                            "id": "msg_1",
                            "type": "message",
                            "role": "assistant",
                            "status": "completed",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "done",
                                    "annotations": [],
                                    "logprobs": [],
                                }
                            ],
                        }
                    ],
                },
            )
        return httpx2.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 1,
                "model": "local-vision",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "done"},
                    }
                ],
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(response)) as http:

        def provider(**kwargs):
            return OpenAIProvider(openai_client=AsyncOpenAI(http_client=http, **kwargs))

        monkeypatch.setattr(model_registry, "OpenAIProvider", provider)
        cfg = ModelsConfig.from_dict(
            {
                "providers": {"ollama": {"api": api}},
                "roles": {"worker": "ollama:local-vision"},
                "models": {"ollama:local-vision": {"vision": True}},
            }
        )
        digest = sha256_bytes(png)
        ref = DataRef(
            uri=f"cas://{digest}", content_hash=digest, size=len(png), kind=DataKind.FIGURE
        )
        call = ToolCallPart(name="python_execute", call_id="image-call", args={"code": "pass"})
        messages = [
            Message.user("Inspect the figure"),
            Message(role=MessageRole.ASSISTANT, parts=[call]),
            Message.tool(
                ToolResultPart(
                    call_id=call.call_id, content=[ImageRefPart(ref=ref, mime="image/png")]
                )
            ),
        ]
        await ModelClient(Router(cfg), store, image_loader=lambda _: png).complete(
            ModelRole.WORKER, messages, session_id="S", classification=Classification.PUBLIC
        )

    assert len(observed) == 1
    assert "data:image/png;base64," + b64encode(png).decode() in json.dumps(observed[0])
