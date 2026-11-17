"""Call a model for a role and record the call as events (ARCHITECTURE.md §5.3, §6.1).

``ModelClient.complete`` is what the agent and review layers use. It routes under the
project's classification, writes ``model_call_started`` / ``finished`` / ``failed`` and
walks the fallback chain on failure. It does not write ``assistant_message``: deciding
what the conversation contains is the caller's job.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai.models import ModelRequestParameters

from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, Classification, ModelRole
from bioquorum.core.ids import new_id
from bioquorum.core.messages import Message
from bioquorum.models.cassette import CassetteMode, ModelCassette, request_key
from bioquorum.models.convert import from_provider, to_provider, tool_definitions
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
    ) -> None:
        self.router = router
        self.store = store
        self.cassette = cassette
        self.factory = factory
        self.prices = prices or {}  # spec -> (USD per 1M input, per 1M output)

    def _cost(self, spec: str, usage: dict[str, int]) -> float:
        p_in, p_out = self.prices.get(spec, (0.0, 0.0))
        return (usage["input_tokens"] * p_in + usage["output_tokens"] * p_out) / 1_000_000

    async def _call(
        self, route: Route, messages: Sequence[Message], tools: Sequence[dict[str, Any]]
    ) -> tuple[Message, dict[str, int]]:
        spec = str(route.spec)
        key = request_key(spec, messages, tools)
        if self.cassette is not None and self.cassette.mode is CassetteMode.REPLAY:
            return self.cassette.lookup(key)
        model = self.factory(route)
        params = ModelRequestParameters(
            function_tools=tool_definitions(tools), allow_text_output=True
        )
        response = await model.request(to_provider(messages, family=route.family), None, params)
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
                message, usage = await self._call(route, messages, tools)
            except Exception as exc:  # try the next route; never leak secrets
                error = redact(f"{type(exc).__name__}: {exc}")[:2000]
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
            self.store.append(
                ev.ModelCallFinished(
                    call_id=call_id,
                    provider=route.spec.provider,
                    model=route.spec.model,
                    role=role,
                    classification=classification,
                    cost_usd=self._cost(str(route.spec), usage),
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
            return Completion(message, route, call_id, usage, latency)
        raise ModelUnavailableError("; ".join(errors) or f"no route for {role}")
