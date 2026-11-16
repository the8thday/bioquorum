from sqlalchemy import select

from bioquorum.core import events as ev
from bioquorum.core.enums import (
    Actor,
    ArtifactKind,
    ClaimKind,
    Classification,
    FindingStatus,
    ModelRole,
    Severity,
)
from bioquorum.core.hashing import code_hash, run_key, sha256_text
from bioquorum.core.status import StatusTrigger, VerificationStatus
from bioquorum.storage import db as t
from bioquorum.storage.rebuild import snapshot


def test_full_scenario_projects_correctly(db, store):
    pid, sid, rid, vid, cid, fid = "P", "S", "R", "V", "C", "F"
    store.append(
        ev.ProjectCreated(
            project_id=pid, name="ibd", root_dir="/d", classification=Classification.CONFIDENTIAL
        ),
        session_id=None,
        actor=Actor.USER,
    )
    store.append(
        ev.SessionCreated(project_id=pid, title="target dossier"), session_id=sid, actor=Actor.USER
    )
    code = "import scanpy as sc\n"
    store.append(
        ev.RunStarted(run_id=rid, kernel_id="k", code=code, code_hash=code_hash(code)),
        session_id=sid,
        actor=Actor.AGENT,
    )
    snap = snapshot(db)
    assert snap["runs"][0]["status"] == "running" and snap["runs"][0]["ended_at"] is None
    key = run_key(code_hash(code), None, [])
    store.append(ev.RunFinished(run_id=rid, run_key=key), session_id=sid, actor=Actor.TOOL)
    store.append(
        ev.ArtifactVersionCreated(
            artifact_id="A",
            version_id=vid,
            project_id=pid,
            name="umap",
            kind=ArtifactKind.FIGURE,
            version=1,
            content_hash=sha256_text("png"),
            run_id=rid,
            event_range=(2, 3),
        ),
        session_id=sid,
        actor=Actor.AGENT,
    )
    store.append(
        ev.ClaimExtracted(
            claim_id=cid, source_event_id="E", text="1,248 cells removed", kind=ClaimKind.NUMERIC
        ),
        session_id=sid,
        actor=Actor.REVIEWER,
    )
    store.append(
        ev.ReviewFinding(
            review_id="RV",
            finding_id=fid,
            claim_id=cid,
            check="value_match",
            severity=Severity.ERROR,
            evidence="stdout says 1,842",
            reviewer_model="x:y",
        ),
        session_id=sid,
        actor=Actor.REVIEWER,
    )
    store.append(
        ev.ClaimStatusChanged(
            claim_id=cid,
            from_status=VerificationStatus.UNVERIFIED,
            to_status=VerificationStatus.DISPUTED,
            trigger=StatusTrigger.REVIEW_FAILED,
            finding_id=fid,
        ),
        session_id=sid,
        actor=Actor.REVIEWER,
    )
    store.append(
        ev.FindingResolved(finding_id=fid, status=FindingStatus.ADDRESSED),
        session_id=sid,
        actor=Actor.AGENT,
    )
    store.append(
        ev.ArtifactStatusChanged(
            version_id=vid,
            from_status=VerificationStatus.UNVERIFIED,
            to_status=VerificationStatus.REVIEWED,
            trigger=StatusTrigger.REVIEW_PASSED,
        ),
        session_id=sid,
        actor=Actor.REVIEWER,
    )
    store.append(
        ev.ModelCallFinished(
            call_id="M",
            provider="anthropic",
            model="m",
            role=ModelRole.REVIEWER,
            classification=Classification.CONFIDENTIAL,
            input_tokens=10,
            output_tokens=5,
            cost_usd=0.01,
        ),
        session_id=sid,
        actor=Actor.AGENT,
    )
    store.append(
        ev.ErrorOccurred(
            where="routing", error="confidential -> external provider", policy_violation=True
        ),
        session_id=sid,
        actor=Actor.SYSTEM,
    )
    store.append(ev.SessionTitleSet(title="renamed"), session_id=sid, actor=Actor.USER)

    snap = snapshot(db)
    assert snap["projects"][0]["classification"] == "confidential"
    assert snap["sessions"][0]["title"] == "renamed"
    run = snap["runs"][0]
    assert run["status"] == "succeeded" and run["run_key"] == key and run["ended_at"] is not None
    assert snap["artifacts"][0]["kind"] == "figure"
    ver = snap["artifact_versions"][0]
    assert ver["status"] == "reviewed" and (ver["event_seq_from"], ver["event_seq_to"]) == (2, 3)
    assert snap["claims"][0]["status"] == "disputed"
    assert snap["review_findings"][0]["status"] == "addressed"
    assert snap["model_calls"][0]["cost_usd"] == 0.01
    actions = sorted(a["action"] for a in snap["audit_entries"])
    assert actions == ["model_call", "policy_violation"]
    assert (
        next(a for a in snap["audit_entries"] if a["action"] == "policy_violation")[
            "policy_violation"
        ]
        is True
    )


def test_second_version_of_same_artifact_does_not_duplicate_artifact_row(db, store):
    store.append(ev.SessionCreated(project_id="P", title="t"), session_id="S", actor=Actor.USER)
    for v in (1, 2):
        store.append(
            ev.ArtifactVersionCreated(
                artifact_id="A",
                version_id=f"V{v}",
                project_id="P",
                name="n",
                kind=ArtifactKind.TABLE,
                version=v,
                parent_version_id="V1" if v == 2 else None,
                content_hash=sha256_text(str(v)),
                event_range=(0, 0),
            ),
            session_id="S",
            actor=Actor.AGENT,
        )
    with db.connect() as conn:
        assert conn.execute(select(t.artifacts)).all().__len__() == 1
        assert conn.execute(select(t.artifact_versions)).all().__len__() == 2
