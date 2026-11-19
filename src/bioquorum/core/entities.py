"""Entities: projections of the event log (ARCHITECTURE.md §5.5).

None of these are written directly. ``bioquorum.storage.projections`` derives them from
events and ``bq rebuild`` can recreate every table from scratch (ADR-002).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from bioquorum.core.enums import (
    ArtifactKind,
    ClaimKind,
    Classification,
    FindingStatus,
    ModelRole,
    RunStatus,
    SessionStatus,
    Severity,
)
from bioquorum.core.refs import ClaimRef, DataRef
from bioquorum.core.status import VerificationStatus


class _Entity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Project(_Entity):
    id: str
    name: str
    root_dir: str
    data_dirs: list[str] = Field(default_factory=list)
    classification: Classification = Classification.INTERNAL
    model_policy: dict[str, Any] = Field(default_factory=dict)
    env_id: str | None = None
    created_at: datetime


class Session(_Entity):
    id: str
    project_id: str
    title: str
    parent_session_id: str | None = None
    forked_at_seq: int | None = None
    status: SessionStatus = SessionStatus.ACTIVE
    created_at: datetime


class Run(_Entity):
    id: str
    session_id: str
    kernel_id: str
    language: str
    code: str
    code_hash: str
    env_lock_hash: str | None
    inputs: list[DataRef] = Field(default_factory=list)
    outputs: list[DataRef] = Field(default_factory=list)
    stdout_ref: DataRef | None = None
    run_key: str | None = None
    status: RunStatus
    started_at: datetime
    ended_at: datetime | None = None


class Artifact(_Entity):
    id: str
    project_id: str
    name: str
    kind: ArtifactKind


class ArtifactVersion(_Entity):
    id: str
    artifact_id: str
    version: int
    parent_version_id: str | None = None
    content_hash: str
    run_id: str | None
    event_range: tuple[int, int]
    description: str = ""
    status: VerificationStatus = VerificationStatus.UNVERIFIED
    created_at: datetime


class Claim(_Entity):
    id: str
    session_id: str
    source_event_id: str
    text: str
    kind: ClaimKind
    refs: list[ClaimRef] = Field(default_factory=list)
    status: VerificationStatus = VerificationStatus.UNVERIFIED


class ReviewFinding(_Entity):
    id: str
    session_id: str
    review_id: str
    claim_id: str | None = None
    artifact_version_id: str | None = None
    check: str
    severity: Severity
    evidence: str
    reviewer_model: str
    status: FindingStatus = FindingStatus.OPEN


class ModelCall(_Entity):
    id: str
    event_id: str
    session_id: str
    provider: str
    model: str
    role: ModelRole
    classification: Classification
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    cost_known: bool = False
    latency_ms: int = 0


class AuditEntry(_Entity):
    id: str
    ts: datetime
    actor: str
    action: str
    target: str
    classification: Classification | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
