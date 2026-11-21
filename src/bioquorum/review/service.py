"""Run a review and record it as events (ARCHITECTURE.md §6.5, §5.7).

Order: review_started -> claim_extracted (one per claim) -> review_finding (one per
finding) -> claim_status_changed (bindings, then review) -> review_finished.

Bindings are resolved first (ADR-014): a claim whose bindings all hold, with nothing left
unbound, becomes ``grounded``; a failed binding is an ERROR finding (check ``binding``).
Checks then see the rendered text. Any ERROR finding makes a claim ``disputed``; a passing
review lifts ``grounded`` to ``reviewed`` and leaves an unbound claim ``unverified``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, Classification, Severity
from bioquorum.core.ids import new_id
from bioquorum.core.refs import CitationRef, ClaimRef, QuoteRef, RunRef, ValueRef
from bioquorum.core.status import StatusTrigger, VerificationStatus, aggregate, transition
from bioquorum.review.binding import Grounding, ground_claim, render
from bioquorum.review.checks.absence import NAME as NEGATIVE_SCOPE
from bioquorum.review.checks.base import Check, Finding
from bioquorum.review.checks.citations import CITATION_UNAVAILABLE
from bioquorum.review.checks.supports import CITATION_SUPPORT_UNAVAILABLE
from bioquorum.review.evidence import ClaimInput, Evidence, cited_keys, normalise_key
from bioquorum.storage.events import EventStore

DETERMINISTIC = "deterministic"
BINDING_CHECK = "binding"


@dataclass
class ReviewedClaim:
    claim: ClaimInput
    claim_id: str
    status: VerificationStatus
    findings: list[Finding] = field(default_factory=list)
    grounding: Grounding = field(default_factory=Grounding)


@dataclass
class ReviewResult:
    review_id: str
    claims: list[ReviewedClaim]
    errors: list[str] = field(default_factory=list)  # checks that could not run
    evidence: Evidence | None = None  # what the claims were judged against

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
    extraction_error: str | None = None,
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
        grounding = ground_claim(c.text, evidence)
        store.append(
            ev.ClaimExtracted(
                claim_id=claim_id,
                source_event_id=target_event_id,
                text=c.text,
                kind=c.kind,
                refs=[*_refs(c), *grounding.refs],
            ),
            session_id=session_id,
            actor=Actor.REVIEWER,
            causation_id=started.id,
        )
        reviewed.append(ReviewedClaim(c, claim_id, VerificationStatus.UNVERIFIED, [], grounding))
    by_index = {r.claim.index: r for r in reviewed}
    n_findings = 0

    def record(f: Finding, model: str) -> None:
        nonlocal n_findings
        target = by_index.get(f.claim_index)
        if target is None:
            return
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
                reviewer_model=model,
            ),
            session_id=session_id,
            actor=Actor.REVIEWER,
            causation_id=started.id,
        )

    for r in reviewed:
        for b in r.grounding.bindings:
            if not b.ok:
                text = f"{b.raw[:120]}: {b.problem}"
                record(Finding(r.claim.index, BINDING_CHECK, Severity.ERROR, text), DETERMINISTIC)

    # Checks read the claim with values filled in at full precision (the display format may
    # round) and quotes spelled out; runs behind bound values count as the claim's runs.
    rendered = [
        replace(
            c,
            text=render(c.text, evidence, exact=True),
            quote_refs=tuple(ref for ref in r.grounding.refs if isinstance(ref, QuoteRef)),
            run_refs=tuple(
                dict.fromkeys(
                    [
                        *c.run_refs,
                        *(ref.run_id for ref in r.grounding.refs if isinstance(ref, ValueRef)),
                    ]
                )
            ),
        )
        for c, r in zip(claims, reviewed, strict=True)
    ]
    errors = (
        []
        if claims
        else [extraction_error or "claim_extraction: the reviewer returned no checkable claims"]
    )
    if not any(check.name == "citation_supports" for check in checks):
        for claim in rendered:
            if claim.quote_refs or cited_keys(claim.text):
                record(
                    Finding(
                        claim.index,
                        CITATION_SUPPORT_UNAVAILABLE,
                        Severity.WARNING,
                        "Citation support was not checked.",
                    ),
                    DETERMINISTIC,
                )
    for check in checks:
        try:
            findings = await check.run(rendered, evidence)
        except Exception as exc:  # a broken model check must not hide deterministic results
            errors.append(f"{check.name}: {type(exc).__name__}")
            continue
        model = getattr(check, "reviewer_model", None) if check.needs_model else DETERMINISTIC
        for f in findings:
            record(f, model or reviewer_model)

    def move(r: ReviewedClaim, trigger: StatusTrigger, reason: str) -> None:
        new = transition(r.status, trigger)
        store.append(
            ev.ClaimStatusChanged(
                claim_id=r.claim_id,
                from_status=r.status,
                to_status=new,
                trigger=trigger,
                reason=reason[:500],
            ),
            session_id=session_id,
            actor=Actor.REVIEWER,
            causation_id=started.id,
        )
        r.status = new

    for r in reviewed:
        if any(f.check == NEGATIVE_SCOPE for f in r.findings):
            continue
        state = r.grounding.state
        if state == "grounded":
            move(r, StatusTrigger.BINDINGS_RESOLVED, f"{len(r.grounding.bindings)} bindings hold")
        elif state == "failed":
            bad = [b.raw[:60] for b in r.grounding.bindings if not b.ok]
            move(r, StatusTrigger.BINDINGS_FAILED, "unresolved: " + ", ".join(bad))
        failed = any(f.severity is Severity.ERROR for f in r.findings)
        if not failed and (
            errors
            or any(
                f.check in (CITATION_UNAVAILABLE, CITATION_SUPPORT_UNAVAILABLE) for f in r.findings
            )
        ):
            continue
        trigger = StatusTrigger.REVIEW_FAILED if failed else StatusTrigger.REVIEW_PASSED
        reason = "; ".join(f"{f.check}: {f.evidence}" for f in r.findings)
        move(r, trigger, reason or "all checks passed")
    store.append(
        ev.ReviewFinished(
            review_id=review_id,
            finding_count=n_findings,
            claim_count=len(reviewed),
            limitations=list(
                dict.fromkeys(
                    [
                        *errors,
                        *(
                            f.check
                            for r in reviewed
                            for f in r.findings
                            if f.check in (CITATION_UNAVAILABLE, CITATION_SUPPORT_UNAVAILABLE)
                        ),
                    ]
                )
            ),
        ),
        session_id=session_id,
        actor=Actor.REVIEWER,
        causation_id=started.id,
    )

    return ReviewResult(review_id, reviewed, errors, evidence)
