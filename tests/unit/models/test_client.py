import pytest
from pydantic_ai import messages as pa
from pydantic_ai.models.function import FunctionModel

from bioquorum.core.enums import Classification, ModelRole
from bioquorum.core.messages import Message, ToolCallPart
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
    assert types(store) == [
        "model_call_started",
        "model_call_failed",
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
