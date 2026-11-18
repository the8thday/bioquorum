"""References to data, runs, artifacts and citations (ARCHITECTURE.md §5.5)."""

from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
)

from bioquorum.core.enums import DataKind

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", description="SHA-256 hex, lowercase")]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DataRef(_Frozen):
    """A snapshot of a file or in-memory object, identified by content hash."""

    uri: str
    content_hash: Sha256
    size: int = Field(ge=0)
    kind: DataKind = DataKind.FILE
    weak_hash: bool = Field(
        default=False,
        description="True when the object had no canonical serialization (ADR-008).",
    )


class RunRef(_Frozen):
    type: Literal["run"] = "run"
    run_id: str


class ArtifactRef(_Frozen):
    type: Literal["artifact"] = "artifact"
    artifact_version_id: str


class CitationRef(_Frozen):
    type: Literal["citation"] = "citation"
    doi: str | None = None
    pmid: str | None = None
    title: str | None = None


VALUE_NAME_PATTERN = r"^[A-Za-z][A-Za-z0-9_]{0,63}$"
MAX_VALUE_TEXT = 200


class NamedValue(_Frozen):
    """A value declared in kernel code with ``bq.value()`` (ADR-014). Scalars only: tables
    and figures are referenced as artifacts."""

    name: str = Field(pattern=VALUE_NAME_PATTERN)
    value: StrictInt | StrictFloat | StrictStr
    unit: str | None = Field(default=None, max_length=40)
    desc: str | None = Field(default=None, max_length=500, description="What the value means")

    @field_validator("value")
    @classmethod
    def _scalar(cls, v: int | float | str) -> int | float | str:
        if isinstance(v, float) and not math.isfinite(v):
            raise ValueError("value must be finite")
        if isinstance(v, str) and len(v) > MAX_VALUE_TEXT:
            raise ValueError(f"text values are limited to {MAX_VALUE_TEXT} characters")
        return v


class ValueRef(_Frozen):
    """A claim's ``{{name}}`` binding, resolved to the run that declared the value."""

    type: Literal["value"] = "value"
    name: str
    run_id: str


class QuoteRef(_Frozen):
    """A claim's ``[@source "quote"]`` binding, resolved to the tool result containing it."""

    type: Literal["quote"] = "quote"
    source: str  # as written: a source alias ("s3") or a literature id ("PMID:123")
    call_id: str  # the tool call whose result contains the quote
    quote: str


ClaimRef = Annotated[
    RunRef | ArtifactRef | CitationRef | ValueRef | QuoteRef, Field(discriminator="type")
]
