"""Build a Pydantic AI model for a route. The only place provider SDK clients are created."""

from __future__ import annotations

from collections.abc import Callable

import httpx2
from openai import APIError, APIStatusError
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.providers.openai import OpenAIProvider

from bioquorum.models.config import ProviderConfig
from bioquorum.models.routing import Route
from bioquorum.policy.secrets import SecretError, resolve_secret

ModelFactory = Callable[[Route], Model]


def build_model(route: Route, *, http_client: httpx2.AsyncClient | None = None) -> Model:
    p = route.provider
    if p.kind == "chatgpt":
        raise ValueError("Use the managed ChatGPT plan transport")
    key = resolve_secret(p.api_key) or "none"  # local servers often ignore the key
    provider = (
        OpenAIProvider(base_url=p.base_url, api_key=key, http_client=http_client)
        if http_client is not None
        else OpenAIProvider(base_url=p.base_url, api_key=key)
    )
    if p.api == "responses":
        return OpenAIResponsesModel(route.spec.model, provider=provider)
    profile = DeepSeekProvider.model_profile(route.spec.model) if p.kind == "deepseek" else None
    return OpenAIChatModel(route.spec.model, provider=provider, profile=profile)


async def openai_model_ids(provider: ProviderConfig, proxy: str | None) -> list[str]:
    """List visible model IDs using the same connection and credentials as inference."""
    try:
        key = resolve_secret(provider.api_key) or "none"
    except SecretError as exc:
        raise ValueError(str(exc)) from None
    try:
        async with httpx2.AsyncClient(proxy=proxy, trust_env=proxy is None, timeout=12) as http:
            client = OpenAIProvider(
                base_url=provider.base_url, api_key=key, http_client=http
            ).client
            models = await client.with_options(max_retries=0, timeout=12).models.list()
            return sorted({model.id async for model in models if isinstance(model.id, str)})
    except APIStatusError as exc:
        if exc.status_code in (401, 403):
            raise ValueError(
                "Model list access denied. Check the API key and its permissions."
            ) from None
        raise ValueError(
            f"The model service returned HTTP {exc.status_code}. Try refreshing."
        ) from None
    except (APIError, ValueError, TypeError, AttributeError, httpx2.HTTPError):
        raise ValueError(
            "Could not load the model list. Check your connection or enter a model ID."
        ) from None
