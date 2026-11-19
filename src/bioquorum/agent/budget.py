"""Exploration limits checked between model requests and tool calls."""

from dataclasses import dataclass, field
from math import isfinite
from time import monotonic

from bioquorum.models.client import Completion


@dataclass(frozen=True)
class Budget:
    tokens: int | None = None
    usd: float | None = None
    seconds: float | None = None

    def __post_init__(self) -> None:
        for name, limit in (("tokens", self.tokens), ("usd", self.usd), ("seconds", self.seconds)):
            if limit is None:
                continue
            allowed_types = (int,) if name == "tokens" else (int, float)
            if type(limit) not in allowed_types or not isfinite(limit) or limit <= 0:
                raise ValueError(
                    f"budget {name} must be a positive finite {allowed_types[0].__name__}"
                )

    @property
    def enabled(self) -> bool:
        return any(limit is not None for limit in (self.tokens, self.usd, self.seconds))


@dataclass
class BudgetMeter:
    limits: Budget
    started: float = field(default_factory=lambda: monotonic())
    tokens: int = 0
    usd: float = 0.0
    missing_usage: bool = False

    def record(self, completion: Completion) -> None:
        used = completion.usage.get("input_tokens", 0) + completion.usage.get("output_tokens", 0)
        self.tokens += used
        self.usd += completion.cost_usd or 0.0
        self.missing_usage |= used == 0

    def stop_reason(self) -> str | None:
        if self.limits.seconds is not None and monotonic() - self.started >= self.limits.seconds:
            return "time budget"
        if self.limits.tokens is not None and self.tokens >= self.limits.tokens:
            return "token budget"
        if self.limits.usd is not None and self.usd >= self.limits.usd:
            return "cost budget"
        if self.missing_usage and (self.limits.tokens is not None or self.limits.usd is not None):
            return "model usage unavailable"
        return None
