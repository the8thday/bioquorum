"""Call a model for a role and record the call as events (ARCHITECTURE.md §5.3, §6.1).

``ModelClient.complete`` is what the agent and review layers use. It routes under the
project's classification, writes ``model_call_started`` / ``finished`` / ``failed`` and
walks the fallback chain on failure. It does not write ``assistant_message``: deciding
what the conversation contains is the caller's job.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

import httpx2
from pydantic_ai import messages as pa
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.settings import ModelSettings

from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, Classification, ModelRole
from bioquorum.core.ids import new_id
from bioquorum.core.messages import Message
from bioquorum.models.cassette import CassetteMode, ModelCassette, request_key
from bioquorum.models.convert import ImageLoader, from_provider, to_provider, tool_definitions
from bioquorum.models.registry import ModelFactory, build_model
from bioquorum.models.routing import Route, Router
from bioquorum.policy.classification import PolicyViolation
from bioquorum.policy.secrets import redact
from bioquorum.storage.events import EventStore

_ACTOR = {ModelRole.REVIEWER: Actor.REVIEWER}


class ModelUnavailableError(RuntimeError):
    """Every route in the role's chain failed."""


@dataclass
class Completion:
    message: Message
    route: Route
    model_call_id: str
    usage: dict[str, int] = field(default_factory=dict)
    latency_ms: int = 0
    cost_usd: float | None = None


OnDelta = Callable[[str], None]


