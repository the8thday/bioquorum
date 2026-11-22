"""Composer controls: reasoning effort, session-scoped grants, and permission reset (ADR-051)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx2
import pytest
from pydantic import BaseModel
from pydantic_ai import messages as pa
from pydantic_ai.usage import RequestUsage

from bioquorum.application.controls import ResearchControls, session_grants
from bioquorum.application.workspace import Workspace, init_project, load
from bioquorum.core import events as ev
from bioquorum.core.enums import Classification, ModelRole, PermissionClass
from bioquorum.core.messages import Message, MessageRole, ToolCallPart
from bioquorum.models.client import ModelClient
from bioquorum.models.config import ConfigError, ModelsConfig
from bioquorum.models.routing import Router
from bioquorum.server.app import create_app
from bioquorum.storage.db import Database
from bioquorum.storage.events import EventStore
from bioquorum.tools.registry import ToolRegistry
from bioquorum.tools.spec import ToolResult, ToolSpec

HOST = "127.0.0.1:8181"
ORIGIN = f"http://{HOST}"


def test_event_upgrades_for_reasoning_effort():
    v4 = ev.upgrade_payload("turn_requested", 4, {"question": "Check TYK2"})
    assert v4["reasoning_effort"] is None

    v1 = ev.upgrade_payload(
        "model_call_started",
        1,
        {
            "call_id": "c1",
            "provider": "openai",
            "model": "gpt-6.2-sol",
            "role": "worker",
            "classification": "public",
        },
    )
    assert v1["reasoning_effort"] is None


def test_models_config_reasoning_support():
    cfg = ModelsConfig.from_dict(
        {
            "providers": {
                "openai": {},
                "deepseek": {},
                "ollama": {},
            },
            "roles": {"worker": "openai:gpt-6.2-sol"},
            "models": {
                "openai:no-think": {"reasoning": False},
                "deepseek:reasoner": {"reasoning": True},
            },
        }
    )
    assert cfg.supports_reasoning_effort("openai:gpt-6.2-sol") is True
    assert cfg.supports_reasoning_effort("openai:no-think") is False
    assert cfg.supports_reasoning_effort("deepseek:chat") is False
    assert cfg.supports_reasoning_effort("deepseek:reasoner") is True
    assert cfg.supports_reasoning_effort("ollama:qwen3") is False

    with pytest.raises(ConfigError, match="reasoning must be true or false"):
        ModelsConfig.from_dict({"models": {"openai:x": {"reasoning": "high"}}})


@pytest.mark.anyio
async def test_model_client_forwards_thinking_only_for_supported_worker(tmp_path: Path):
    seen_settings: list[dict[str, Any] | None] = []

    class RecordingModel:
        async def request(self, _history, settings, _params):
            seen_settings.append(dict(settings) if settings else None)
            return pa.ModelResponse(
                parts=[pa.TextPart(content="ok")],
                usage=RequestUsage(input_tokens=5, output_tokens=2),
            )

    cfg = ModelsConfig.from_dict(
        {
            "providers": {"openai": {}, "deepseek": {}},
            "roles": {
                "worker": "openai:gpt-6.2-sol",
                "reviewer": "openai:gpt-6.2-sol",
            },
        }
    )
    store = EventStore(Database.memory())
    client = ModelClient(
        Router(cfg), store, factory=lambda _route: RecordingModel()  # type: ignore[arg-type]
    ).with_reasoning("high")

    await client.complete(
        ModelRole.WORKER,
        [Message.user("hi")],
        session_id="s1",
        classification=Classification.PUBLIC,
    )
    await client.complete(
        ModelRole.REVIEWER,
        [Message.user("review")],
        session_id="s1",
        classification=Classification.PUBLIC,
    )
    assert seen_settings == [{"thinking": "high"}, None]

    started = [
        e.parsed()
        for e in store.history("s1")
        if e.type == ev.ModelCallStarted.event_type
    ]
    assert [s.reasoning_effort for s in started if isinstance(s, ev.ModelCallStarted)] == [
        "high",
        None,
    ]


class _LookupArgs(BaseModel):
    query: str


def _make_controls(tmp_path: Path, *, ask_tool_calls: bool = False) -> ResearchControls:
    ws = init_project(tmp_path, classification=Classification.PUBLIC)
    (tmp_path / "bioquorum.toml").write_text(
        f"""
[project]
id = "{ws.project_id}"
name = "Antibody Lab"
classification = "public"

[providers.openai]
[providers.deepseek]

