"""Deterministic random event streams for projection/rebuild tests."""

from __future__ import annotations

import random

from bioquorum.core import events as ev
from bioquorum.core.enums import (
    Actor,
    ArtifactKind,
    ClaimKind,
    Classification,
    FindingStatus,
    ModelRole,
    PermissionClass,
    PermissionScope,
    Severity,
)
from bioquorum.core.hashing import code_hash, run_key, sha256_text
from bioquorum.core.ids import new_id
from bioquorum.core.messages import Message, TextPart, ToolResultPart
from bioquorum.core.refs import ClaimRef, DataRef, RunRef
from bioquorum.core.status import StatusTrigger, VerificationStatus, transition
from bioquorum.storage.events import EventStore


def _ref(rng: random.Random) -> DataRef:
    return DataRef(
        uri=f"file:///d/{rng.randrange(1000)}.csv",
        content_hash=sha256_text(str(rng.random())),
        size=rng.randrange(1, 10_000),
    )


def generate(
    store: EventStore, seed: int, sessions: int = 2, steps: int = 40
) -> dict[str, list[str]]:
    """Append a plausible random stream. Returns ids created, for assertions."""
    rng = random.Random(seed)
    created: dict[str, list[str]] = {
        "sessions": [],
        "runs": [],
        "claims": [],
        "versions": [],
        "findings": [],
    }
    project_id = new_id()
    store.append(
        ev.ProjectCreated(
            project_id=project_id,
            name=f"proj-{seed}",
            root_dir="/data/p",
            classification=rng.choice(list(Classification)),
        ),
        session_id=None,
        actor=Actor.USER,
    )
    for _ in range(sessions):
        sid = new_id()
        created["sessions"].append(sid)
        store.append(
            ev.SessionCreated(project_id=project_id, title="t"), session_id=sid, actor=Actor.USER
        )
        claim_status: dict[str, VerificationStatus] = {}
        version_status: dict[str, VerificationStatus] = {}
        open_runs: list[str] = []
        for _ in range(steps):
            kind = rng.choice(
                [
                    "msg",
                    "run",
                    "artifact",
                    "claim",
                    "finding",
                    "model",
                    "perm",
                    "error",
                    "title",
                    "status",
                ]
            )
            if kind == "msg":
                store.append(
                    ev.UserMessage(message=Message.user("q")), session_id=sid, actor=Actor.USER
                )
                store.append(
                    ev.AssistantMessage(message=Message.assistant("a")),
                    session_id=sid,
                    actor=Actor.AGENT,
                )
            elif kind == "run":
                rid = new_id()
                code = f"x = {rng.randrange(100)}"
                inputs = [_ref(rng) for _ in range(rng.randrange(3))]
                store.append(
                    ev.RunStarted(
                        run_id=rid,
                        kernel_id="k1",
                        code=code,
                        code_hash=code_hash(code),
                        inputs=inputs,
                    ),
                    session_id=sid,
                    actor=Actor.AGENT,
                )
                created["runs"].append(rid)
                outcome = rng.choice(["ok", "ok", "fail", "cancel", "timeout", "open"])
                if outcome == "ok":
                    store.append(
                        ev.RunFinished(
                            run_id=rid,
                            outputs=[_ref(rng)],
                            run_key=run_key(
                                code_hash(code), None, [i.content_hash for i in inputs]
                            ),
                        ),
                        session_id=sid,
                        actor=Actor.TOOL,
                    )
                elif outcome == "fail":
                    store.append(
                        ev.RunFailed(run_id=rid, error="boom"), session_id=sid, actor=Actor.TOOL
                    )
                elif outcome == "cancel":
                    store.append(ev.RunCancelled(run_id=rid), session_id=sid, actor=Actor.USER)
                elif outcome == "timeout":
                    store.append(
                        ev.RunTimedOut(run_id=rid, timeout_s=30), session_id=sid, actor=Actor.SYSTEM
                    )
                else:
                    open_runs.append(rid)
            elif kind == "artifact":
                aid = (
                    rng.choice(created["versions"] + [None])
                    if created["versions"] and rng.random() < 0.3
                    else None
                )
                vid = new_id()
                store.append(
                    ev.ArtifactVersionCreated(
                        artifact_id=new_id() if aid is None else aid,
                        version_id=vid,
                        project_id=project_id,
                        name="fig",
                        kind=rng.choice(list(ArtifactKind)),
                        version=1,
                        content_hash=sha256_text(vid),
                        run_id=rng.choice(created["runs"]) if created["runs"] else None,
                        event_range=(0, 1),
                    ),
                    session_id=sid,
                    actor=Actor.AGENT,
                )
                created["versions"].append(vid)
                version_status[vid] = VerificationStatus.UNVERIFIED
            elif kind == "claim":
                cid = new_id()
                refs: list[ClaimRef] = (
                    [RunRef(run_id=rng.choice(created["runs"]))] if created["runs"] else []
                )
                store.append(
                    ev.ClaimExtracted(
                        claim_id=cid,
                        source_event_id=new_id(),
                        text="x is 3",
                        kind=rng.choice(list(ClaimKind)),
                        refs=refs,
                    ),
                    session_id=sid,
                    actor=Actor.REVIEWER,
                )
                created["claims"].append(cid)
                claim_status[cid] = VerificationStatus.UNVERIFIED
            elif kind == "finding" and claim_status:
                fid = new_id()
                store.append(
                    ev.ReviewFinding(
                        review_id=new_id(),
                        finding_id=fid,
                        claim_id=rng.choice(list(claim_status)),
                        check="value_match",
                        severity=rng.choice(list(Severity)),
                        evidence="e",
                        reviewer_model="m",
                    ),
                    session_id=sid,
                    actor=Actor.REVIEWER,
                )
                created["findings"].append(fid)
                if rng.random() < 0.5:
                    store.append(
                        ev.FindingResolved(
                            finding_id=fid,
                            status=rng.choice([FindingStatus.ADDRESSED, FindingStatus.DISMISSED]),
                        ),
                        session_id=sid,
                        actor=Actor.AGENT,
                    )
            elif kind == "status" and (claim_status or version_status):
                trigger = rng.choice(list(StatusTrigger))
                if claim_status and (not version_status or rng.random() < 0.5):
                    cid = rng.choice(list(claim_status))
                    new = transition(claim_status[cid], trigger)
                    store.append(
                        ev.ClaimStatusChanged(
                            claim_id=cid,
                            from_status=claim_status[cid],
                            to_status=new,
                            trigger=trigger,
                        ),
                        session_id=sid,
                        actor=Actor.REVIEWER,
                    )
                    claim_status[cid] = new
                else:
                    vid = rng.choice(list(version_status))
                    new = transition(version_status[vid], trigger)
                    store.append(
                        ev.ArtifactStatusChanged(
                            version_id=vid,
                            from_status=version_status[vid],
                            to_status=new,
                            trigger=trigger,
                        ),
                        session_id=sid,
                        actor=Actor.REVIEWER,
                    )
                    version_status[vid] = new
            elif kind == "model":
                store.append(
                    ev.ModelCallFinished(
                        call_id=new_id(),
                        provider="p",
                        model="m",
                        role=rng.choice(list(ModelRole)),
                        classification=Classification.PUBLIC,
                        input_tokens=rng.randrange(5000),
                        output_tokens=rng.randrange(500),
                        cost_usd=rng.random(),
                    ),
                    session_id=sid,
                    actor=Actor.AGENT,
                )
            elif kind == "perm":
                store.append(
                    ev.PermissionDecided(
                        request_id=new_id(),
                        permission=rng.choice(list(PermissionClass)),
                        target="/data",
                        granted=rng.random() < 0.8,
                        scope=rng.choice(list(PermissionScope)),
                    ),
                    session_id=sid,
                    actor=Actor.USER,
                )
                store.append(
                    ev.ToolCallFinished(
                        call_id=new_id(),
                        tool="fs.read",
                        result=ToolResultPart(call_id="c", content=[TextPart(text="ok")]),
                    ),
                    session_id=sid,
                    actor=Actor.TOOL,
                )
            elif kind == "error":
                store.append(
                    ev.ErrorOccurred(
                        where="routing", error="denied", policy_violation=rng.random() < 0.5
                    ),
                    session_id=sid,
                    actor=Actor.SYSTEM,
                )
            elif kind == "title":
                store.append(
                    ev.SessionTitleSet(title=f"title-{rng.randrange(9)}"),
                    session_id=sid,
                    actor=Actor.USER,
                )
    return created
