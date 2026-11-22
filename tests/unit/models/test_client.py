import pytest
from pydantic_ai import messages as pa
from pydantic_ai.models.function import FunctionModel

from bioquorum.core.enums import Classification, DataKind, MessageRole, ModelRole
from bioquorum.core.hashing import sha256_bytes
from bioquorum.core.messages import ImageRefPart, Message, ToolCallPart, ToolResultPart
from bioquorum.core.refs import DataRef
from bioquorum.models import (
    CassetteMissError,
    CassetteMode,
    ModelCassette,
    ModelClient,
    ModelsConfig,
    ModelUnavailableError,
    Router,
)
from bioquorum.policy import PolicyViolation
from bioquorum.policy.secrets import register_secret
from bioquorum.storage.rebuild import snapshot

pytestmark = pytest.mark.anyio
CONFIG = ModelsConfig.from_dict(
    {
        "providers": {"openai": {}, "deepseek": {}},
        "roles": {"worker": ["openai:m1", "deepseek:m2"]},
        "models": {},
    }
)
TOOLS = [{"name": "python_execute", "description": "d", "input_schema": {"type": "object"}}]


def answer(text):
    def fn(messages, info):
        return pa.ModelResponse(parts=[pa.TextPart(content=text)])

    return FunctionModel(fn)


def failing(msg):
    def fn(messages, info):
        raise RuntimeError(msg)

    return FunctionModel(fn)


def factory(by_spec):
    return lambda route: by_spec[str(route.spec)]


def types(store):
    return [e.type for e in store.read("S")]


async def test_optional_output_limit_reaches_the_provider(store):
    def limited(_messages, info):
        assert info.model_settings is not None and info.model_settings.get("max_tokens") == 90
        return pa.ModelResponse(parts=[pa.TextPart(content="done")])

    client = ModelClient(
        Router(CONFIG), store, factory=lambda _: FunctionModel(limited), max_output_tokens=90
    )
    assert (
        await client.complete(
            ModelRole.WORKER,
            [Message.user("q")],
            session_id="S",
            classification=Classification.PUBLIC,
        )
    ).message.text() == "done"


@pytest.mark.parametrize(
    ("configured", "enabled", "expected"),
    [(False, True, False), (True, False, False), (True, True, True)],
)
async def test_image_bytes_require_opt_in_and_a_vision_route(store, configured, enabled, expected):
    data = b"\x89PNG\r\n\x1a\nfixture-image"
    digest = sha256_bytes(data)
    image = ImageRefPart(
        ref=DataRef(
            uri=f"cas://{digest}", content_hash=digest, size=len(data), kind=DataKind.FIGURE
        ),
        mime="image/png",
    )
    call = ToolCallPart(call_id="figure-call", name="python_execute", args={"code": "..."})
    history = [
        Message.user("Inspect the result"),
        Message(role=MessageRole.ASSISTANT, parts=[call]),
        Message.tool(ToolResultPart(call_id=call.call_id, content=[image])),
    ]
    cfg = ModelsConfig.from_dict(
        {
            "providers": {"ollama": {}},
            "roles": {"worker": "ollama:m"},
            "models": {"ollama:m": {"vision": configured}},
        }
    )
    loads = []

    def load(ref):
        loads.append(ref)
        return data

    def inspect(messages, _info):
        finals = messages[-1].parts
        ret = finals[0]
        assert isinstance(ret, pa.ToolReturnPart)
        observed = any(
            isinstance(part, pa.BinaryContent) and part.data == data for part in ret.files
        )
        assert observed is expected
        return pa.ModelResponse(
            parts=[pa.TextPart(content="image present" if observed else "text only")]
        )

    client = ModelClient(
        Router(cfg),
        store,
        factory=lambda _: FunctionModel(inspect),
        image_loader=load if enabled else None,
    )
    result = await client.complete(
        ModelRole.WORKER, history, session_id="S", classification=Classification.PUBLIC
    )
    assert result.message.text() == ("image present" if expected else "text only")
    assert len(loads) == int(expected)
    assert "fixture-image" not in str([e.payload for e in store.read("S")])


async def test_success_writes_started_and_finished(store):
    client = ModelClient(
        Router(CONFIG),
        store,
        factory=factory({"openai:m1": answer("hi")}),
        prices={"openai:m1": (1.0, 2.0)},
    )
    c = await client.complete(
        ModelRole.WORKER,
        [Message.user("q")],
        TOOLS,
        session_id="S",
        classification=Classification.PUBLIC,
    )
    assert c.message.text() == "hi" and str(c.route.spec) == "openai:m1"
    assert types(store) == ["model_call_started", "model_call_finished"]
    (row,) = snapshot(store.db)["model_calls"]
    assert row["provider"] == "openai" and row["role"] == "worker" and row["input_tokens"] > 0
    assert row["cost_usd"] > 0


