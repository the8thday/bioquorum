"""Event envelope and payload registry (ARCHITECTURE.md §5.2-5.3, ADR-002).

Every event type is a Pydantic model registered here with its ``event_type`` and
``schema_version``. The envelope stores the payload as a plain dict so storage never
depends on payload classes; ``Event.parsed()`` gives the typed payload back.

Adding an event type: subclass :class:`EventPayload`, set ``event_type``, decorate with
``@register``. Changing a payload incompatibly: bump ``schema_version`` on the new class,
keep the old class registered, and add an upgrader with ``@upgrader``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bioquorum.core.enums import (
    Actor,
    ArtifactKind,
    ClaimKind,
    Classification,
    FindingStatus,
    ModelRole,
    OutputStream,
    PermissionClass,
    PermissionScope,
    Severity,
)
from bioquorum.core.ids import id_time, new_id, utc_now
from bioquorum.core.messages import Message, OpaqueBytes, ToolResultPart
from bioquorum.core.refs import ClaimRef, DataRef, NamedValue
from bioquorum.core.selections import ArtifactSelection
from bioquorum.core.status import StatusTrigger, VerificationStatus
from bioquorum.core.verification import Tolerance, ValueComparison, VerificationOutcome

# ----------------------------------------------------------------- registry


class EventPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: ClassVar[str]
    schema_version: ClassVar[int] = 1


_REGISTRY: dict[str, dict[int, type[EventPayload]]] = {}
_UPGRADERS: dict[tuple[str, int], Callable[[dict[str, Any]], dict[str, Any]]] = {}


def register[T: type[EventPayload]](cls: T) -> T:
    versions = _REGISTRY.setdefault(cls.event_type, {})
    if cls.schema_version in versions:
        raise ValueError(f"{cls.event_type} v{cls.schema_version} registered twice")
    versions[cls.schema_version] = cls
    return cls


def upgrader(event_type: str, from_version: int):
    """Register a function that converts payload dicts from ``from_version`` to the next."""

    def deco(fn: Callable[[dict[str, Any]], dict[str, Any]]):
        _UPGRADERS[(event_type, from_version)] = fn
        return fn

    return deco


def event_types() -> list[str]:
    return sorted(_REGISTRY)


def latest_version(event_type: str) -> int:
    return max(_REGISTRY[event_type])


def payload_class(event_type: str, schema_version: int | None = None) -> type[EventPayload]:
    versions = _REGISTRY[event_type]
    return versions[schema_version if schema_version is not None else max(versions)]


def upgrade_payload(
    event_type: str, schema_version: int, payload: dict[str, Any]
) -> dict[str, Any]:
    """Bring a stored payload dict up to the latest schema version."""
    latest = latest_version(event_type)
    while schema_version < latest:
        fn = _UPGRADERS.get((event_type, schema_version))
        if fn is None:
            raise KeyError(
                f"no upgrader for {event_type} v{schema_version} -> v{schema_version + 1}"
            )
        payload = fn(payload)
        schema_version += 1
    return payload


# ----------------------------------------------------------------- envelope


class Event(BaseModel):
    """The append-only unit of truth. ``session_id`` is None only for project-level events."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=new_id)
    session_id: str | None
    seq: int = Field(ge=0, description="Per-session sequence assigned by storage")
    ts: datetime = Field(default_factory=utc_now)
    type: str
    schema_version: int = 1
    actor: Actor
    causation_id: str | None = None
    payload: dict[str, Any]

    @classmethod
    def build(
        cls,
        payload: EventPayload,
        *,
        session_id: str | None,
        seq: int,
        actor: Actor,
        causation_id: str | None = None,
        id: str | None = None,
        ts: datetime | None = None,
    ) -> Event:
        event_id = id or new_id()
        return cls(
            id=event_id,
            ts=ts or id_time(event_id),  # ts and id always agree -> replay order is unambiguous
            session_id=session_id,
            seq=seq,
            type=payload.event_type,
            schema_version=payload.schema_version,
            actor=actor,
            causation_id=causation_id,
            payload=payload.model_dump(mode="json"),
        )

    def parsed(self) -> EventPayload:
        """Typed payload at the latest schema version."""
        data = upgrade_payload(self.type, self.schema_version, self.payload)
        return payload_class(self.type).model_validate(data)


# ----------------------------------------------------------------- payloads: project & session


