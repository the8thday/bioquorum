"""References to data, runs, artifacts and citations (ARCHITECTURE.md §5.5)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

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


ClaimRef = Annotated[RunRef | ArtifactRef | CitationRef, Field(discriminator="type")]