async def test_stream_persists_only_completed_reply_and_prices(store):
    from bioquorum.core import events as ev

    async def chunks(messages, info):
        yield "first "
        assert types(store) == ["model_call_started"]
        yield "second"

    cfg = ModelsConfig.from_dict(
        {
            "providers": {"ollama": {}},
            "roles": {"worker": "ollama:m"},
            "models": {"ollama:m": {"input_usd_per_million": 1, "output_usd_per_million": 2}},
        }
    )
    client = ModelClient(
        Router(cfg), store, factory=lambda route: FunctionModel(stream_function=chunks)
    )
    deltas: list[str] = []
    result = await client.complete(
        ModelRole.WORKER,
        [Message.user("q")],
        session_id="S",
        classification=Classification.PUBLIC,
        on_delta=deltas.append,
    )
    tail = store.read("S")[-1].parsed()
    assert isinstance(tail, ev.ModelCallFinished)
    assert result.message.text() == "".join(deltas) == "first second"
    assert tail.cost_known and tail.cost_usd > 0


async def test_fallback_on_failure_and_secrets_redacted(store):
    register_secret("super-secret-key-123")
    client = ModelClient(
        Router(CONFIG),
        store,
        factory=factory(
            {"openai:m1": failing("401 bad key super-secret-key-123"), "deepseek:m2": answer("ok")}
        ),
    )
    c = await client.complete(
        ModelRole.WORKER,
        [Message.user("q")],
        session_id="S",
        classification=Classification.INTERNAL,
    )
    assert str(c.route.spec) == "deepseek:m2"
    transitions = store.read("S", types=["model_route_fallback"])
    assert [(e.payload["previous"], e.payload["next_model"]) for e in transitions] == [
        ("openai:m1", "deepseek:m2")
    ]
    assert types(store) == [
        "model_call_started",
        "model_call_failed",
        "model_route_fallback",
        "model_call_started",
        "model_call_finished",
    ]
    failed = store.read("S")[1]
    assert "super-secret-key-123" not in failed.payload["error"]


async def test_all_routes_fail(store):
    client = ModelClient(
        Router(CONFIG),
        store,
        factory=factory({"openai:m1": failing("a"), "deepseek:m2": failing("b")}),
    )
    with pytest.raises(ModelUnavailableError):
        await client.complete(
            ModelRole.WORKER,
            [Message.user("q")],
            session_id="S",
            classification=Classification.PUBLIC,
        )


async def test_classification_violation_is_audited(store):
    client = ModelClient(Router(CONFIG), store, factory=factory({}))
    with pytest.raises(PolicyViolation):
        await client.complete(
            ModelRole.WORKER,
            [Message.user("q")],
            session_id="S",
            classification=Classification.CONFIDENTIAL,
        )
    assert types(store) == ["error"]
    assert snapshot(store.db)["audit_entries"][0]["policy_violation"]


async def test_cassette_record_then_replay_ignores_ulids(store, tmp_path):
    calls = []

    def fn(messages, info):
        calls.append(1)
        return pa.ModelResponse(
            parts=[pa.ToolCallPart("python_execute", {"code": "1"}, tool_call_id="p1")]
        )

    path = tmp_path / "models.json"
    rec = ModelClient(
        Router(CONFIG),
        store,
        cassette=ModelCassette(path, CassetteMode.RECORD),
        factory=factory({"openai:m1": FunctionModel(fn)}),
    )
    run1 = [Message.user("run_id: 01M7KH0000000000000000000A please")]
    first = await rec.complete(
        ModelRole.WORKER, run1, TOOLS, session_id="S", classification=Classification.PUBLIC
    )

    replay = ModelClient(
        Router(CONFIG), store, cassette=ModelCassette(path), factory=factory({})
    )  # no model at all
    run2 = [Message.user("run_id: 01M7KH0000000000000000000B please")]  # different ULID
    second = await replay.complete(
        ModelRole.WORKER, run2, TOOLS, session_id="S", classification=Classification.PUBLIC
    )
    assert calls == [1]
    assert second.message == first.message
    (call,) = second.message.tool_calls()
    assert isinstance(call, ToolCallPart) and call.name == "python_execute"

    miss = ModelClient(Router(CONFIG), store, cassette=ModelCassette(path), factory=factory({}))
    with pytest.raises(ModelUnavailableError) as info:
        await miss.complete(
            ModelRole.WORKER,
            [Message.user("something else")],
            session_id="S",
            classification=Classification.PUBLIC,
        )
    assert "no recorded model response" in str(info.value)
    assert CassetteMissError.__name__


async def test_failure_cannot_fall_back_across_project_classification(store):
    cfg = ModelsConfig.from_dict(
        {
            "providers": {"ollama": {}, "openai": {"allowed_classifications": ["public"]}},
            "roles": {
                "planner": ["ollama:primary", "openai:public-only"],
                "worker": "ollama:primary",
            },
        }
    )
    client = ModelClient(
        Router(cfg), store, factory=factory({"ollama:primary": failing("offline")})
    )
    with pytest.raises(ModelUnavailableError):
        await client.complete(
            ModelRole.PLANNER,
            [Message.user("q")],
            session_id="S",
            classification=Classification.CONFIDENTIAL,
        )
    assert [e.payload["provider"] for e in store.read("S", types=["model_call_started"])] == [
        "ollama"
    ]
    assert not store.read("S", types=["model_route_fallback"])
