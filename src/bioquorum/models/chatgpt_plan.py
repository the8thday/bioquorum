"""Local Sign in with ChatGPT and authenticated Responses API transport."""

import asyncio
import json
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from tempfile import NamedTemporaryFile

import httpx2
import jwt
from filelock import AsyncFileLock
from pydantic_ai.providers.openai_chatgpt import (
    OpenAIChatGPTCredentials,
    OpenAIChatGPTOAuthFlow,
    OpenAIChatGPTProvider,
)

ISSUER = "https://auth.openai.com"
USAGE = "https://chatgpt.com/settings/usage"


def directory() -> Path:
    return Path.home() / ".bioquorum" / "accounts" / "chatgpt"


def _lock() -> AsyncFileLock:
    directory().mkdir(mode=0o700, parents=True, exist_ok=True)
    return AsyncFileLock(directory() / "account.lock", mode=0o600, timeout=120)


def _read(name: str) -> dict:
    path = directory() / name
    try:
        return json.loads(path.read_bytes())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise ValueError("Cannot read the local ChatGPT account record") from None


def _write(name: str, value: dict) -> None:
    with NamedTemporaryFile(dir=directory(), delete=False) as f:
        temp = Path(f.name)
        try:
            f.write(json.dumps(value).encode())
            f.flush()
            f.close()
            temp.replace(directory() / name)
        finally:
            temp.unlink(missing_ok=True)


def status() -> dict:
    account = _read("account.json")
    tokens = account.get("tokens", {})
    return {
        "connected": bool(tokens and "chatgpt.tokens.use.direct" in tokens["scope"].split()),
        "signed_in": bool(tokens),
        "account": account.get("label", "") if tokens else "",
    }


class CredentialSource:
    async def load(self) -> OpenAIChatGPTCredentials:
        tokens = _read("account.json").get("tokens")
        if not tokens:
            raise ValueError("Sign in with ChatGPT in Models & accounts")
        return OpenAIChatGPTCredentials(**tokens)

    async def save(self, credentials: OpenAIChatGPTCredentials) -> None:
        account = _read("account.json")
        account["tokens"] = asdict(credentials)
        _write("account.json", account)


async def verify_identity(
    token: str, client_id: str, nonce: str, *, proxy: str | None = None
) -> dict:
    async with httpx2.AsyncClient(proxy=proxy, trust_env=proxy is None, timeout=15) as http:
        response = await http.get(ISSUER + "/.well-known/jwks.json")
        response.raise_for_status()
    keys = jwt.PyJWKSet.from_dict(response.json())
    key = keys[jwt.get_unverified_header(token)["kid"]]
    claims = jwt.decode(
        token,
        key.key,
        algorithms=["RS256"],
        audience=client_id,
        issuer=ISSUER,
        options={"require": ["exp", "sub", "aud", "iss", "nonce"]},
    )
    if not secrets.compare_digest(claims["nonce"], nonce):
        raise ValueError("Invalid identity nonce")
    return claims


class SignIn:
    def __init__(self, host: str, *, proxy: str | None = None):
        self.redirect = f"http://{host}/auth/callback"
        self.proxy = proxy
        self.flow: OpenAIChatGPTOAuthFlow | None = None
        self.expires = 0.0

    async def start(self) -> dict:
        async with _lock():
            host = _read("host.json")
            if not host:
                host = {"id": uuid.uuid4().urn}
                _write("host.json", host)
            account = _read("account.json")
            self.flow = OpenAIChatGPTOAuthFlow(
                app_name="BioQuorum",
                host_id=host["id"],
                client_id=account.get("client_id"),
                redirect_uri=self.redirect,
            )
            self.expires = time.monotonic() + 600
            return {"url": self.flow.authorization_url(extra_params={"prompt": "consent"})}

    async def finish(self, params: dict[str, str]) -> bool:
        flow = self.flow
        if (
            not flow
            or self.expires < time.monotonic()
            or not secrets.compare_digest(params.get("state", "").encode(), flow.state.encode())
        ):
            raise ValueError("This sign-in has expired or was replaced. Start again.")
        self.flow = None
        if params.get("error") or not params.get("code"):
            raise ValueError("Sign-in was cancelled. Return to the workbench.")
        try:
            async with _lock(), asyncio.timeout(45):
                account = _read("account.json")
                if account.get("client_id") != flow.client_id:
                    raise ValueError("Account changed during sign-in")
                credentials = await flow.exchange_code(
                    params["code"], client_id=params.get("client_id")
                )
                claims = await verify_identity(
                    credentials.id_token or "", credentials.client_id, flow.nonce, proxy=self.proxy
                )
                if account and claims["sub"] != account["sub"]:
                    raise ValueError("Account identity changed")
                _write(
                    "account.json",
                    {
                        "client_id": credentials.client_id,
                        "sub": claims["sub"],
                        "label": claims.get("email") or "ChatGPT account",
                        "tokens": asdict(credentials),
                    },
                )
                return credentials.plan_usage_granted
        except Exception:
            raise ValueError(
                "ChatGPT authorization could not be verified. Return to the workbench and sign in again."
            ) from None


async def disconnect(*, proxy: str | None = None) -> dict:
    async with _lock():
        account = _read("account.json")
        tokens = account.pop("tokens", None)
        confirmed = True
        if tokens:
            confirmed = False
            async with httpx2.AsyncClient(proxy=proxy, trust_env=proxy is None, timeout=8) as http:
                for attempt in range(2):
                    try:
                        response = await http.post(
                            ISSUER + "/api/accounts/oauth/revoke",
                            data={
                                "client_id": account["client_id"],
                                "token": tokens["refresh_token"],
                                "token_type_hint": "refresh_token",
                            },
                        )
                        confirmed = response.status_code == 200
                        if response.status_code < 500:
                            break
                    except httpx2.HTTPError:
                        pass
                    if not attempt:
                        await asyncio.sleep(0.3)
            _write("account.json", account)
        return {"revoked": confirmed}


@asynccontextmanager
async def transport(proxy: str | None = None):
    # The SDK owns token refresh. Serialize instances that share a rotating refresh token.
    async with _lock(), httpx2.AsyncClient(proxy=proxy, trust_env=False, timeout=120) as http:
        provider = OpenAIChatGPTProvider(credential_source=CredentialSource(), http_client=http)
        yield provider, http


async def catalog(proxy: str | None = None) -> list[dict[str, str]]:
    try:
        async with transport(proxy) as (_, http):
            response = await http.get("https://api.openai.com/v1/models")
            response.raise_for_status()
            return [
                {"id": m["slug"], "name": m.get("display_name") or m["slug"]}
                for m in response.json()["models"]
                if m.get("visibility") == "list"
            ]
    except Exception:
        raise ValueError(
            "Cannot load the account model catalog. Check sign-in and plan access."
        ) from None
