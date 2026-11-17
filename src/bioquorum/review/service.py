"""Run a review and record it as events (ARCHITECTURE.md §6.5, §5.7).

Order: review_started -> claim_extracted (one per claim) -> review_finding (one per
finding) -> review_finished -> claim_status_changed (one per claim). Any ERROR finding
on a claim makes it ``disputed``; otherwise it becomes ``reviewed``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, Classification, Severity
from bioquorum.core.ids import new_id
from bioquorum.core.refs import CitationRef, ClaimRef, RunRef
from bioquorum.core.status import StatusTrigger, VerificationStatus, aggregate, transition
from bioquorum.review.checks.base import Check, Finding
from bioquorum.review.evidence import ClaimInput, Evidence, cited_keys, normalise_key
from bioquorum.storage.events import EventStore

DETERMINISTIC = "deterministic"


@dataclass
class ReviewedClaim:
    claim: ClaimInput
    claim_id: str
    status: VerificationStatus
    findings: list[Finding] = field(default_factory=list)


@dataclass
class ReviewResult:
    review_id: str
    claims: list[ReviewedClaim]
    errors: list[str] = field(default_factory=list)  # checks that could not run

    @property
    def status(self) -> VerificationStatus:
        return aggregate(c.status for c in self.claims)

    @property
    def findings(self) -> list[Finding]:
        return [f for c in self.claims for f in c.findings]


def _refs(claim: ClaimInput) -> list[ClaimRef]:
    refs: list[ClaimRef] = [RunRef(run_id=r) for r in claim.all_run_refs()]
    for key in cited_keys(claim.text):
        k = normalise_key(key)
        if k.startswith("PMID:"):
            refs.append(CitationRef(pmid=k[5:]))
        elif k.startswith("DOI:"):
            refs.append(CitationRef(doi=k[4:]))
        else:
            refs.append(CitationRef(title=key))
    return refs


async def review(
    store: EventStore,
    *,
    session_id: str,
    target_event_id: str,
    claims: Sequence[ClaimInput],
    evidence: Evidence,
    checks: Sequence[Check],
    reviewer_model: str = DETERMINISTIC,
    classification: Classification = Classification.INTERNAL,
) -> ReviewResult:
    review_id = new_id()
    started = store.append(
        ev.ReviewStarted(
            review_id=review_id, reviewer_model=reviewer_model, target_event_id=target_event_id
        ),
        session_id=session_id,
        actor=Actor.REVIEWER,
        causation_id=target_event_id,
    )
    reviewed: list[ReviewedClaim] = []
    for c in claims:
        claim_id = c.claim_id or new_id()
        store.append(
            ev.ClaimExtracted(
                claim_id=claim_id,
                source_event_id=target_event_id,
                text=c.text,
                kind=c.kind,
                refs=_refs(c),
            ),
            session_id=session_id,
            actor=Actor.REVIEWER,
            causation_id=started.id,
        )
        reviewed.append(ReviewedClaim(c, claim_id, VerificationStatus.UNVERIFIED))
    by_index = {r.claim.index: r for r in reviewed}

    errors: list[str] = []
    n_findings = 0
    for check in checks:
        try:
            findings = await check.run(claims, evidence)
        except Exception as exc:  # a broken model check must not hide deterministic results
            errors.append(f"{check.name}: {type(exc).__name__}: {exc}"[:300])
            continue
        model = getattr(check, "reviewer_model", None) if check.needs_model else DETERMINISTIC
        for f in findings:
            target = by_index.get(f.claim_index)
            if target is None:
                continue
            target.findings.append(f)
            n_findings += 1
            store.append(
                ev.ReviewFinding(
                    review_id=review_id,
                    finding_id=new_id(),
                    claim_id=target.claim_id,
                    check=f.check,
                    severity=f.severity,
                    evidence=f.evidence,
                    reviewer_model=model or reviewer_model,
                ),
                session_id=session_id,
                actor=Actor.REVIEWER,
                causation_id=started.id,
            )
    store.append(
        ev.ReviewFinished(review_id=review_id, finding_count=n_findings),
        session_id=session_id,
        actor=Actor.REVIEWER,
        causation_id=started.id,
    )
    for r in reviewed:
        failed = any(f.severity is Severity.ERROR for f in r.findings)
        trigger = StatusTrigger.REVIEW_FAILED if failed else StatusTrigger.REVIEW_PASSED
        new = transition(r.status, trigger)
        reason = "; ".join(f"{f.check}: {f.evidence}" for f in r.findings)[:500]
        store.append(
            ev.ClaimStatusChanged(
                claim_id=r.claim_id,
                from_status=r.status,
                to_status=new,
                trigger=trigger,
                reason=reason or "all checks passed",
            ),
            session_id=session_id,
            actor=Actor.REVIEWER,
            causation_id=started.id,
        )
        r.status = new
    return ReviewResult(review_id, reviewed, errors)
