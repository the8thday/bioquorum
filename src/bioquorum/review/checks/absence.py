"""Flag unsupported generalizations from searches. A scoped miss stays descriptive."""

import re
from collections.abc import Sequence

from bioquorum.core.enums import Severity
from bioquorum.review.checks.base import Finding
from bioquorum.review.evidence import ClaimInput, Evidence

NAME = "negative_search_scope"
REASON = "Assertion only: the absence claim lacks a matching, scoped search result."
NEGATIVE = re.compile(
    r"\b(?:no|lack of|absence of)\s+(?:(?:published|clinical|genetic|human|direct|available)\s+)*"
    r"(?:evidence|studies|trials|reports|records|results|data)\b|"
    r"(?:没有|尚无|缺乏|未见|未发现|未检索到)(?:任何)?(?:相关|直接|临床|遗传|人类|公开|已发表的?)*"
    r"(?:证据|研究|报道|试验|数据|记录|结果)",
    re.IGNORECASE,
)
BOUNDED = re.compile(
    r"(?:returned|yielded|retrieved) no (?:records|results|studies|reports)\b|"
    r"(?:检索|查询).{0,220}(?:未发现|未检索到|未见)(?:相关)?(?:结果|记录|报道)",
    re.I,
)


def _scope_terms(scope: dict) -> list[str]:
    terms: list[str] = []
    for k, v in scope.items():
        if k in {"page", "offset", "limit", "pageSize"}:
            continue
        if isinstance(v, str) and v.strip():
            terms.append(v)
        elif isinstance(v, list):
            terms.extend(item for item in v if isinstance(item, str) and item.strip())
    return terms


def unsupported_absence(text: str, ledger: dict | None) -> bool:
    if ledger is None:
        return False
    for clause in re.split(
        r"[。\uff01\uff1f;\uff1b\n]|(?<=[.!?])\s+|,\s*(?:so|therefore|implying|indicating)\b", text
    ):
        if not NEGATIVE.search(clause):
            continue
        # An explicit result statement covers only its own clause and recorded search.
        if len(NEGATIVE.findall(clause)) != 1 or not BOUNDED.search(clause):
            return True
        ids = re.findall(r"\[(q[1-9]\d*)\]", clause)
        matching = [q for q in ledger["queries"] if q["id"] in ids and q["status"] == "empty"]
        if not any(
            q["tool"].casefold() in clause.casefold()
            and any(term.casefold() in clause.casefold() for term in _scope_terms(q["scope"]))
            for q in matching
        ):
            return True
    return False


class NegativeSearchScope:
    name = NAME
    needs_model = False

    async def run(self, claims: Sequence[ClaimInput], evidence: Evidence) -> list[Finding]:
        return [
            Finding(c.index, NAME, Severity.WARNING, REASON)
            for c in claims
            if unsupported_absence(c.text, evidence.coverage)
        ]
