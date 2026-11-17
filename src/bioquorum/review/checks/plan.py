"""plan_complete: a claim of completeness needs every approved plan step to be done.

A step is done when marked so (``plan_step_done``) or, when unmarked, when its content
words are covered: by a successful run for computational steps, or by the answer text for
writing steps. A plan claim is flagged if any step is not done.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from bioquorum.core.enums import ClaimKind, Severity
from bioquorum.review.checks.base import Finding
from bioquorum.review.evidence import ClaimInput, Evidence, PlanStep
from bioquorum.review.text import (
    ACKNOWLEDGES,
    GENERIC_VERBS,
    NUMBER_WORDS,
    mentions,
    proper_names,
    stem,
    stems,
    words,
)

_COMPUTE = re.compile(
    r"\b(run|compute|calculate|integrate|cluster|filter|fit|pull|query|retrieve|download|"
    r"normali[sz]e|annotate|test|estimate|pool|regress|align|quantif|score|detect|remove|"
    r"count|load)\w*",
    re.I,
)
# Saying a missing step *was done*; "the plan specified Harmony" is only a description.
_DONE = re.compile(
    r"\b(integrated|performed|completed|ran|applied|computed|carried out|did|done|checked|"
    r"counted|queried|summari[sz]ed|covered|included|addressed|finished)\b",
    re.I,
)
_COMPLETENESS = re.compile(r"\b(all|every|complete[sd]?|covers?|covered|as planned)\b", re.I)
_COVERAGE = 1 / 3  # share of a step's content stems that must be found


def _sources(step: PlanStep, evidence: Evidence) -> str:
    runs = " ".join(f"{r.code}\n{r.stdout}" for r in evidence.runs if r.succeeded)
    # computational steps need a successful run; writing steps may be done in the answer
    return runs if _COMPUTE.search(step.text) else f"{evidence.answer}\n{runs}"


def _step_done(step: PlanStep, evidence: Evidence) -> bool:
    if step.done is not None:
        return step.done
    text = _sources(step, evidence)
    topic = evidence.question
    named = [n for n in proper_names(step.text) if not mentions(n, topic)]
    if named:  # a named tool or database must actually show up
        return all(mentions(n, text) for n in named)
    own = {
        stem(w)
        for w in words(step.text) - words(topic)
        if w not in GENERIC_VERBS and w not in NUMBER_WORDS
    }
    if not own:
        return True
    return len(own & stems(text)) / len(own) >= _COVERAGE


class PlanComplete:
    name = "plan_complete"
    needs_model = False

    async def run(self, claims: Sequence[ClaimInput], evidence: Evidence) -> list[Finding]:
        missing = [s for s in evidence.plan if not _step_done(s, evidence)]
        if not missing:
            return []
        steps = "; ".join(s.text for s in missing)
        missing_names = [n for s in missing for n in proper_names(s.text)]
        findings: list[Finding] = []
        for c in claims:
            if c.kind is not ClaimKind.PLAN or ACKNOWLEDGES.search(c.text):
                continue  # "the gnomAD step was not run" is honest, not a completion claim
            about_missing = any(mentions(n, c.text) for n in missing_names) and bool(
                _DONE.search(c.text)
            )
            if _COMPLETENESS.search(c.text) or about_missing:
                findings.append(
                    Finding(
                        c.index,
                        self.name,
                        Severity.ERROR,
                        f"approved plan step(s) not done: {steps}",
                    )
                )
        return findings
