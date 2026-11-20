"""One handler per event type that affects a projection table.

Handlers must be deterministic functions of (event, payload, current tables). They never
read the clock or generate IDs: every ID and timestamp comes from the event.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection

from bioquorum.core import events as ev
from bioquorum.core.enums import FindingStatus, RunStatus, SessionStatus
from bioquorum.core.events import Event, EventPayload
from bioquorum.core.status import VerificationStatus
from bioquorum.storage import db as t

Handler = Callable[[Connection, Event, Any], None]
HANDLERS: dict[str, Handler] = {}


def _on(payload_cls: type[EventPayload]):
    def deco(fn: Handler) -> Handler:
        HANDLERS[payload_cls.event_type] = fn
        return fn

    return deco


def _dump(models) -> list[dict]:
    return [m.model_dump(mode="json") for m in models]


def _audit(
    conn: Connection,
    e: Event,
    action: str,
    target: str,
    detail: dict,
    *,
    violation=False,
    classification=None,
):
    conn.execute(
        insert(t.audit_entries).values(
            id=e.id,
            ts=e.ts.isoformat(),
            actor=e.actor.value,
            action=action,
            target=target,
            classification=classification,
            detail=detail,
            policy_violation=violation,
        )
    )


# ----------------------------------------------------------------- project & session


@_on(ev.ProjectCreated)
def _project_created(conn: Connection, e: Event, p: ev.ProjectCreated) -> None:
    conn.execute(
        insert(t.projects).values(
            id=p.project_id,
            name=p.name,
            root_dir=p.root_dir,
            data_dirs=p.data_dirs,
            classification=p.classification.value,
            model_policy=p.model_policy,
            env_id=p.env_id,
            created_at=e.ts.isoformat(),
        )
    )


@_on(ev.SessionCreated)
def _session_created(conn: Connection, e: Event, p: ev.SessionCreated) -> None:
    conn.execute(
        insert(t.sessions).values(
            id=e.session_id,
            project_id=p.project_id,
            title=p.title,
            parent_session_id=None,
            forked_at_seq=None,
            status=SessionStatus.ACTIVE.value,
            created_at=e.ts.isoformat(),
        )
    )


@_on(ev.SessionForked)
def _session_forked(conn: Connection, e: Event, p: ev.SessionForked) -> None:
    conn.execute(
        insert(t.sessions).values(
            id=e.session_id,
            project_id=p.project_id,
            title=p.title,
            parent_session_id=p.parent_session_id,
            forked_at_seq=p.forked_at_seq,
            status=SessionStatus.ACTIVE.value,
            created_at=e.ts.isoformat(),
        )
    )


@_on(ev.SessionTitleSet)
def _session_title(conn: Connection, e: Event, p: ev.SessionTitleSet) -> None:
    conn.execute(update(t.sessions).where(t.sessions.c.id == e.session_id).values(title=p.title))


@_on(ev.SessionArchived)
def _session_archived(conn: Connection, e: Event, p: ev.SessionArchived) -> None:
    conn.execute(
        update(t.sessions)
        .where(t.sessions.c.id == e.session_id)
        .values(status=SessionStatus.ARCHIVED.value)
    )


# ----------------------------------------------------------------- model calls & audit


@_on(ev.ModelCallFinished)
def _model_call_finished(conn: Connection, e: Event, p: ev.ModelCallFinished) -> None:
    conn.execute(
        insert(t.model_calls).values(
            id=p.call_id,
            event_id=e.id,
            session_id=e.session_id,
            provider=p.provider,
            model=p.model,
            role=p.role.value,
            classification=p.classification.value,
            input_tokens=p.input_tokens,
            output_tokens=p.output_tokens,
            cache_read_tokens=p.cache_read_tokens,
            cache_write_tokens=p.cache_write_tokens,
            cost_usd=p.cost_usd,
            cost_known=p.cost_known,
            latency_ms=p.latency_ms,
        )
    )
    _audit(
        conn,
        e,
        "model_call",
        f"{p.provider}:{p.model}",
        {"role": p.role.value, "input_tokens": p.input_tokens, "output_tokens": p.output_tokens},
        classification=p.classification.value,
    )


@_on(ev.ToolCallFinished)
def _tool_call_finished(conn: Connection, e: Event, p: ev.ToolCallFinished) -> None:
    _audit(conn, e, "tool_call", p.tool, {"call_id": p.call_id, "is_error": p.result.is_error})


@_on(ev.ToolCallFailed)
def _tool_call_failed(conn: Connection, e: Event, p: ev.ToolCallFailed) -> None:
    _audit(conn, e, "tool_call_failed", p.tool, {"call_id": p.call_id, "error": p.error})


@_on(ev.PermissionDecided)
def _permission_decided(conn: Connection, e: Event, p: ev.PermissionDecided) -> None:
    _audit(
        conn,
        e,
        "permission",
        p.target,
        {
            "permission": p.permission.value,
            "granted": p.granted,
            "scope": p.scope.value if p.scope else None,
        },
    )


@_on(ev.ErrorOccurred)
def _error(conn: Connection, e: Event, p: ev.ErrorOccurred) -> None:
    if p.policy_violation:
        _audit(conn, e, "policy_violation", p.where, {"error": p.error}, violation=True)


# ----------------------------------------------------------------- runs


@_on(ev.RunStarted)
def _run_started(conn: Connection, e: Event, p: ev.RunStarted) -> None:
    conn.execute(
        insert(t.runs).values(
            id=p.run_id,
            session_id=e.session_id,
            kernel_id=p.kernel_id,
            language=p.language,
            code=p.code,
            code_hash=p.code_hash,
            env_lock_hash=p.env_lock_hash,
            inputs=_dump(p.inputs),
            outputs=[],
            stdout_ref=None,
            run_key=None,
            status=RunStatus.RUNNING.value,
            started_at=e.ts.isoformat(),
            ended_at=None,
        )
    )


def _end_run(conn: Connection, e: Event, run_id: str, status: RunStatus, **extra) -> None:
    conn.execute(
        update(t.runs)
        .where(t.runs.c.id == run_id)
        .values(status=status.value, ended_at=e.ts.isoformat(), **extra)
    )


@_on(ev.RunFinished)
def _run_finished(conn: Connection, e: Event, p: ev.RunFinished) -> None:
    _end_run(
        conn,
        e,
        p.run_id,
        RunStatus.SUCCEEDED,
        outputs=_dump(p.outputs),
        stdout_ref=p.stdout_ref.model_dump(mode="json") if p.stdout_ref else None,
        run_key=p.run_key,
    )


@_on(ev.RunFailed)
def _run_failed(conn: Connection, e: Event, p: ev.RunFailed) -> None:
    _end_run(conn, e, p.run_id, RunStatus.FAILED)


@_on(ev.RunCancelled)
def _run_cancelled(conn: Connection, e: Event, p: ev.RunCancelled) -> None:
    _end_run(conn, e, p.run_id, RunStatus.CANCELLED)


@_on(ev.RunTimedOut)
def _run_timed_out(conn: Connection, e: Event, p: ev.RunTimedOut) -> None:
    _end_run(conn, e, p.run_id, RunStatus.TIMED_OUT)


# ----------------------------------------------------------------- artifacts


@_on(ev.ArtifactVersionCreated)
def _artifact_version_created(conn: Connection, e: Event, p: ev.ArtifactVersionCreated) -> None:
    exists = conn.execute(select(t.artifacts.c.id).where(t.artifacts.c.id == p.artifact_id)).first()
    if exists is None:
        conn.execute(
            insert(t.artifacts).values(
                id=p.artifact_id, project_id=p.project_id, name=p.name, kind=p.kind.value
            )
        )
    conn.execute(
        insert(t.artifact_versions).values(
            id=p.version_id,
            artifact_id=p.artifact_id,
            version=p.version,
            parent_version_id=p.parent_version_id,
            content_hash=p.content_hash,
            run_id=p.run_id,
            event_seq_from=p.event_range[0],
            event_seq_to=p.event_range[1],
            description=p.description,
            status=VerificationStatus.UNVERIFIED.value,
            created_at=e.ts.isoformat(),
        )
    )


@_on(ev.ArtifactStatusChanged)
def _artifact_status_changed(conn: Connection, e: Event, p: ev.ArtifactStatusChanged) -> None:
    conn.execute(
        update(t.artifact_versions)
        .where(t.artifact_versions.c.id == p.version_id)
        .values(status=p.to_status.value)
    )


# ----------------------------------------------------------------- claims & review


@_on(ev.ClaimExtracted)
def _claim_extracted(conn: Connection, e: Event, p: ev.ClaimExtracted) -> None:
    conn.execute(
        insert(t.claims).values(
            id=p.claim_id,
            session_id=e.session_id,
            source_event_id=p.source_event_id,
            text=p.text,
            kind=p.kind.value,
            refs=_dump(p.refs),
            status=VerificationStatus.UNVERIFIED.value,
        )
    )


@_on(ev.ClaimStatusChanged)
def _claim_status_changed(conn: Connection, e: Event, p: ev.ClaimStatusChanged) -> None:
    conn.execute(
        update(t.claims).where(t.claims.c.id == p.claim_id).values(status=p.to_status.value)
    )


@_on(ev.ReviewFinding)
def _review_finding(conn: Connection, e: Event, p: ev.ReviewFinding) -> None:
    conn.execute(
        insert(t.review_findings).values(
            id=p.finding_id,
            session_id=e.session_id,
            review_id=p.review_id,
            claim_id=p.claim_id,
            artifact_version_id=p.artifact_version_id,
            check=p.check,
            severity=p.severity.value,
            evidence=p.evidence,
            reviewer_model=p.reviewer_model,
            status=FindingStatus.OPEN.value,
        )
    )


@_on(ev.FindingResolved)
def _finding_resolved(conn: Connection, e: Event, p: ev.FindingResolved) -> None:
    conn.execute(
        update(t.review_findings)
        .where(t.review_findings.c.id == p.finding_id)
        .values(status=p.status.value)
    )
