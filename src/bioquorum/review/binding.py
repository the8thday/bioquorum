"""Evidence binding (ADR-014): resolve ``{{name}}`` and ``[@source "quote"]`` without a model.

- ``{{name}}`` / ``{{name:spec}}``: a value declared with ``bq.value`` in a successful run of
  this session. The reader sees the stored value, formatted here, never the model's digits.
- ``[@s3 "quote"]`` / ``[@PMID:123 "quote"]``: the quote must occur verbatim (whitespace
  normalised) in a non-kernel tool result: the one with that alias, or one that mentions
  that identifier.

A claim is ``grounded`` when it has at least one binding, every binding resolves, and no
bare number or citation is left outside the bindings. Rules are in ADR-014's design note.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from functools import cache
from typing import Literal

from bioquorum.core.refs import QuoteRef, ValueRef
from bioquorum.review.evidence import (
    Evidence,
    Source,
    citations_in_text,
    cited_keys,
    normalise_key,
    source_documents,
)
from bioquorum.review.text import Number, numbers

MIN_QUOTE = 8  # characters after whitespace normalisation

_VALUE = re.compile(r"\{\{\s*([A-Za-z][A-Za-z0-9_]{0,63})\s*(?::([^{}]*))?\}\}")
# The quote runs to the first closing quote that is followed by "]", so a quote may itself
# contain straight quotes (JSON from database tools).
_QUOTE = re.compile(r"\[@([^\s\[\]\"\u201c\u201d]+)\s+(?:\"(.*?)\"|\u201c(.*?)\u201d)\s*\]", re.S)
_MARKER_START = re.compile(r"\[@")
_SPEC = re.compile(r"[0-9.,+\- eEfFgG%]*")
_ALIAS = re.compile(r"s\d+")
_LIST_PREFIX = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+")
_RUN_REF = re.compile(r"\b[Rr]un\s+[0-9A-HJKMNP-TV-Z]{26}\b|\b[Rr]un\s+r\d+\b")

Kind = Literal["value", "quote"]


@dataclass(frozen=True)
class Binding:
    kind: Kind
    raw: str
    start: int
    end: int
    ok: bool
    rendered: str  # what the reader sees in place of the marker
    problem: str = ""
    ref: ValueRef | QuoteRef | None = None
    source: str | None = None  # quotes: as written ("s3", "PMID:123")
    exact: str = ""  # values: the recorded value at full precision, for checks


def _norm(text: str) -> str:
    return " ".join(text.split())


def format_value(value: int | float | str, spec: str | None = None) -> str:
    """Default: ints as is, floats to 4 significant digits (large ones as integers),
    text as is. ``spec`` is a Python format spec; a bad one raises ValueError."""
    if spec:
        if not _SPEC.fullmatch(spec):
            raise ValueError(f"unsupported format {spec!r}")
        return format(value, spec)
    if isinstance(value, float):
        if 1e4 <= abs(value) < 1e15:
            return f"{value:.0f}"
        return f"{value:.4g}"
    return str(value)


# ----------------------------------------------------------------- sources


@cache
def _variants(text: str) -> tuple[str, ...]:
    """Normalised forms a quote may match: the text, and for JSON its decoded strings."""
    forms = [_norm(text)]
    try:
        data = json.loads(text)
    except ValueError:
        return tuple(forms)
    leaves: list[str] = []

    def walk(x: object) -> None:
        if isinstance(x, str):
            leaves.append(x)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(data)
    forms.extend(_norm(leaf) for leaf in leaves if leaf.strip())
    return tuple(forms)


@cache
def _ids(text: str) -> frozenset[str]:
    return frozenset(normalise_key(c.key) for c in citations_in_text(text))


def _mentions(source: Source, key: str) -> bool:
    k = normalise_key(key)
    if k.startswith(("PMID:", "DOI:")):
        keys = _ids(source.text)
        return (
            k in keys
            and sum(key.startswith("PMID:") for key in keys) <= 1
            and sum(key.startswith("DOI:") for key in keys) <= 1
        )
    return key.lower() in source.text.lower()


def scoped_sources(source: Source, key: str) -> list[Source]:
    if _ALIAS.fullmatch(key):
        return [source] if key == source.alias else []
    docs = [replace(source, text=text) for text in source_documents(source.text)]
    return [doc for doc in docs if _mentions(doc, key)]


def quote_matches(source: Source, quote: str) -> bool:
    """For a delegated source, only the approved excerpt is quotable."""
    normalized = _norm(quote)
    return len(normalized) >= MIN_QUOTE and any(
        normalized in form for form in _variants(source.excerpt or source.text)
    )


# ----------------------------------------------------------------- resolution


def _value(m: re.Match[str], evidence: Evidence) -> Binding:
    name, spec = m.group(1), m.group(2)
    declared = evidence.values.get(name)
    if declared is None:
        return Binding(
            "value",
            m.group(0),
            m.start(),
            m.end(),
            False,
            m.group(0),
            f"no bq.value named {name!r} in a successful run of this session",
        )
    try:
        shown = format_value(declared.value.value, spec.strip() if spec else None)
    except (ValueError, TypeError) as exc:
        return Binding(
            "value",
            m.group(0),
            m.start(),
            m.end(),
            False,
            m.group(0),
            f"bad format for {name!r}: {exc}",
        )
    ref = ValueRef(name=name, run_id=declared.run_id)
    exact = str(declared.value.value)  # what bq.value printed in the run output
    return Binding("value", m.group(0), m.start(), m.end(), True, shown, ref=ref, exact=exact)


def _quote(m: re.Match[str], evidence: Evidence) -> Binding:
    src = m.group(1)
    quote = _norm(m.group(2) if m.group(2) is not None else m.group(3))
    shown = f'"{quote}" [{src}]'

    def fail(problem: str) -> Binding:
        return Binding("quote", m.group(0), m.start(), m.end(), False, shown, problem, None, src)

    if len(quote) < MIN_QUOTE:
        return fail(f"quote too short (minimum {MIN_QUOTE} characters)")
    if _ALIAS.fullmatch(src):
        candidates = [s for s in evidence.sources if s.alias == src]
        if not candidates:
            return fail(f"no source {src} in this session")
    else:
        candidates = [doc for source in evidence.sources for doc in scoped_sources(source, src)]
        if not candidates:
            return fail(f"no tool result in this session mentions {src}")
    hit = next((s for s in candidates if quote_matches(s, quote)), None)
    if hit is None:
        where = src if _ALIAS.fullmatch(src) else f"the results that mention {src}"
        return fail(f"quote not found verbatim in {where}")
    ref = QuoteRef(source=src, call_id=hit.call_id, quote=quote)
    return Binding("quote", m.group(0), m.start(), m.end(), True, shown, ref=ref, source=src)


def find_bindings(text: str, evidence: Evidence) -> list[Binding]:
    """Every binding marker in ``text``, in order, each resolved against ``evidence``."""
    found = [_value(m, evidence) for m in _VALUE.finditer(text)]
    quotes = [_quote(m, evidence) for m in _QUOTE.finditer(text)]
    found += quotes
    # Anything else that opens like a quote marker is malformed: fail it so it is sent back,
    # rather than letting it pass as ordinary text.
    for m in _MARKER_START.finditer(text):
        if any(q.start <= m.start() < q.end for q in quotes):
            continue
        close = text.find("]", m.start())
        end = close + 1 if close != -1 else min(len(text), m.start() + 80)
        raw = text[m.start() : end]
        problem = 'malformed quote marker; write [@source "exact words"]'
        found.append(Binding("quote", raw, m.start(), end, False, raw, problem))
    return sorted(found, key=lambda b: b.start)


def unresolved(text: str, evidence: Evidence) -> list[Binding]:
    return [b for b in find_bindings(text, evidence) if not b.ok]


def _splice(text: str, bindings: list[Binding], show: Callable[[Binding], str]) -> str:
    out, pos = [], 0
    for b in bindings:
        out += [text[pos : b.start], show(b)]
        pos = b.end
    out.append(text[pos:])
    return "".join(out)


def render(
    text: str,
    evidence: Evidence,
    *,
    failed: Callable[[Binding], str] | None = None,
    exact: bool = False,
) -> str:
    """``text`` as a reader sees it: values filled in, quotes as ``"quote" [source]``.
    Failed bindings stay as written unless ``failed`` says how to show them. ``exact``:
    values at full precision instead of their display format (what checks compare)."""
    bindings = find_bindings(text, evidence)

    def show(b: Binding) -> str:
        if b.ok:
            return b.exact if exact and b.exact else b.rendered
        return failed(b) if failed is not None else b.raw

    return _splice(text, bindings, show)


# ----------------------------------------------------------------- claims


@dataclass(frozen=True)
class Grounding:
    bindings: list[Binding] = field(default_factory=list)
    unbound: list[str] = field(default_factory=list)  # bare numbers and citations

    @property
    def state(self) -> Literal["grounded", "failed", "none"]:
        if any(not b.ok for b in self.bindings):
            return "failed"
        if self.bindings and not self.unbound:
            return "grounded"
        return "none"

    @property
    def refs(self) -> list[ValueRef | QuoteRef]:
        return [b.ref for b in self.bindings if b.ref is not None]


# A small integer right after one of these nouns names something ("type 2", "phase 3",
# "outcome 0", "cluster 3"); it is an identifier, not a reported quantity.
_LABEL = re.compile(
    r"(?i)\b(?:type|phase|stage|grade|class|group|outcome|cohort|arm|cluster|chromosome|chr"
    r"|figure|fig\.|table|step|tier|level|batch|sample|replicate|run)\s*$"
)
_MAX_LABEL = 10


def _identifier(text: str, n: Number) -> bool:
    before = text[: n.start]
    if before.endswith("-") and before[:-1][-1:].isalpha():
        return True  # IL-23, COVID-19, type-2
    small = n.decimals == 0 and not n.unit and 0 <= n.value <= _MAX_LABEL
    return small and _LABEL.search(before) is not None


def _bare(text: str) -> Iterator[str]:
    """Citations and numbers left once bindings, list markers, run ids and identifiers
    (ADR-014 design note) are removed."""
    for key in cited_keys(text):
        yield f"[{key}]"
    text = re.sub(r"\[[^\[\]]*\]", " ", text)  # citation groups were reported above
    text = _RUN_REF.sub(" ", _LIST_PREFIX.sub("", text))
    for n in numbers(text):
        if not _identifier(text, n):
            yield n.text


def ground_claim(text: str, evidence: Evidence) -> Grounding:
    bindings = find_bindings(text, evidence)
    rest = _splice(text, bindings, lambda _b: " ")
    return Grounding(bindings, list(_bare(rest)))
