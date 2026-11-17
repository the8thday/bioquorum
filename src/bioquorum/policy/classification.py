"""Data classification checks (ARCHITECTURE.md §6.1, §6.4, §6.7; principle 10).

A tool's ``classification_floor`` names the most sensitive project classification whose
data may flow to the tool's destination. ``None`` means data never leaves the machine.
Violations are rejected, never silently downgraded.
"""

from __future__ import annotations

from bioquorum.core.enums import CLASSIFICATION_ORDER, Classification


class PolicyViolation(Exception):  # noqa: N818  (name fixed by ARCHITECTURE.md §6.1)
    """A call would send data somewhere its classification forbids."""


def allows(project: Classification, clearance: Classification | None) -> bool:
    if clearance is None:
        return True
    return CLASSIFICATION_ORDER[project] <= CLASSIFICATION_ORDER[clearance]


def check(project: Classification, clearance: Classification | None, *, what: str) -> None:
    if not allows(project, clearance):
        raise PolicyViolation(
            f"{what} is cleared for '{clearance}' data but the project is '{project}'"
        )
