"""Additive portable extension to manifest v0."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from bioquorum.core.bundle import (
    ManifestArtifact,
    ManifestModel,
    ManifestProject,
    ManifestRun,
    _Model,
)


class PortableRun(_Model):
    run_id: str
    inputs: dict[str, str]
    stdout_sha256: str | None
    environment: str | None
    synthetic: bool = False
    preceding_runs: list[str] = Field(default_factory=list)


class ManifestV1(_Model):
    spec_version: Literal["1"] = "1"
    tool: str
    tool_version: str
    created_at: datetime
    project: ManifestProject
    runs: list[ManifestRun]
    artifacts: list[ManifestArtifact]
    models_used: list[ManifestModel]
    files: dict[str, str]
    run_records: list[PortableRun] = Field(default_factory=list)
    claim_bindings_path: str = "review/claims.json"
    pixi_lock_path: str | None = None