@register
class ProjectCreatedV1(EventPayload):
    event_type: ClassVar[str] = "project_created"
    project_id: str
    name: str
    root_dir: str
    data_dirs: list[str] = Field(default_factory=list)
    classification: Classification = Classification.INTERNAL
    model_policy: dict[str, Any] = Field(default_factory=dict)
    env_id: str | None = None


@register
class ProjectCreated(ProjectCreatedV1):
    schema_version: ClassVar[int] = 2
    recommended_agent: dict[str, Any] | None = None


@upgrader("project_created", 1)
def _legacy_project_defaults(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "recommended_agent": None}


@register
class ProgramCreated(EventPayload):
    event_type: ClassVar[str] = "program_created"
    project_id: str
    program_id: str
    name: str = Field(min_length=1, max_length=160)


@register
class ProgramUpdated(EventPayload):
    event_type: ClassVar[str] = "program_updated"
    project_id: str
    program_id: str
    name: str | None = Field(default=None, min_length=1, max_length=160)
    closed: bool = False

    @model_validator(mode="after")
    def one_change(self) -> Self:
        if (self.name is not None) == self.closed:
            raise ValueError("Choose one change to the program")
        return self


@register
class SessionAttached(EventPayload):
    event_type: ClassVar[str] = "session_attached"
    project_id: str
    program_id: str
    session_id: str


@register
class ConnectorChecked(EventPayload):
    event_type: ClassVar[str] = "connector_checked"
    project_id: str
    server: str
    status: str
    tools: int | None


@register
class ProjectSettingsChanged(EventPayload):
    event_type: ClassVar[str] = "project_settings_changed"
    project_id: str
    prior_revision: str | None
    patch: dict[str, Any]


@register
class ProjectNoteSaved(EventPayload):
    event_type: ClassVar[str] = "project_note_saved"
    project_id: str
    editor_session_id: str
    prior_revision: str | None
    body: str = Field(max_length=6000)
    draft_event_id: str | None = None


@register
class ProjectNoteDrafted(EventPayload):
    event_type: ClassVar[str] = "project_note_drafted"
    project_id: str
    prior_revision: str | None
    body: str = Field(max_length=6000)


@register
class SessionCreated(EventPayload):
    event_type: ClassVar[str] = "session_created"
    project_id: str
    title: str


@register
class SessionForked(EventPayload):
    event_type: ClassVar[str] = "session_forked"
    project_id: str
    parent_session_id: str
    forked_at_seq: int
    title: str


@register
class SessionTitleSet(EventPayload):
    event_type: ClassVar[str] = "session_title_set"
    title: str


@register
class SessionArchived(EventPayload):
    event_type: ClassVar[str] = "session_archived"


# ----------------------------------------------------------------- conversation & model


@register
class TurnRequestedV1(EventPayload):
    event_type: ClassVar[str] = "turn_requested"
    question: str
    plan_id: str | None = None


@register
class TurnRequestedV2(TurnRequestedV1):
    schema_version: ClassVar[int] = 2
    worker_model: str | None = None


class TurnReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["answer", "claim", "value", "figure", "source", "run", "activity", "artifact"]
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(default="", max_length=128)
    excerpt: str = Field(default="", max_length=2000)

    selection: ArtifactSelection | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if (self.selection is not None) != (self.kind == "artifact"):
            raise ValueError("An artifact reference requires a selection")
        if bool(self.name) != (self.kind in {"value", "figure"}):
            raise ValueError("Only values and figures require a record name")
        if self.excerpt and self.kind not in {"answer", "source", "artifact"}:
            raise ValueError("Only answers, sources and artifact selections accept an excerpt")
        return self


@register
class TurnRequestedV3(TurnRequestedV2):
    schema_version: ClassVar[int] = 3
    references: list[TurnReference] = Field(default_factory=list, max_length=8)


@register
class TurnRequested(TurnRequestedV3):
    schema_version: ClassVar[int] = 4
    attachments: list[str] = Field(default_factory=list, max_length=8)


@upgrader("turn_requested", 3)
def _turn_files_default(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "attachments": []}


@upgrader("turn_requested", 2)
def _turn_references_default(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "references": []}


@upgrader("turn_requested", 1)
def _turn_worker_default(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "worker_model": None}


@register
class TurnStarted(EventPayload):
    event_type: ClassVar[str] = "turn_started"
    turn_id: str


@register
class TurnFinished(EventPayload):
    event_type: ClassVar[str] = "turn_finished"
    turn_id: str
    outcome: Literal["completed", "failed", "interrupted", "declined"]
    error: str = ""