[roles]
worker = ["openai:gpt-6.2-sol", "deepseek:deepseek-chat"]
reviewer = "openai:gpt-6.2-sol"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    ws = load(tmp_path)

    def make_client(workspace: Workspace, store: EventStore) -> ModelClient:
        cfg = workspace.models_config()

        class FakeClient(ModelClient):
            def __init__(self) -> None:
                super().__init__(Router(cfg), store)

            async def _call(self, route, messages, tools, on_delta=None, effort=None):
                usage = {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                }
                if route.role is ModelRole.REVIEWER:
                    return Message.assistant('{"claims": []}'), usage
                if ask_tool_calls and messages[-1].role is not MessageRole.TOOL:
                    return (
                        Message(
                            role=MessageRole.ASSISTANT,
                            parts=[ToolCallPart(call_id="tc1", name="ext_lookup", args={"query": "IL11"})],
                        ),
                        usage,
                    )
                label = f"effort={effort or 'default'}"
                return Message.assistant(f"Recorded finding ({label})."), usage

        return FakeClient()

    def registry_factory() -> ToolRegistry:
        reg = ToolRegistry()

        async def run_lookup(args: _LookupArgs, _ctx) -> ToolResult:
            return ToolResult.text(f"found {args.query}")

        reg.register(
            ToolSpec(
                name="ext_lookup",
                description="External database lookup",
                args_model=_LookupArgs,
                permission=PermissionClass.CONNECTOR,
                classification_floor=Classification.PUBLIC,
                target=lambda _a, _c: "pubmed.example",
                handler=run_lookup,
            )
        )
        return reg

    return ResearchControls(ws, make_client=make_client, registry_factory=registry_factory)


@pytest.mark.anyio
async def test_api_reasoning_effort_and_session_grants_lifecycle(tmp_path: Path):
    controls = _make_controls(tmp_path, ask_tool_calls=True)
    app = create_app(controls, HOST)
    transport = httpx2.ASGITransport(app=app)
    headers = {"Origin": ORIGIN}

    async with httpx2.AsyncClient(transport=transport, base_url=ORIGIN, headers=headers) as http:
        meta = (await http.get("/api/sessions")).json()
        assert meta["default_worker"] == "openai:gpt-6.2-sol"
        by_spec = {m["spec"]: m for m in meta["worker_models"]}
        assert by_spec["openai:gpt-6.2-sol"]["reasoning"] is True
        assert by_spec["deepseek:deepseek-chat"]["reasoning"] is False
        assert any(g["permission"] == "execute" for g in meta["project_permissions"])

        sid = (await http.post("/api/sessions", json={"title": "IL11 study"})).json()["id"]

        # Unsupported model rejects reasoning_effort
        bad = await http.post(
            f"/api/sessions/{sid}/turns",
            json={
                "question": "Check IL11",
                "worker_model": "deepseek:deepseek-chat",
                "reasoning_effort": "high",
            },
        )
        assert bad.status_code == 400

        # Supported model accepts reasoning_effort="high" and asks for connector permission
        ok = await http.post(
            f"/api/sessions/{sid}/turns",
            json={"question": "Check IL11", "reasoning_effort": "high"},
        )
        assert ok.status_code == 202

        # Wait for permission prompt
        perm_id = None
        snap: dict[str, Any] = {}
        for _ in range(50):
            snap = (await http.get(f"/api/sessions/{sid}")).json()
            if snap["permissions"]:
                perm_id = snap["permissions"][0]["request_id"]
                break
            await asyncio.sleep(0.02)
        assert perm_id is not None, (snap["turns"], snap["activity"])

        # Approve for the entire session
        resp = await http.post(
            f"/api/sessions/{sid}/permissions/{perm_id}",
            json={"grant": True, "remember": True},
        )
        assert resp.status_code == 204

        # Wait for turn to finish
        for _ in range(50):
            snap = (await http.get(f"/api/sessions/{sid}")).json()
            if snap["turns"] and snap["turns"][-1]["status"] == "completed":
                break
            await asyncio.sleep(0.02)
        assert snap["turns"][-1]["status"] == "completed"
        assert snap["last_reasoning_effort"] == "high"
        assert snap["answers"][-1]["reasoning_effort"] == "high"
        assert snap["session_grants"] == [{"permission": "connector", "target": "pubmed.example"}]
        assert session_grants(controls.store.history(sid)) == [
            (PermissionClass.CONNECTOR, "pubmed.example")
        ]

        # Second turn should NOT ask for permission again because of session grant
        ok2 = await http.post(
            f"/api/sessions/{sid}/turns",
            json={"question": "Follow-up on IL11", "reasoning_effort": "low"},
        )
        assert ok2.status_code == 202
        for _ in range(50):
            snap = (await http.get(f"/api/sessions/{sid}")).json()
            if len(snap["turns"]) == 2 and snap["turns"][-1]["status"] == "completed":
                break
            await asyncio.sleep(0.02)
        assert snap["turns"][-1]["status"] == "completed"
        assert snap["answers"][-1]["reasoning_effort"] == "low"

        # Reset session permissions
        reset = await http.post(f"/api/sessions/{sid}/permissions/reset", json={})
        assert reset.status_code == 204
        snap = (await http.get(f"/api/sessions/{sid}")).json()
        assert snap["session_grants"] == []
        assert any(a["type"] == "session_permissions_reset" for a in snap["activity"])

        # Resetting again when empty returns 409
        reset2 = await http.post(f"/api/sessions/{sid}/permissions/reset", json={})
        assert reset2.status_code == 409

    await controls.close()
