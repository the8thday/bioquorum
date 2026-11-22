from urllib.parse import parse_qs, urlparse

import pytest

from bioquorum.models import chatgpt_plan as plan


@pytest.mark.anyio
async def test_signin_uses_stable_host_pkce_and_single_use_state(tmp_path, monkeypatch):
    monkeypatch.setattr(plan, "directory", lambda: tmp_path / "credentials")
    login = plan.SignIn("127.0.0.1:8181")
    first = parse_qs(urlparse((await login.start())["url"]).query)
    assert first["redirect_uri"] == ["http://127.0.0.1:8181/auth/callback"]
    assert first["client_id"] == ["dynamic_agent_client"]
    with pytest.raises(ValueError):
        await login.finish({"state": "invalid", "code": "sensitive"})
    with pytest.raises(ValueError):
        await login.finish({"state": first["state"][0], "error": "access_denied"})
    with pytest.raises(ValueError):
        await login.finish({"state": first["state"][0], "code": "sensitive"})
    other = parse_qs(urlparse((await login.start())["url"]).query)
    assert (
        other["state"] != first["state"]
        and other["ext_agent_host_id"] == first["ext_agent_host_id"]
    )
    assert plan.status()["connected"] is False


@pytest.mark.anyio
async def test_authorize_catalog_stream_and_disconnect(tmp_path, monkeypatch):
    import json
    import time

    import httpx2
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from jwt.algorithms import RSAAlgorithm

    from bioquorum.application import workspace
    from bioquorum.application.controls import ResearchControls
    from bioquorum.core.enums import Classification, ModelRole
    from bioquorum.core.messages import Message
    from bioquorum.models import ModelClient, Router

    monkeypatch.setattr(plan, "directory", lambda: tmp_path / "private")
    monkeypatch.setenv("OPENAI_API_KEY", "different-api-key")
    login = plan.SignIn("127.0.0.1:8181")
    params = parse_qs(urlparse((await login.start())["url"]).query)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    claims = {
        "iss": plan.ISSUER,
        "sub": "local-fixture",
        "aud": "registered",
        "exp": int(time.time()) + 3600,
        "nonce": params["nonce"][0],
    }
    identity = jwt.encode(claims, key, algorithm="RS256", headers={"kid": "one"})
    bearer = jwt.encode({"exp": claims["exp"]}, key, algorithm="RS256")
    jwk = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
    paths = []

    async def handler(self, request):
        path = request.url.path
        paths.append(path)
        if path.endswith("/token"):
            assert b"code_verifier=" in await request.aread()
            return httpx2.Response(
                200,
                json={
                    "id_token": identity,
                    "access_token": bearer,
                    "refresh_token": "renew-fixture",
                    "scope": "chatgpt.tokens.use.direct",
                },
            )
        if path.endswith("jwks.json"):
            return httpx2.Response(200, json={"keys": [{**jwk, "kid": "one"}]})
        if path.endswith("/revoke"):
            assert b"renew-fixture" in await request.aread()
            return httpx2.Response(200)
        assert request.headers["authorization"] == "Bearer " + bearer
        if path.endswith("/models"):
            return httpx2.Response(
                200,
                json={
                    "models": [
                        {"visibility": "list", "slug": "listed", "display_name": "Available"},
                        {"visibility": "hidden", "slug": "other"},
                    ]
                },
            )
        assert path == "/v1/responses"
        body = json.loads(await request.aread())
        assert body["store"] is False and body["stream"] is True and body["model"] == "listed"
        assert body["tools"][0]["name"] == "lookup"
        item = {
            "type": "function_call",
            "id": "fc_fixture",
            "call_id": "call_fixture",
            "name": "lookup",
            "arguments": "{}",
            "status": "completed",
        }
        response = {
            "id": "resp_fixture",
            "object": "response",
            "created_at": 0,
            "model": "listed",
            "status": "completed",
            "output": [item],
            "usage": {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
        }

        async def stream():
            events = [
                {
                    "type": "response.created",
                    "response": {**response, "output": [], "status": "in_progress"},
                },
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {**item, "arguments": ""},
                },
                {
                    "type": "response.function_call_arguments.delta",
                    "item_id": item["id"],
                    "output_index": 0,
                    "delta": "{}",
                },
                {"type": "response.output_item.done", "output_index": 0, "item": item},
                {"type": "response.completed", "response": response},
            ]
            for seq, event in enumerate(events):
                yield ("data: " + json.dumps({**event, "sequence_number": seq}) + "\n\n").encode()

        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=stream())

    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", handler)
    assert await login.finish(
        {"state": params["state"][0], "code": "fake-code", "client_id": "registered"}
    )
    assert (plan.directory() / "account.json").stat().st_mode & 0o777 == 0o600
    assert await plan.catalog() == [{"id": "listed", "name": "Available"}]
    ws = workspace.init_project(tmp_path / "research")
    ws.config = {"providers": {"chatgpt": {}}, "roles": {"worker": "chatgpt:listed"}}
    c = ResearchControls(ws)
    client = ModelClient(Router(ws.models_config()), c.store)
    result = await client.complete(
        ModelRole.WORKER,
        [Message.user("Use the lookup tool.")],
        [
            {
                "name": "lookup",
                "description": "Look up a record",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
        session_id=c.new_session("Offline"),
        classification=Classification.INTERNAL,
    )
    assert result.message.tool_calls()[0].name == "lookup"
    assert result.usage["input_tokens"] == 12
    assert bearer not in str([e.payload for e in c.store.iter_all()])
    assert (await plan.disconnect())["revoked"] and not plan.status()["connected"]
    assert paths.count("/v1/responses") == 1
