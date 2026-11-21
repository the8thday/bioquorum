"""Review checks. Deterministic ones never call a model (ARCHITECTURE.md §6.5)."""

from bioquorum.review.checks.absence import NegativeSearchScope
from bioquorum.review.checks.base import Check, Finding
from bioquorum.review.checks.citations import CitationResolves
from bioquorum.review.checks.execution import ExecutionEvidence
from bioquorum.review.checks.plan import PlanComplete
from bioquorum.review.checks.values import ValueMatch
from bioquorum.tools.context import ToolContext


def deterministic_checks(
    online_citations: ToolContext | None = None, *, causation_id: str | None = None
) -> list[Check]:
    return [
        ExecutionEvidence(),
        ValueMatch(),
        CitationResolves(online_citations, causation_id=causation_id),
        PlanComplete(),
        NegativeSearchScope(),
    ]


__all__ = [
    "Check",
    "CitationResolves",
    "ExecutionEvidence",
    "Finding",
    "PlanComplete",
    "ValueMatch",
    "deterministic_checks",
]