@register
class UserMessage(EventPayload):
    event_type: ClassVar[str] = "user_message"
    message: Message


@register
class AssistantMessage(EventPayload):
    event_type: ClassVar[str] = "assistant_message"
    message: Message
    model_call_id: str | None = None
    provider_raw: OpaqueBytes | None = Field(default=None, description="Debug only (ADR-003)")


@register
class ModelCallStarted(EventPayload):
    event_type: ClassVar[str] = "model_call_started"
    call_id: str
    provider: str
    model: str
    role: ModelRole
    classification: Classification


@register
class ModelCallFinished(EventPayload):
    event_type: ClassVar[str] = "model_call_finished"
    call_id: str
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
    provider_raw: OpaqueBytes | None = None


@register
class ModelCallFailed(EventPayload):
    event_type: ClassVar[str] = "model_call_failed"
    call_id: str
    provider: str
    model: str
    role: ModelRole
    error: str


# ----------------------------------------------------------------- tools


@register
class ToolCallRequested(EventPayload):
    event_type: ClassVar[str] = "tool_call_requested"
    call_id: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    permission: PermissionClass


@register
class ToolCallFinished(EventPayload):
    event_type: ClassVar[str] = "tool_call_finished"
    call_id: str
    tool: str
    result: ToolResultPart
    duration_ms: int = 0


@register
class ToolCallFailed(EventPayload):
    event_type: ClassVar[str] = "tool_call_failed"
    call_id: str
    tool: str
    error: str


# ----------------------------------------------------------------- plan & permission


@register
class PlanProposed(EventPayload):
    event_type: ClassVar[str] = "plan_proposed"
    plan_id: str
    steps: list[str]


@register
class PlanApproved(EventPayload):
    event_type: ClassVar[str] = "plan_approved"
    plan_id: str


@register
class PlanStepDone(EventPayload):
    event_type: ClassVar[str] = "plan_step_done"
    plan_id: str
    step_index: int = Field(ge=0)


@register
class PlanDeviation(EventPayload):
    event_type: ClassVar[str] = "plan_deviation"
    plan_id: str
    reason: str
    change: str


@register
class PlanAbandoned(EventPayload):
    event_type: ClassVar[str] = "plan_abandoned"
    plan_id: str
    reason: str = ""


@register
class PermissionRequested(EventPayload):
    event_type: ClassVar[str] = "permission_requested"
    request_id: str
    permission: PermissionClass
    target: str
    reason: str = ""


@register
class PermissionDecided(EventPayload):
    event_type: ClassVar[str] = "permission_decided"
    request_id: str
    permission: PermissionClass
    target: str
    granted: bool
    scope: PermissionScope | None = None


# ----------------------------------------------------------------- runs


@register
class RunStarted(EventPayload):
    event_type: ClassVar[str] = "run_started"
    run_id: str
    kernel_id: str
    language: str = "python"
    code: str
    code_hash: str
    env_lock_hash: str | None = None
    input_paths: list[str] = Field(default_factory=list)
    environment: str | None = None
    synthetic: bool = False
    verification_run: bool = False
    inputs: list[DataRef] = Field(default_factory=list)
    # Kernel isolation: "bwrap", "bwrap+net", "seatbelt" or "none" (ADR-017/026). None = recorded
    # before isolation existed.
    isolation: str | None = None


@register
class RunOutput(EventPayload):
    event_type: ClassVar[str] = "run_output"
    run_id: str
    stream: OutputStream
    text: str | None = None
    ref: DataRef | None = None


@register
class LineageObserved(EventPayload):
    event_type: ClassVar[str] = "lineage_observed"
    run_id: str
    path: str
    direction: Literal["input", "output"]
    function: str
    post_run_hash: str | None = None


@register
class RunFinished(EventPayload):
    event_type: ClassVar[str] = "run_finished"
    run_id: str
    outputs: list[DataRef] = Field(default_factory=list)
    stdout_ref: DataRef | None = None
    run_key: str
    values: list[NamedValue] = Field(
        default_factory=list, description="Declared with bq.value() (ADR-014)"
    )


@register
class RunFailed(EventPayload):
    event_type: ClassVar[str] = "run_failed"
    run_id: str
    error: str


@register
class RunCancelled(EventPayload):
    event_type: ClassVar[str] = "run_cancelled"
    run_id: str


@register
class RunTimedOut(EventPayload):
    event_type: ClassVar[str] = "run_timed_out"
    run_id: str
    timeout_s: float


# ----------------------------------------------------------------- artifacts & claims


