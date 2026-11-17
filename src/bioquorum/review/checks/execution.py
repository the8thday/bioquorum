"""execution_evidence: a claim that something was computed needs a successful run.

A claim needs execution evidence when it is an ``execution`` claim, or when it reports a
first-person action ("I queried / ran / pooled / counted ..."). Runs are linked to the
claim by shared content words, ignoring words from the user's question (topic words such
as the gene name appear everywhere and identify nothing). If the best-matching run
failed, or no run matches, the claim is unsupported.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from bioquorum.core.enums import ClaimKind, Severity
from bioquorum.review.checks.base import Finding
from bioquorum.review.evidence import ClaimInput, Evidence, RunEvidence
from bioquorum.review.text import Number, normalise, numbers, same_value, stems

_ACTION = re.compile(
    r"\bI\s+(?:also\s+|then\s+|first\s+)?(?:queried|ran|run|pooled|computed|calculated|counted|"
    r"fitted|fit|filtered|performed|executed|searched|pulled|downloaded|clustered|integrated|"
    r"tested|estimated|modelled|modeled|selected|removed|normalised|normalized)\b",
    re.I,
)


_MODAL = re.compile(r"\b(could|would|should|might|may|future|next step|would need)\b", re.I)
_REPORTS_FAILURE = re.compile(
    r"\b(failed|fails|error|errored|exception|timed? ?out|timeout|could not|did not complete|"
    r"was not completed|unable|crashed|missing module|not (?:be )?(?:computed|quantified|obtained|run))\b",
    re.I,
)


def _result_numbers(text: str) -> list[Number]:
    return [n for n in numbers(text) if n.unit or n.decimals > 0 or n.value > 10]


def needs_execution(claim: ClaimInput) -> bool:
    """Explicit run references and first-person actions always; execution claims only when
    they report results ("These markers suggest CD4 T cells" interprets a run)."""
    if claim.all_run_refs() or _ACTION.search(claim.text):
        return True
    return claim.kind is ClaimKind.EXECUTION and bool(_result_numbers(normalise(claim.text)))


def best_run(claim: ClaimInput, evidence: Evidence) -> tuple[RunEvidence | None, set[str]]:
    topic = stems(evidence.question)
    wanted = stems(claim.text) - topic
    reported = _result_numbers(normalise(claim.text))
    best: tuple[int, RunEvidence | None, set[str]] = (0, None, set())
    for run in evidence.runs:
        body = normalise(f"{run.code}\n{run.stdout}")
        shared = wanted & stems(body)
        printed = numbers(body)
        same_numbers = sum(1 for r in reported if any(same_value(r, o) for o in printed))
        score = len(shared) + 2 * same_numbers  # matching values identify a run best
        # a failed run wins ties: if it is just as relevant, the claim leans on it
        if score > best[0] or (score == best[0] > 0 and not run.succeeded):
            best = (score, run, shared)
    return best[1], best[2]


class ExecutionEvidence:
    name = "execution_evidence"
    needs_model = False

    async def run(self, claims: Sequence[ClaimInput], evidence: Evidence) -> list[Finding]:
        findings: list[Finding] = []
        for claim in claims:
            if not needs_execution(claim):
                continue
            refs = claim.all_run_refs()
            if refs:
                findings.extend(self._explicit(claim, refs, evidence))
                continue
            run, shared = best_run(claim, evidence)
            if run is None:
                findings.append(
                    Finding(
                        claim.index,
                        self.name,
                        Severity.ERROR,
                        "claims a computation, but no run in the session matches it",
                    )
                )
            elif not run.succeeded:
                findings.append(
                    Finding(
                        claim.index,
                        self.name,
                        Severity.ERROR,
                        f"relies on run {run.run_id}, which failed "
                        f"(matched on: {', '.join(sorted(shared)[:5])})",
                    )
                )
            else:
                reported = _result_numbers(normalise(claim.text))
                outputs = numbers(normalise(f"{run.code}\n{run.stdout}"))
                if reported and not any(same_value(r, o) for r in reported for o in outputs):
                    findings.append(
                        Finding(
                            claim.index,
                            self.name,
                            Severity.ERROR,
                            f"reports {', '.join(r.text for r in reported[:4])}, but the closest "
                            f"run ({run.run_id}) printed none of these values",
                        )
                    )
        return findings

    @staticmethod
    def _hypothetical(claim: ClaimInput) -> bool:
        """ "A further attempt could rerun the model" proposes work; it reports no result."""
        return bool(_MODAL.search(claim.text)) and not _result_numbers(normalise(claim.text))

    def _explicit(
        self, claim: ClaimInput, refs: tuple[str, ...], evidence: Evidence
    ) -> list[Finding]:
        out: list[Finding] = []
        for ref in refs:
            run = evidence.run(ref)
            if run is None:
                out.append(
                    Finding(
                        claim.index,
                        self.name,
                        Severity.ERROR,
                        f"cites run {ref}, which does not exist in this session",
                    )
                )
            elif not run.succeeded and not (
                _REPORTS_FAILURE.search(claim.text) or self._hypothetical(claim)
            ):
                # "run r1 failed, so no pooled HR was computed" is honest; flag the rest
                out.append(
                    Finding(
                        claim.index, self.name, Severity.ERROR, f"cites run {ref}, which failed"
                    )
                )
        return out
