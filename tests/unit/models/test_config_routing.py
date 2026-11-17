import pytest

from bioquorum.core.enums import Classification, ModelRole
from bioquorum.models import ConfigError, ModelsConfig, ModelSpec, ProviderConfig, Router
from bioquorum.policy import PolicyViolation

C = Classification
BASE = {
    "providers": {
        "openai": {},
        "deepseek": {},
        "lab": {
            "kind": "openai-compatible",
            "base_url": "http://localhost:8000/v1",
            "family": "qwen",
        },
    },
    "roles": {"worker": ["openai:gpt-6.1-sol", "deepseek:deepseek-v4-pro"]},
}


def cfg(**roles):
    data = {**BASE, "roles": {**BASE["roles"], **roles}}
    return ModelsConfig.from_dict(data)


def test_provider_defaults():
    ds = ProviderConfig.from_dict("deepseek", {})
    assert (
        ds.base_url == "https://api.deepseek.com" and ds.family == "deepseek" and ds.api == "chat"
    )
    assert ds.api_key == "env:DEEPSEEK_API_KEY"
    assert ds.allowed_classifications == (C.PUBLIC, C.INTERNAL)  # remote: never confidential
    oa = ProviderConfig.from_dict("openai", {})
    assert oa.api == "responses" and oa.api_key == "env:OPENAI_API_KEY"
    lab = ProviderConfig.from_dict("lab", BASE["providers"]["lab"])
    assert lab.is_local and C.CONFIDENTIAL in lab.allowed_classifications


@pytest.mark.parametrize(
    "data",
    [
        {"kind": "nope"},
        {"kind": "openai-compatible"},  # no base_url
        {"api_key": "sk-literal-key-in-config"},
    ],
)
def test_bad_provider_config(data):
    with pytest.raises(ConfigError):
        ProviderConfig.from_dict("x", data)


def test_spec_parse():
    assert str(ModelSpec.parse("deepseek:deepseek-v4-pro")) == "deepseek:deepseek-v4-pro"
    with pytest.raises(ConfigError):
        ModelSpec.parse("no-colon")
    with pytest.raises(ConfigError):
        ModelsConfig.from_dict({"providers": {}, "roles": {"worker": "ghost:m"}})


def test_chain_and_classification_filter():
    r = Router(cfg())
    assert [str(x.spec) for x in r.chain(ModelRole.WORKER, C.INTERNAL)] == [
        "openai:gpt-6.1-sol",
        "deepseek:deepseek-v4-pro",
    ]
    with pytest.raises(PolicyViolation):
        r.resolve(ModelRole.WORKER, C.CONFIDENTIAL)


def test_confidential_routes_to_local_only():
    r = Router(cfg(worker=["openai:gpt-6.1-sol", "lab:qwen3"]))
    assert str(r.resolve(ModelRole.WORKER, C.CONFIDENTIAL).spec) == "lab:qwen3"
    assert str(r.resolve(ModelRole.WORKER, C.PUBLIC).spec) == "openai:gpt-6.1-sol"


def test_reviewer_falls_back_to_worker_and_same_family_is_fine():
    r = Router(cfg())
    assert str(r.resolve(ModelRole.REVIEWER, C.PUBLIC).spec) == "openai:gpt-6.1-sol"
    r2 = Router(cfg(reviewer="openai:gpt-5.5"))  # same family as worker: allowed (ADR-013)
    assert str(r2.resolve(ModelRole.REVIEWER, C.PUBLIC).spec) == "openai:gpt-5.5"


def test_auto_reviewer_by_score_and_family_preference():
    data = {
        **BASE,
        "roles": {**BASE["roles"], "reviewer": "auto"},
        "models": {
            "openai:gpt-6.1-sol": {"review_score": 95.0},
            "deepseek:deepseek-v4-pro": {"review_score": 94.4},
        },
    }
    assert str(Router(ModelsConfig.from_dict(data)).resolve(ModelRole.REVIEWER, C.PUBLIC).spec) == (
        "openai:gpt-6.1-sol"
    )
    data["roles"]["prefer_different_family"] = True
    assert str(Router(ModelsConfig.from_dict(data)).resolve(ModelRole.REVIEWER, C.PUBLIC).spec) == (
        "deepseek:deepseek-v4-pro"
    )
