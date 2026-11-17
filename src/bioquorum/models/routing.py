"""Pick a model for a role under the project's classification (ARCHITECTURE.md §6.1, ADR-013).

Order is fixed: filter providers by classification first, then walk the role's chain.
A role with nothing allowed raises PolicyViolation; there is no silent downgrade.
"""

from __future__ import annotations

from dataclasses import dataclass

from bioquorum.core.enums import Classification, ModelRole
from bioquorum.models.config import ModelsConfig, ModelSpec, ProviderConfig
from bioquorum.policy.classification import PolicyViolation


@dataclass(frozen=True)
class Route:
    role: ModelRole
    spec: ModelSpec
    provider: ProviderConfig

    @property
    def family(self) -> str:
        return self.provider.family


class Router:
    def __init__(self, config: ModelsConfig) -> None:
        self.config = config

    def allowed(self, spec: ModelSpec, classification: Classification) -> bool:
        return classification in self.config.providers[spec.provider].allowed_classifications

    def chain(self, role: ModelRole, classification: Classification) -> list[Route]:
        """All usable routes for ``role`` in preference order (fallbacks included)."""
        cfg = self.config
        if role is ModelRole.REVIEWER and cfg.reviewer_auto:
            candidates = sorted(cfg.review_scores, key=lambda s: -cfg.review_scores[s])
            specs = [
                ModelSpec.parse(s)
                for s in candidates
                if ModelSpec.parse(s).provider in cfg.providers
            ]
            if cfg.prefer_different_family:
                worker = self.chain(ModelRole.WORKER, classification)
                wf = worker[0].family if worker else None
                specs.sort(key=lambda s: cfg.providers[s.provider].family == wf)  # stable
        else:
            specs = cfg.roles.get(role) or []
        if not specs and role is not ModelRole.WORKER:
            # planner / summarizer / reviewer fall back to the worker (ADR-013)
            return [
                Route(role, r.spec, r.provider)
                for r in self.chain(ModelRole.WORKER, classification)
            ]
        return [
            Route(role, s, cfg.providers[s.provider])
            for s in specs
            if self.allowed(s, classification)
        ]

    def resolve(self, role: ModelRole, classification: Classification) -> Route:
        routes = self.chain(role, classification)
        if not routes:
            configured = (
                self.config.roles.get(role) or self.config.roles.get(ModelRole.WORKER) or []
            )
            raise PolicyViolation(
                f"no model for role '{role}' is allowed for '{classification}' data "
                f"(configured: {', '.join(map(str, configured)) or 'none'})"
            )
        return routes[0]
