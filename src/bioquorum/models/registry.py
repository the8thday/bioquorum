"""Build a Pydantic AI model for a route. The only place provider SDK clients are created."""

from __future__ import annotations

from collections.abc import Callable

from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider

from bioquorum.models.routing import Route
from bioquorum.policy.secrets import resolve_secret

ModelFactory = Callable[[Route], Model]


def build_model(route: Route) -> Model:
    p = route.provider
    key = resolve_secret(p.api_key) or "none"  # local servers often ignore the key
    provider = OpenAIProvider(base_url=p.base_url, api_key=key)
    if p.api == "responses":
        return OpenAIResponsesModel(route.spec.model, provider=provider)
    return OpenAIChatModel(route.spec.model, provider=provider)
