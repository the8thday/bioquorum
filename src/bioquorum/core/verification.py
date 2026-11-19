"""Typed, provider-independent verification results and comparisons."""

from __future__ import annotations

import math
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from bioquorum.core.refs import NamedValue


class Tolerance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    absolute: float = Field(default=0, ge=0, allow_inf_nan=False)
    relative: float = Field(default=0, ge=0, le=1, allow_inf_nan=False)


class ValueComparison(BaseModel):
    name: str
    unit: str | None
    original: int | float | str
    observed: int | float | str | None = None
    observed_unit: str | None = None
    matched: bool | None
    reason: str = ""


VerificationOutcome = Literal["matched", "different", "inconclusive"]


def compare_values(
    original: list[NamedValue], observed: list[NamedValue], tolerance: Tolerance
) -> list[ValueComparison]:
    readings = {v.name: v for v in observed}
    results = []
    for before in original:
        after = readings.get(before.name)
        matched: bool | None
        reason = ""
        if after is None:
            matched, reason = None, "Named result was not produced"
        elif before.unit != after.unit:
            matched, reason = False, "Units differ"
        elif type(before.value) is int and type(after.value) is int and tolerance == Tolerance():
            matched = before.value == after.value
        elif isinstance(before.value, int | float) and isinstance(after.value, int | float):
            try:
                matched = math.isclose(
                    before.value,
                    after.value,
                    rel_tol=tolerance.relative,
                    abs_tol=tolerance.absolute,
                )
            except OverflowError:
                matched = before.value == after.value
        else:
            matched = before.value == after.value
        results.append(
            ValueComparison(
                name=before.name,
                unit=before.unit,
                original=before.value,
                observed=after.value if after else None,
                observed_unit=after.unit if after else None,
                matched=matched,
                reason=reason,
            )
        )
    return results


def input_path(value: str) -> PurePosixPath:
    """Accept only portable, ordinary project paths; staging never restores host paths."""
    path = PurePosixPath(value)
    if (
        not path.parts
        or path.is_absolute()
        or "\\" in value
        or ":" in value
        or any(part.startswith(".") for part in path.parts)
        or path.as_posix() in {"bioquorum.toml", "pixi.toml", "pixi.lock"}
    ):
        raise ValueError("Input path is not a portable project file")
    return path