@register
class FileRegistered(EventPayload):
    event_type: ClassVar[str] = "file_registered"
    project_id: str
    file_id: str
    filename: str
    path: str
    ref: DataRef
    classification: Classification
    source: Literal["user_upload"] = "user_upload"


MaterialKind = Literal["experimental_data", "vendor_delivery", "literature", "other"]


class MaterialChange(EventPayload):
    project_id: str
    program_id: str


@register
class MaterialRegistered(MaterialChange):
    event_type: ClassVar[str] = "material_registered"
    upload_event_id: str
    kind: MaterialKind
    source: str = Field(min_length=1, max_length=1200)


@register
class MaterialSuperseded(MaterialChange):
    event_type: ClassVar[str] = "material_superseded"
    prior_version_id: str
    upload_event_id: str
    source: str = Field(min_length=1, max_length=1200)
    reason: str = Field(min_length=1, max_length=1200)


@register
class MaterialWithdrawn(MaterialChange):
    event_type: ClassVar[str] = "material_withdrawn"
    version_id: str
    reason: str = Field(min_length=1, max_length=1200)


class MaterialReview(EventPayload):
    project_id: str
    program_id: str
    material_version_id: str


@register
class ClaimReviewFlagged(MaterialReview):
    event_type: ClassVar[str] = "claim_review_flagged"
    claim_id: str


@register
class ClaimReviewCleared(MaterialReview):
    event_type: ClassVar[str] = "claim_review_cleared"
    claim_id: str
    recompute_id: str
    replacement_claim_id: str


@register
class RecomputeStarted(MaterialReview):
    event_type: ClassVar[str] = "recompute_started"
    recompute_id: str
    session_id: str
    replacement_version_id: str
    run_ids: list[str]
    claim_ids: list[str]


class RecomputedClaim(BaseModel):
    old_claim_id: str
    new_claim_id: str | None = None
    review_cleared: bool = False


@register
class RecomputeFinished(MaterialReview):
    event_type: ClassVar[str] = "recompute_finished"
    recompute_id: str
    status: Literal["completed", "failed"]
    reason: str = ""
    run_map: dict[str, str] = Field(default_factory=dict)
    comparisons: list[RecomputedClaim] = Field(default_factory=list)


@register
class ArtifactVersionCreated(EventPayload):
    event_type: ClassVar[str] = "artifact_version_created"
    artifact_id: str
    version_id: str
    project_id: str
    name: str
    kind: ArtifactKind
    version: int = Field(ge=1)
    parent_version_id: str | None = None
    content_hash: str
    run_id: str | None = None
    event_range: tuple[int, int]
    description: str = ""


@register
class ArtifactStatusChanged(EventPayload):
    event_type: ClassVar[str] = "artifact_status_changed"
    version_id: str
    from_status: VerificationStatus
    to_status: VerificationStatus
    trigger: StatusTrigger
    reason: str = ""
    finding_id: str | None = None
    rerun_id: str | None = None


@register
class ClaimExtracted(EventPayload):
    event_type: ClassVar[str] = "claim_extracted"
    claim_id: str
    source_event_id: str
    text: str
    kind: ClaimKind
    refs: list[ClaimRef] = Field(default_factory=list)


@register
class ClaimStatusChanged(EventPayload):
    event_type: ClassVar[str] = "claim_status_changed"
    claim_id: str
    from_status: VerificationStatus
    to_status: VerificationStatus
    trigger: StatusTrigger
    reason: str = ""
    finding_id: str | None = None
    rerun_id: str | None = None


# ----------------------------------------------------------------- review & reproduction


@register
class ReviewStarted(EventPayload):
    event_type: ClassVar[str] = "review_started"
    review_id: str
    reviewer_model: str
    target_event_id: str


@register
class ReviewFinding(EventPayload):
    event_type: ClassVar[str] = "review_finding"
    review_id: str
    finding_id: str
    claim_id: str | None = None
    artifact_version_id: str | None = None
    check: str
    severity: Severity
    evidence: str
    reviewer_model: str


@register
class ReviewFinished(EventPayload):
    event_type: ClassVar[str] = "review_finished"
    review_id: str
    finding_count: int = Field(ge=0)
    claim_count: int | None = Field(default=None, ge=0)
    limitations: list[str] = Field(default_factory=list)


@register
class FindingResolved(EventPayload):
    event_type: ClassVar[str] = "finding_resolved"
    finding_id: str
    status: FindingStatus
    note: str = ""


