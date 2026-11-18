"""What a review is judged against (ARCHITECTURE.md §6.5).

Checks see only :class:`Evidence` and :class:`ClaimInput`, never storage or models, so the
same checks run on a live session (``from_events``) and on the injected-error corpus
(``from_case``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from bioquorum.core import events as ev
from bioquorum.core.enums import ClaimKind
from bioquorum.core.messages import TextPart
from bioquorum.core.refs import NamedValue

KERNEL_TOOLS = frozenset({"python_execute"})
"""Their output is not a quotable source: text the kernel prints is not evidence (ADR-014)."""


@dataclass(frozen=True)
class RunEvidence:
    run_id: str
    succeeded: bool
    code: str
    stdout: str


@dataclass(frozen=True)
class CitationEvidence:
    key: str  # what answers cite: "c1" in the corpus, "PMID:123" / "DOI:10.x/y" live
    title: str = ""
    abstract: str = ""
    doi: str | None = None
    pmid: str | None = None

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.abstract}"


@dataclass(frozen=True)
class PlanStep:
    text: str
    done: bool | None = None  # None: unknown, infer from runs and the answer


@dataclass(frozen=True)
class DeclaredValue:
    """A ``bq.value`` declaration and the run that made it (ADR-014)."""

    value: NamedValue
    run_id: str


@dataclass(frozen=True)
class Source:
    """A tool result that quotes may come from, with its alias (``s1``, ``s2`` ...)."""

    alias: str
    call_id: str
    tool: str
    text: str


def result_text(part) -> str:
    return "\n".join(c.text for c in part.content if isinstance(c, TextPart))


def is_source(p: ev.ToolCallFinished) -> bool:
    return p.tool not in KERNEL_TOOLS and not p.result.is_error


def source_aliases(events: Iterable[ev.Event]) -> dict[str, str]:
    """call_id -> alias for every quotable tool result, numbered in event order. The agent
    shows the same aliases to the model, so both sides agree without storing them."""
    out: dict[str, str] = {}
    for e in events:
        if e.type != ev.ToolCallFinished.event_type:
            continue
        p = e.parsed()
        assert isinstance(p, ev.ToolCallFinished)
        if is_source(p) and p.call_id not in out:
            out[p.call_id] = f"s{len(out) + 1}"
    return out


@dataclass
class Evidence:
    question: str = ""
    runs: list[RunEvidence] = field(default_factory=list)
    citations: list[CitationEvidence] = field(default_factory=list)
    plan: list[PlanStep] = field(default_factory=list)
    answer: str = ""
    values: dict[str, DeclaredValue] = field(default_factory=dict)
    """Named values of successful runs; for a repeated name, the latest declaration."""
    sources: list[Source] = field(default_factory=list)

    def run(self, run_id: str) -> RunEvidence | None:
        return next((r for r in self.runs if r.run_id == run_id), None)

    def citation(self, key: str) -> CitationEvidence | None:
        k = normalise_key(key)
        return next((c for c in self.citations if normalise_key(c.key) == k), None)

    # ------------------------------------------------------------- builders

    @classmethod
    def from_case(cls, case: dict[str, Any]) -> Evidence:
        e = case.get("evidence") or {}
        return cls(
            question=case.get("question", ""),
            runs=[
                RunEvidence(
                    run_id=r["run_id"],
                    succeeded=r.get("status") == "succeeded",
                    code=r.get("code", ""),
                    stdout=r.get("stdout", ""),
                )
                for r in e.get("runs") or []
            ],
            citations=[
                CitationEvidence(
                    key=c["key"],
                    title=c.get("title", ""),
                    abstract=c.get("abstract", ""),
                    doi=c.get("doi"),
                    pmid=str(c["pmid"]) if c.get("pmid") else None,
                )
                for c in e.get("citations") or []
            ],
            plan=[PlanStep(text=s) for s in e.get("plan") or []],
            answer=case.get("answer", ""),
        )

    @classmethod
    def from_events(cls, events: Iterable[ev.Event], *, answer: str = "") -> Evidence:
        """Runs from run_* events; citations from identifiers seen in tool results; plan
        steps from plan_* events; named values from run_finished; quotable sources from
        non-kernel tool results."""
        runs: dict[str, dict[str, Any]] = {}
        values: dict[str, DeclaredValue] = {}
        sources: list[Source] = []
        citations: dict[str, CitationEvidence] = {}
        plan: dict[str, list[str]] = {}
        done: dict[str, set[int]] = {}
        question = ""
        for e in events:
            p = e.parsed()
            if isinstance(p, ev.UserMessage) and not question:
                question = p.message.text()
            elif isinstance(p, ev.RunStarted):
                runs[p.run_id] = {"code": p.code, "stdout": [], "ok": False}
            elif isinstance(p, ev.RunOutput) and p.run_id in runs and p.text:
                runs[p.run_id]["stdout"].append(p.text)
            elif isinstance(p, ev.RunFinished) and p.run_id in runs:
                runs[p.run_id]["ok"] = True
                for nv in p.values:
                    values.pop(nv.name, None)  # re-insert: keep "latest" also in order
                    values[nv.name] = DeclaredValue(nv, p.run_id)
            elif isinstance(p, ev.ToolCallFinished) and p.tool not in KERNEL_TOOLS:
                text = result_text(p.result)
                if is_source(p) and all(s.call_id != p.call_id for s in sources):
                    sources.append(Source(f"s{len(sources) + 1}", p.call_id, p.tool, text))
                for cit in citations_in_text(text):
                    citations[cit.key] = _richer(citations.get(cit.key), cit)
            elif isinstance(p, ev.PlanProposed):
                plan[p.plan_id] = list(p.steps)
            elif isinstance(p, ev.PlanStepDone):
                done.setdefault(p.plan_id, set()).add(p.step_index)
        steps = [
            PlanStep(text=s, done=(i in done.get(pid, set())) or None)
            for pid, items in plan.items()
            for i, s in enumerate(items)
        ]
        return cls(
            question=question,
            runs=[
                RunEvidence(rid, r["ok"], r["code"], "".join(r["stdout"]))
                for rid, r in runs.items()
            ],
            citations=list(citations.values()),
            plan=steps,
            answer=answer,
            values=values,
            sources=sources,
        )


@dataclass(frozen=True)
class ClaimInput:
    index: int
    text: str
    kind: ClaimKind
    claim_id: str | None = None
    run_refs: tuple[str, ...] = ()
    """Runs the claim explicitly relies on (Claim refs, or "run r2" in its text)."""

    def all_run_refs(self) -> tuple[str, ...]:
        named = re.findall(r"\b[Rr]un\s+([A-Za-z0-9]+)\b", self.text)
        refs = [
            *self.run_refs,
            *(n for n in named if re.fullmatch(r"r\d+|[0-9A-HJKMNP-TV-Z]{26}", n)),
        ]
        return tuple(dict.fromkeys(refs))


# ----------------------------------------------------------------- citation identifiers

_PMID = re.compile(r"\bPMID[:\s]*([0-9]{5,9})\b", re.I)
_DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\]\)>,;\"']+)", re.I)
_KEY = re.compile(r"\[([^\[\]]+)\]")


def normalise_key(key: str) -> str:
    k = key.strip()
    m = _PMID.fullmatch(k) or re.fullmatch(r"(?i)pmid[:\s]*(\d+)", k)
    if m:
        return f"PMID:{m.group(1)}"
    if k.lower().startswith("doi:"):
        k = k[4:]
    if _DOI.fullmatch(k):
        return f"DOI:{k.rstrip('.').lower()}"
    return k.lower()


def cited_keys(text: str) -> list[str]:
    """Keys an answer cites: ``[c1]``, ``[c1, c2]``, ``[PMID:123]``, ``[DOI:10.x/y]``."""
    keys: list[str] = []
    for group in _KEY.findall(text):
        for part in re.split(r"[;,]", group):
            part = part.strip()
            if (
                re.fullmatch(r"c\d+", part)
                or _PMID.fullmatch(part)
                or part.lower().startswith(("doi:", "pmid"))
            ):
                keys.append(part)
    return keys


_JSON_PMID = re.compile(r'"pmid"\s*:\s*"?(\d{5,9})"?', re.I)
_JSON_DOI = re.compile(r'"doi"\s*:\s*"(10\.\d{4,9}/[^"]+)"', re.I)
_JSON_TITLE = re.compile(r'"title"\s*:\s*"([^"]{5,400})"')
_MAX_BODY = 6000


def _richer(old: CitationEvidence | None, new: CitationEvidence) -> CitationEvidence:
    """Keep the most informative record per identifier: a full record beats a search-list
    line; two full records (e.g. summary + full text) are merged."""
    if old is None:
        return new
    if bool(new.title) != bool(old.title):
        return new if new.title else old
    if not new.title:  # two list lines: keep the longer
        return new if len(new.abstract) > len(old.abstract) else old
    if new.abstract in old.abstract:
        return old
    merged = f"{old.abstract}\n\n{new.abstract}"[:_MAX_BODY]
    return CitationEvidence(old.key, old.title, merged, old.doi or new.doi, old.pmid or new.pmid)


def citations_in_text(text: str) -> list[CitationEvidence]:
    """Literature records in a tool result.

    One record (a single PMID, as from ``get article``): title + the text as context, keyed
    by PMID and DOI. Several (a search list): one entry per identifier whose context is only
    the line it appears on, so one paper's abstract is never another paper's evidence."""
    pmids = list(dict.fromkeys([*_PMID.findall(text), *_JSON_PMID.findall(text)]))
    dois = list(
        dict.fromkeys(d.rstrip(".") for d in [*_DOI.findall(text), *_JSON_DOI.findall(text)])
    )
    if len(pmids) == 1 and len(dois) <= 1:
        pmid, doi = pmids[0], (dois[0] if dois else None)
        title = next(
            (ln.lstrip("# ").strip() for ln in text.splitlines() if ln.startswith("#")), ""
        )
        if not title:
            m = _JSON_TITLE.search(text)
            title = m.group(1) if m else ""
        body = text[:_MAX_BODY]
        found = [CitationEvidence(f"PMID:{pmid}", title, body, doi, pmid)]
        if doi:
            found.append(CitationEvidence(f"DOI:{doi.lower()}", title, body, doi, pmid))
        return found
    lines = text.splitlines()

    def line_of(ident: str) -> str:
        return next((ln.strip() for ln in lines if ident in ln), "")[:500]

    found = [CitationEvidence(f"PMID:{p}", "", line_of(p), None, p) for p in pmids]
    found += [CitationEvidence(f"DOI:{d.lower()}", "", line_of(d), d, None) for d in dois]
    return found