def _usage(u: Any) -> dict[str, int]:
    return {
        "input_tokens": int(getattr(u, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(u, "output_tokens", 0) or 0),
        "cache_read_tokens": int(getattr(u, "cache_read_tokens", 0) or 0),
        "cache_write_tokens": int(getattr(u, "cache_write_tokens", 0) or 0),
    }


class ModelClient:
    def __init__(
        self,
        router: Router,
        store: EventStore,
        *,
        cassette: ModelCassette | None = None,
        factory: ModelFactory = build_model,
        prices: dict[str, tuple[float, float]] | None = None,
        image_loader: ImageLoader | None = None,
        outbound_proxy: str | None = None,
        max_output_tokens: int | None = None,
    ) -> None:
        self.router = router
        self.store = store
        self.cassette = cassette
        self.factory = factory
        self.prices = router.config.prices if prices is None else prices
        self.image_loader = image_loader
        self.outbound_proxy = outbound_proxy
        self.max_output_tokens = max_output_tokens

    def _images_for(self, route: Route) -> ImageLoader | None:
        return self.image_loader if str(route.spec) in self.router.config.vision_models else None

    def _cost(self, spec: str, usage: dict[str, int]) -> float | None:
        if spec not in self.prices:
            return None
        p_in, p_out = self.prices[spec]
        if (p_in or p_out) and not (usage["input_tokens"] or usage["output_tokens"]):
            return None
        return (usage["input_tokens"] * p_in + usage["output_tokens"] * p_out) / 1_000_000

    async def _call(
        self,
        route: Route,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]],
        on_delta: OnDelta | None = None,
    ) -> tuple[Message, dict[str, int]]:
        spec = str(route.spec)
        image_loader = self._images_for(route)
        variant = spec + "#vision" if image_loader is not None else spec
        if self.max_output_tokens is not None:
            variant += f"#max_output_tokens={self.max_output_tokens}"
        key = request_key(variant, messages, tools)
        if self.cassette is not None and self.cassette.mode is CassetteMode.REPLAY:
            message, usage = self.cassette.lookup(key)
            if on_delta is not None and message.text():
                on_delta(message.text())
            return message, usage
        params = ModelRequestParameters(
            function_tools=tool_definitions(tools), allow_text_output=True
        )
        history = to_provider(messages, family=route.family, image_loader=image_loader)
        settings: ModelSettings | None = (
            {"max_tokens": self.max_output_tokens} if self.max_output_tokens is not None else None
        )
        async with AsyncExitStack() as stack:
            if self.outbound_proxy is not None and not route.provider.is_local:
                http = await stack.enter_async_context(
                    httpx2.AsyncClient(proxy=self.outbound_proxy, trust_env=False)
                )
                model = build_model(route, http_client=http)
            else:
                model = self.factory(route)
            if on_delta is None:
                response = await model.request(history, settings, params)
            else:
                async with model.request_stream(history, settings, params) as stream:
                    async for event in stream:
                        if isinstance(event, pa.PartStartEvent) and isinstance(
                            event.part, pa.TextPart
                        ):
                            on_delta(event.part.content)
                        elif isinstance(event, pa.PartDeltaEvent) and isinstance(
                            event.delta, pa.TextPartDelta
                        ):
                            on_delta(event.delta.content_delta)
                    response = stream.get()
        message, usage = from_provider(response, family=route.family), _usage(response.usage)
        if self.cassette is not None and self.cassette.mode is CassetteMode.RECORD:
            self.cassette.store(key, spec, message, usage)
        return message, usage

    async def complete(
        self,
        role: ModelRole,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]] = (),
        *,
        session_id: str,
        classification: Classification,
        causation_id: str | None = None,
        on_delta: OnDelta | None = None,
    ) -> Completion:
        actor = _ACTOR.get(role, Actor.AGENT)
        try:
            self.router.resolve(role, classification)  # raises if nothing is allowed
        except PolicyViolation as exc:
            self.store.append(
                ev.ErrorOccurred(
                    where=f"models.route:{role}", error=str(exc), policy_violation=True
                ),
                session_id=session_id,
                actor=Actor.SYSTEM,
                causation_id=causation_id,
            )
            raise

        errors: list[str] = []
        for route in self.router.chain(role, classification):
            call_id = new_id()
            started = self.store.append(
                ev.ModelCallStarted(
                    call_id=call_id,
                    provider=route.spec.provider,
                    model=route.spec.model,
                    role=role,
                    classification=classification,
                ),
                session_id=session_id,
                actor=actor,
                causation_id=causation_id,
            )
            t0 = time.monotonic()
            try:
                if on_delta is None:
                    message, usage = await self._call(route, messages, tools)
                else:
                    message, usage = await self._call(route, messages, tools, on_delta=on_delta)
            except asyncio.CancelledError:
                self.store.append(
                    ev.ModelCallFailed(
                        call_id=call_id,
                        provider=route.spec.provider,
                        model=route.spec.model,
                        role=role,
                        error="Research stopped",
                    ),
                    session_id=session_id,
                    actor=actor,
                    causation_id=started.id,
                )
                raise
            except Exception as exc:  # try the next route; never leak secrets
                error = (
                    f"{type(exc).__name__}: Vision-enabled model request failed"
                    if self._images_for(route) is not None
                    else redact(f"{type(exc).__name__}: {exc}")[:2000]
                )
                errors.append(f"{route.spec}: {error}")
                self.store.append(
                    ev.ModelCallFailed(
                        call_id=call_id,
                        provider=route.spec.provider,
                        model=route.spec.model,
                        role=role,
                        error=error,
                    ),
                    session_id=session_id,
                    actor=actor,
                    causation_id=started.id,
                )
                continue
            latency = int((time.monotonic() - t0) * 1000)
            cost = self._cost(str(route.spec), usage)
            self.store.append(
                ev.ModelCallFinished(
                    call_id=call_id,
                    provider=route.spec.provider,
                    model=route.spec.model,
                    role=role,
                    classification=classification,
                    cost_usd=cost or 0.0,
                    cost_known=cost is not None,
                    latency_ms=latency,
                    input_tokens=usage["input_tokens"],
                    output_tokens=usage["output_tokens"],
                    cache_read_tokens=usage["cache_read_tokens"],
                    cache_write_tokens=usage["cache_write_tokens"],
                ),
                session_id=session_id,
                actor=actor,
                causation_id=started.id,
            )
            return Completion(message, route, call_id, usage, latency, cost)
        raise ModelUnavailableError("; ".join(errors) or f"no route for {role}")
