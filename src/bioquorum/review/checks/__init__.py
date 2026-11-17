"""Review checks. Deterministic ones never call a model (ARCHITECTURE.md §6.5)."""

from bioquorum.review.checks.base import Check, Finding
from bioquorum.review.checks.citations import CitationResolves
from bioquorum.review.checks.execution import ExecutionEvidence
from bioquorum.review.checks.plan import PlanComplete
from bioquorum.review.checks.values import ValueMatch


def deterministic_checks() -> list[Check]:
    return [ExecutionEvidence(), ValueMatch(), CitationResolves(), PlanComplete()]


__all__ = [
    "Check",
    "CitationResolves",
    "ExecutionEvidence",
    "Finding",
    "PlanComplete",
    "ValueMatch",
    "deterministic_checks",
]
