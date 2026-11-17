"""Small text utilities shared by deterministic checks. No models, no heuristics per case."""

from __future__ import annotations

import re
from dataclasses import dataclass

# An unsigned number standing on its own: not glued to letters (CD14, rs123, GSE9100417).
_NUM = re.compile(
    r"(?<![A-Za-z0-9_.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?(?:e-?\d+)?)(?![A-Za-z0-9_]|\.\d)"
)
_SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻", "0123456789-")
_SCI = re.compile(r"(\d(?:\.\d+)?)\s*[x\u00d7]\s*10\s*\^?\s*([-+]?\d+)")
_UNIT = re.compile(
    r"\s?(%|percent|mg/dl|mmol/l|mg|kg|g|ml|mm|cm|-?fold|years?|months?|weeks?|days?|hours?)\b|\s?%",
    re.I,
)
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+\-]*")
_LINE_UNIT = re.compile(r"(?i)(?:^|[_\s])(mg_?dl|mg/dl|mmol_?l|pct|percent|fold)(?:$|[_\s:])")

STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "by",
        "for",
        "from",
        "had",
        "has",
        "have",
        "i",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "our",
        "that",
        "the",
        "their",
        "then",
        "there",
        "these",
        "this",
        "those",
        "to",
        "was",
        "were",
        "which",
        "with",
        "we",
        "than",
        "more",
        "most",
        "less",
        "also",
        "only",
        "all",
        "both",
        "each",
        "other",
        "such",
        "using",
        "used",
        "use",
        "via",
        "per",
        "after",
        "before",
        "between",
        "across",
        "about",
        "above",
        "below",
        "over",
        "under",
        "up",
        "down",
        "out",
        "than",
        "versus",
        "vs",
        "not",
        "no",
        "yes",
        "can",
        "could",
        "may",
        "might",
        "showed",
        "show",
        "shows",
        "found",
        "find",
        "finds",
        "reported",
        "report",
        "reports",
        "observed",
        "higher",
        "lower",
        "increased",
        "decreased",
        "reduced",
        "increase",
        "decrease",
        "reduction",
        "among",
        "within",
        "without",
        "while",
        "patients",
        "cells",
        "data",
        "analysis",
        "study",
        "studies",
        "results",
        "result",
        "evidence",
        "answer",
    ]
)
# Verbs that say *how* a step is phrased, not *what* it is about.
GENERIC_VERBS = frozenset(
    [
        "summarise",
        "summarize",
        "summary",
        "check",
        "retrieve",
        "pull",
        "compute",
        "calculate",
        "describe",
        "assess",
        "evaluate",
        "report",
        "identify",
        "load",
        "inspect",
        "build",
        "building",
        "make",
        "run",
        "perform",
        "estimate",
        "count",
        "review",
        "list",
        "obtain",
        "get",
        "fetch",
        "gather",
        "collect",
        "provide",
        "examine",
        "determine",
        "compare",
        "look",
    ]
)
NUMBER_WORDS = frozenset(
    ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]
)


@dataclass(frozen=True)
class Number:
    value: float
    text: str
    unit: str  # normalised: "%" / "mg/dl" / "fold" / "" ...
    start: int
    end: int

    @property
    def decimals(self) -> int:
        t = self.text.replace(",", "")
        if "e" in t.lower():
            return 99
        return len(t.split(".")[1]) if "." in t else 0


def normalise(text: str) -> str:
    """One spelling for numbers: unicode minus and dashes, superscripts, "3.1 x 10^-32".

    Checks run on normalised text, so positions refer to the normalised string."""
    t = text.replace("\u2212", "-").replace("\u2009", " ").replace("\u202f", " ")
    t = re.sub(r"(?<=\d)\s*[\u2013\u2014]\s*(?=\d)", "-", t)  # 0.61<en dash>0.96 -> 0.61-0.96
    t = re.sub(r"10([⁰¹²³⁴⁵⁶⁷⁸⁹⁻]+)", lambda m: "10^" + m.group(1).translate(_SUPERSCRIPT), t)
    return _SCI.sub(lambda m: f"{m.group(1)}e{int(m.group(2))}", t)


