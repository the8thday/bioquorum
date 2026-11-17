"""value_match: numbers in a claim must appear in the evidence.

Three rules, all generic:
1. Every standalone number in the claim must occur in some evidence text (run code and
   stdout, citation titles and abstracts), allowing rounding to the claim's precision.
2. Units must agree: a claim "38.5%" is not supported by evidence that only ever says
   "38.5 mg/dL".
3. Table rows bind values to labels: if a value comes from a stdout row "CD4 T 34.2" and
   the claim puts a *different* row's label right before that value, the labels are
   swapped.
Small integers (0-10) are ignored unless they carry a unit: they are mostly counts of
things named in the text ("two trials", "4 samples").
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from bioquorum.core.enums import Severity
from bioquorum.review.checks.base import Finding
from bioquorum.review.evidence import ClaimInput, Evidence
from bioquorum.review.text import Number, normalise, numbers, same_value, tokens

_LABEL_WINDOW = 60  # characters before a number in which a row label counts as attached
_CONFLICTING_UNITS = {
    "mg/dl",
    "mmol/l",
    "mg",
    "kg",
    "g",
    "ml",
    "mm",
    "cm",
    "fold",
    "years",
    "months",
    "weeks",
    "days",
    "hours",
}


_TABLE_ROW = re.compile(
    r"\s*([A-Za-z][A-Za-z0-9+\-]*)(?:[ \t]+[A-Za-z][A-Za-z0-9+\-]*)*(?:[ \t]+-?\d[\d.,e-]*%?)+\s*$"
)


@dataclass(frozen=True)
class _Row:
    label: str  # first token of a stdout line, e.g. "CD4"
    numbers: tuple[float, ...]
    table: int  # rows in one block of consecutive "label value" lines share a table id


def _rows(evidence: Evidence) -> list[_Row]:
    rows: list[_Row] = []
    table = 0
    for run in evidence.runs:
        if not run.succeeded:
            continue
        table += 1
        for line in run.stdout.splitlines():
            # a table row: label words, then numbers only ("CD4 T   34.2", "E4  310  71.2");
            # "PValue<0.05: 212 FDR<0.05: 9" is a key-value summary, not a row
            m = _TABLE_ROW.match(line)
            nums = tuple(n.value for n in numbers(line))
            if m and nums:
                rows.append(_Row(m.group(1), nums, table))
            else:
                table += 1  # a header or blank line ends the block
    return rows


_FOLLOWING = re.compile(r"^\s*%?\s*(?:in|for|among|within)\s+(?:the\s+)?(\S+)")


def _attached_label(text: str, n: Number, labels: set[str]) -> str | None:
    """Which table label the claim attaches ``n`` to.

    "71.2 in E3 carriers": the label right after a preposition. Otherwise the nearest label
    before the number ("CD14 monocytes are the largest population at 34.2%")."""
    after = _FOLLOWING.match(text[n.end : n.end + 40])
    if after:
        word = re.sub(r"[^A-Za-z0-9+\-]", "", after.group(1))
        if word in labels:
            return word
    before = [t for t, _ in tokens(text[max(0, n.start - _LABEL_WINDOW) : n.start])]
    return next((t for t in reversed(before) if t in labels), None)


def _norm(label: str) -> str:
    return re.sub(r"[^a-z0-9]", "", label.lower())


def _derived(evidence: Evidence) -> list[float]:
    """Sums and differences of two numbers printed by the same run (totals, deltas)."""
    out: list[float] = []
    for r in evidence.runs:
        if not r.succeeded:
            continue
        vals = [n.value for n in numbers(normalise(r.stdout))][:60]
        out += [a + b for i, a in enumerate(vals) for b in vals[i + 1 :]]
        out += [abs(a - b) for i, a in enumerate(vals) for b in vals[i + 1 :]]
    return out


def _evidence_numbers(evidence: Evidence) -> list[Number]:
    texts = [f"{r.code}\n{r.stdout}" for r in evidence.runs if r.succeeded]
    texts += [c.text for c in evidence.citations]
    return [n for t in texts for n in numbers(normalise(t))]


_COMPARISON = re.compile(
    r"(?:[<>≤≥]=?|\b(?:below|above|under|over|less than|more than|fewer than|greater than|at least|at most))\s*$",
    re.I,
)


def _skippable(n: Number) -> bool:
    return not n.unit and n.decimals == 0 and 0 <= n.value <= 10


class ValueMatch:
    name = "value_match"
    needs_model = False

    async def run(self, claims: Sequence[ClaimInput], evidence: Evidence) -> list[Finding]:
        pool = _evidence_numbers(evidence)
        derived = _derived(evidence)
        rows = _rows(evidence)
        findings: list[Finding] = []
        for claim in claims:
            text = normalise(claim.text)
            for n in numbers(text):
                if _skippable(n):
                    continue
                matches = [e for e in pool if same_value(n, e)]
                if not matches and any(
                    round(d, n.decimals if n.decimals < 99 else 6) == n.value for d in derived
                ):
                    continue  # e.g. 5,981 cells = 2,877 + 3,104 in the same output
                if not matches:
                    findings.append(
                        Finding(
                            claim.index,
                            self.name,
                            Severity.ERROR,
                            f"{n.text}{n.unit if n.unit == '%' else ''} does not appear in any run output or source",
                        )
                    )
                    continue
                if (
                    n.unit == "%"
                    and n.value != 0
                    and all(m.unit in _CONFLICTING_UNITS for m in matches)
                ):
                    units = ", ".join(sorted({m.unit for m in matches}))
                    findings.append(
                        Finding(
                            claim.index,
                            self.name,
                            Severity.ERROR,
                            f"{n.text}% is reported in the evidence as {n.text} {units}, not a percentage",
                        )
                    )
                    continue
                swap = self._label_swap(text, n, rows)
                if swap:
                    findings.append(Finding(claim.index, self.name, Severity.ERROR, swap))
        return findings

    @staticmethod
    def _label_swap(text: str, n: Number, rows: list[_Row]) -> str | None:
        owners = [r for r in rows if n.value in r.numbers]
        if len({(r.label, r.table) for r in owners}) != 1:
            return None  # value not in a table, or ambiguous
        owner = owners[0]
        table_labels = {r.label for r in rows if r.table == owner.table}
        if _COMPARISON.search(text[max(0, n.start - 15) : n.start]):
            return None  # a threshold ("p < 0.05"), not a value read from the table
        named = {t for t, _ in tokens(text) if t in table_labels}
        if len(named) < 2:
            return None  # the claim is not reading this table row by row
        attached = _attached_label(text, n, table_labels)
        if attached is None or _norm(attached) == _norm(owner.label):
            return None
        if attached != owner.label:
            return (
                f"{n.text} belongs to row '{owner.label}' in the run output, "
                f"but the claim attaches it to '{attached}'"
            )
        return None