@register
class RerunStarted(EventPayload):
    event_type: ClassVar[str] = "rerun_started"
    rerun_id: str
    artifact_version_id: str | None = None
    run_ids: list[str]
    claim_id: str | None = None


@register
class RerunFinished(EventPayload):
    event_type: ClassVar[str] = "rerun_finished"
    rerun_id: str
    artifact_version_id: str | None = None
    matched: bool
    details: dict[str, Any] = Field(default_factory=dict)


@register
class ReplicationStarted(EventPayload):
    event_type: ClassVar[str] = "replication_started"
    replication_id: str
    claim_id: str
    blind_session_id: str
    question: str
    definitions: dict[str, str]
    tolerance: Tolerance
    estimated_usd: float
    max_estimated_usd: float


@register
class ReplicationFinished(EventPayload):
    event_type: ClassVar[str] = "replication_finished"
    replication_id: str
    claim_id: str
    outcome: VerificationOutcome
    comparisons: list[ValueComparison] = Field(default_factory=list)
    note: str = ""


@register
class BundleExported(EventPayload):
    event_type: ClassVar[str] = "bundle_exported"
    bundle_id: str
    path: str
    manifest_hash: str
    run_ids: list[str] | None = None
    claim_ids: list[str] | None = None


# ----------------------------------------------------------------- compaction & errors


@register
class ExplorationStopped(EventPayload):
    event_type: ClassVar[str] = "exploration_stopped"
    reason: str


class LiteratureExcerpt(BaseModel):
    call_id: str
    source_event_id: str
    tool: str
    text: str = Field(min_length=12, max_length=360)
    identifier: str | None = None


@register
class LiteratureStarted(EventPayload):
    event_type: ClassVar[str] = "literature_started"
    task_id: str
    parent_call_id: str
    question: str = Field(max_length=1200)
    prefetched: bool = False


@register
class LiteratureFinished(EventPayload):
    event_type: ClassVar[str] = "literature_finished"
    task_id: str
    parent_call_id: str
    outcome: Literal["completed", "inconclusive", "cancelled"]
    prefetched: bool = False
    summary: str = Field(default="", max_length=1200)
    sources: list[LiteratureExcerpt] = Field(default_factory=list, max_length=4)
    reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    cost_known: bool = False
    priced_calls: int = 0
    model_calls: int = 0
    failed_calls: int = 0


@register
class ContextCompacted(EventPayload):
    event_type: ClassVar[str] = "context_compacted"
    from_seq: int
    to_seq: int
    summary: str
    summarizer_model: str
    manual: bool = False


@register
class ErrorOccurred(EventPayload):
    event_type: ClassVar[str] = "error"
    where: str
    error: str
    policy_violation: bool = False


@register
class CoveragePlanned(EventPayload):
    event_type: ClassVar[str] = "coverage_planned"
    topics: dict[str, str]


@register
class CoverageUpdated(EventPayload):
    event_type: ClassVar[str] = "coverage_updated"
    assignments: dict[str, str]  # recorded call IDs to declared topic names


@register
class CoverageClosed(EventPayload):
    event_type: ClassVar[str] = "coverage_closed"
    gaps: list[str]
    decisive: str


@register
class FirstPassStarted(EventPayload):
    event_type: ClassVar[str] = "first_pass_started"


@register
class ClarificationRequested(EventPayload):
    event_type: ClassVar[str] = "clarification_requested"
    question: str = Field(min_length=1, max_length=400)
    assumption: str = Field(min_length=1, max_length=500)


@register
class ClarificationAnswered(EventPayload):
    event_type: ClassVar[str] = "clarification_answered"
    request_id: str
    turn_id: str


class DecisionClaim(BaseModel):
    """An immutable citation to a particular claim and its trust event."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    session_id: str
    source_event_id: str
    revision: str
    text: str
    status: VerificationStatus


@register
class DecisionRecorded(EventPayload):
    event_type: ClassVar[str] = "decision_recorded"
    project_id: str
    program_id: str
    decision_id: str
    text: str = Field(min_length=1, max_length=4000)
    claims: list[DecisionClaim] = Field(min_length=1, max_length=100)


@register
class DecisionRevised(DecisionRecorded):
    event_type: ClassVar[str] = "decision_revised"
    prior_revision: str


@register
class ProgramBundleExported(EventPayload):
    event_type: ClassVar[str] = "program_bundle_exported"
    project_id: str
    program_id: str
    bundle_id: str
    material_version_ids: list[str]
    session_bundle_ids: list[str]
    manifest_hash: str
