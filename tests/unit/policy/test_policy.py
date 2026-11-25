import pytest

from bioquorum.core.enums import Classification, PermissionClass, PermissionScope
from bioquorum.policy import Decision, PermissionPolicy, PermissionRequest, PolicyViolation
from bioquorum.policy import classification as cls
from bioquorum.policy.permissions import covers
from bioquorum.policy.secrets import redact, register_secret

pytestmark = pytest.mark.anyio

C = Classification


@pytest.mark.parametrize(
    ("project", "clearance", "ok"),
    [
        (C.CONFIDENTIAL, None, True),
        (C.PUBLIC, C.PUBLIC, True),
        (C.INTERNAL, C.PUBLIC, False),
        (C.INTERNAL, C.INTERNAL, True),
        (C.CONFIDENTIAL, C.INTERNAL, False),
        (C.PUBLIC, C.CONFIDENTIAL, True),
    ],
)
def test_classification_matrix(project, clearance, ok):
    assert cls.allows(project, clearance) is ok
    if not ok:
        with pytest.raises(PolicyViolation):
            cls.check(project, clearance, what="t")


@pytest.mark.parametrize(
    ("grant", "target", "ok"),
    [
        ("*", "anything", True),
        ("/data", "/data/x/y.csv", True),
        ("/data", "/data", True),
        ("/data", "/data2/x", False),
        ("/data", "/data/../etc/passwd", False),
        ("python", "python", True),
        ("python", "python/x", False),
    ],
)
def test_covers(grant, target, ok):
    assert covers(grant, target) is ok


def req(target="/d/x", perm=PermissionClass.READ_FS, session="S", project="P"):
    return PermissionRequest(permission=perm, target=target, session_id=session, project_id=project)


async def test_no_asker_denies_by_default():
    d = await PermissionPolicy().decide(req())
    assert not d.granted and not d.asked


async def test_asker_decision_remembered_per_scope():
    calls = []

    async def ask(r):
        calls.append(r)
        return Decision(granted=True, scope=PermissionScope.SESSION)

    p = PermissionPolicy(ask)
    assert (await p.decide(req())).asked
    again = await p.decide(req())
    assert again.granted and not again.asked
    other_session = await p.decide(req(session="S2"))
    assert other_session.asked
    assert len(calls) == 2


async def test_once_grants_are_not_remembered():
    async def ask(r):
        return Decision(granted=True, scope=PermissionScope.ONCE)

    p = PermissionPolicy(ask)
    await p.decide(req())
    assert p.lookup(req()) is None


def test_project_grant_and_revoke():
    p = PermissionPolicy()
    p.grant(PermissionClass.READ_FS, "/d", PermissionScope.PROJECT, project_id="P")
    assert p.lookup(req(session="any")) is not None
    assert p.lookup(req(project="Q")) is None
    assert p.revoke(PermissionClass.READ_FS, "/d") == 1
    assert p.lookup(req()) is None


def test_allow_all_grants_everything():
    p = PermissionPolicy.allow_all()
    for perm in PermissionClass:
        assert p.lookup(req(perm=perm, target="x")) is not None


def test_redact_known_and_patterned_secrets():
    register_secret("my-very-secret-value")
    text = (
        "auth failed for my-very-secret-value; key sk-abcdefghijklmnopqrstu; "
        "api_key=zzz123; Authorization: Bearer abcdefghijklmnop"
    )
    out = redact(text)
    for leaked in (
        "my-very-secret-value",
        "sk-abcdefghijklmnopqrstu",
        "zzz123",
        "abcdefghijklmnop",
    ):
        assert leaked not in out
    assert "auth failed" in out


def test_redact_quoted_json_and_provider_tokens():
    jwt_token = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlMTIzNDU2"
    text = (
        '{"api_key": "json-secret-123", "refresh_token": "refresh-xyz-999", '
        '"input_tokens": 42, "token": ""} '
        "password='quoted-pass-456' "
        "ghp_1234567890abcdefghij1234 "
        "hf_1234567890abcdefghij1234 "
        "AKIAIOSFODNN7EXAMPLE "
        f"jwt={jwt_token}"
    )
    out = redact(text)
    for leaked in (
        "json-secret-123",
        "refresh-xyz-999",
        "quoted-pass-456",
        "ghp_1234567890abcdefghij1234",
        "hf_1234567890abcdefghij1234",
        "AKIAIOSFODNN7EXAMPLE",
        jwt_token,
    ):
        assert leaked not in out
    assert '"input_tokens": 42' in out
    assert '"token": ""' in out


def test_resolve_secret(monkeypatch):
    from bioquorum.policy.secrets import SecretError, resolve_secret

    monkeypatch.setenv("BQ_TEST_KEY", "value-of-the-test-key")
    assert resolve_secret("env:BQ_TEST_KEY") == "value-of-the-test-key"
    assert redact("boom value-of-the-test-key") == "boom ***"
    assert resolve_secret("none") is None
    monkeypatch.delenv("BQ_TEST_KEY")
    with pytest.raises(SecretError) as info:
        resolve_secret("env:BQ_TEST_KEY")
    assert "BQ_TEST_KEY" in str(info.value)
    with pytest.raises(SecretError):
        resolve_secret("sk-literal")
