"""Research bundle manifest v0 (ARCHITECTURE.md §6.3, ADR-010).

The manifest exists from v0.1 even though bundle export ships later. Fields are only
ever added, never removed or renamed. ``bundle-spec/manifest-v0.schema.json`` is generated
from :class:`Manifest` and a test keeps them in sync.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from bioquorum.core.enums import ArtifactKind, Classification
from bioquorum.core.refs import DataRef
from bioquorum.core.status import VerificationStatus

SPEC_VERSION = "0"


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ManifestProject(_Model):
    name: str
    classification: Classification


class ManifestRun(_Model):
    run_id: str
    language: str
    code_path: str = Field(description="Path inside the bundle, e.g. runs/<id>/code.py")
    code_hash: str
    env_lock_hash: str | None
    inputs: list[DataRef] = Field(default_factory=list)
    outputs: list[DataRef] = Field(default_factory=list)
    run_key: str | None = None


class ManifestArtifact(_Model):
    artifact_id: str
    version_id: str
    name: str
    kind: ArtifactKind
    version: int
    content_hash: str
    path: str
    run_id: str | None
    status: VerificationStatus


class ManifestModel(_Model):
    provider: str
    model: str
    role: str


class Manifest(_Model):
    spec_version: Literal["0"] = SPEC_VERSION
    tool: str = "bioquorum"
    tool_version: str
    created_at: datetime
    project: ManifestProject
    runs: list[ManifestRun] = Field(default_factory=list)
    artifacts: list[ManifestArtifact] = Field(default_factory=list)
    models_used: list[ManifestModel] = Field(default_factory=list)
    files: dict[str, str] = Field(
        default_factory=dict, description="bundle-relative path -> sha256 of every file"
    )


def json_schema() -> dict:
    return Manifest.model_json_schema()
