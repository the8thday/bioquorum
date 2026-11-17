"""The Check interface (ARCHITECTURE.md §6.5, §7: review checks are an extension point)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from bioquorum.core.enums import Severity
from bioquorum.review.evidence import ClaimInput, Evidence


@dataclass(frozen=True)
class Finding:
    claim_index: int
    check: str
    severity: Severity
    evidence: str  # human-readable reason, quoting what was (not) found


@runtime_checkable
class Check(Protocol):
    name: str
    needs_model: bool

    async def run(self, claims: Sequence[ClaimInput], evidence: Evidence) -> list[Finding]: ...