def numbers(text: str) -> list[Number]:
    """Numbers in (normalised) ``text``. ``a-b`` is a range of two positive numbers that
    share b's unit; a leading minus not preceded by a digit or letter makes a number negative."""
    out: list[Number] = []
    for m in _NUM.finditer(text):
        raw = m.group(1)
        start = m.start()
        if start >= 1 and text[start - 1] == "-":
            before = text[start - 2] if start >= 2 else " "
            if not (before.isalnum() or before in "_.") or before in "=(:":
                raw = "-" + raw
                start -= 1
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        u = _UNIT.match(text, m.end())
        unit = ""
        if u:
            unit = (u.group(1) or "%").lower().lstrip("-")
            unit = "%" if unit in ("%", "percent") else unit
            unit = {
                "year": "years",
                "month": "months",
                "week": "weeks",
                "day": "days",
                "hour": "hours",
            }.get(unit, unit)
        if not unit:  # "35-39%": the first number of a range takes the second one's unit
            r = re.match(r"-(\d+(?:\.\d+)?)\s?(%|percent)", text[m.end() :])
            if r:
                unit = "%"
        out.append(Number(value, raw, unit or line_unit(text, m.start()), start, m.end()))
    return out


def line_unit(text: str, pos: int) -> str:
    """A unit named elsewhere on the number's line, e.g. ``difference_mg_dl 38.5``."""
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    m = _LINE_UNIT.search(text[start : end if end != -1 else len(text)])
    if not m:
        return ""
    u = m.group(1).lower().replace("_", "")
    return {"mgdl": "mg/dl", "mmoll": "mmol/l", "pct": "%", "percent": "%"}.get(u, u)


def same_value(claim: Number, evidence: Number) -> bool:
    """Equal, or the claim is the evidence rounded to the claim's precision."""
    if claim.value == evidence.value:
        return True
    if claim.decimals >= 99 or evidence.decimals >= 99:  # scientific notation: exact text
        return claim.text.lower() == evidence.text.lower()
    if evidence.decimals > claim.decimals:
        return round(evidence.value, claim.decimals) == claim.value
    return False


def words(text: str) -> set[str]:
    """Lower-cased content words, identifiers split on punctuation and underscores."""
    out: set[str] = set()
    for token in re.split(r"[^A-Za-z0-9]+", text):
        t = token.lower()
        if len(t) >= 3 and t not in STOPWORDS and not t.isdigit():
            out.add(t)
    return out


def tokens(text: str) -> list[tuple[str, int]]:
    """Original-case word tokens with their start offsets."""
    return [(m.group(0), m.start()) for m in _WORD.finditer(text)]


def stem(word: str) -> str:
    """Crude, deterministic: enough to match mutation/mutations, neighbour/neighbors."""
    return word.lower()[:5]


def stems(text: str) -> set[str]:
    return {stem(w) for w in words(text)}


def proper_names(text: str) -> list[str]:
    """Named tools, databases and methods: mixed-case words (gnomAD, ChEMBL,
    ClinicalTrials.gov) and capitalised words that do not start the text (Harmony, Leiden,
    Open Targets). Consecutive capitalised words are joined ("Open Targets")."""
    names: list[str] = []
    current: list[str] = []
    for i, (tok, _pos) in enumerate(tokens(text)):
        mixed = any(c.isupper() for c in tok[1:]) and any(c.islower() for c in tok)
        capital = tok[0].isupper() and i > 0
        if mixed or capital:
            current.append(tok)
        else:
            if current:
                names.append(" ".join(current))
            current = []
    if current:
        names.append(" ".join(current))
    return names


def mentions(name: str, text: str) -> bool:
    """``name`` appears in ``text``, also as one glued identifier (Open Targets -> opentargets)."""
    low = text.lower()
    n = name.lower()
    return n in low or n.replace(" ", "") in low or n.replace(" ", "_") in low


# "Run r1 failed", "could not be quantified": a claim that reports a failure honestly.
ACKNOWLEDGES = re.compile(
    r"\b(not|no|never|n't|failed|fails|skipped|missing|without|unable|pending|remains?|"
    r"outstanding|lack(?:s|ing)?|could not|cannot|did not|was not|were not)\b",
    re.I,
)
