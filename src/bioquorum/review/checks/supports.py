"""citation_supports: does the cited source actually support the claim? (needs a model)

Only claims whose citations resolve are sent; unresolved keys are citation_resolves' job.
Abstracts are tool output, so they are fenced as untrusted before the model sees them.
"""

from __future__ import annotations

from collections.abc import Sequence

from bioquorum.core.enums import Classification, Severity
from bioquorum.core.messages import UNTRUSTED_NOTICE, fence_untrusted
from bioquorum.models.client import ModelClient
from bioquorum.review.checks.base import Finding
from bioquorum.review.evidence import ClaimInput, Evidence, cited_keys
from bioquorum.review.modeljson import ask_json

CITATION_SUPPORT_UNAVAILABLE = "citation_support_unavailable"

SYSTEM = f"""You check whether cited sources support claims in a biomedical answer.
A claim is supported only if the cited source states it: same population or model system
(mouse is not patients), same endpoint, same direction, and no causal language where the
source is associative. Judge only against the given sources. {UNTRUSTED_NOTICE}"""


def _around_quote(text: str, quote: str) -> str:
    position = text.find(quote)
    if position < 0:
        return text[:800]
    return text[max(0, position - 350) : position + len(quote) + 350]


class CitationSupports:
    name = "citation_supports"
    needs_model = True

    def __init__(
        self, client: ModelClient, *, session_id: str, classification: Classification
    ) -> None:
        self.client = client
        self.session_id = session_id
        self.classification = classification
        self.reviewer_model = "unknown"

    async def run(self, claims: Sequence[ClaimInput], evidence: Evidence) -> list[Finding]:
        items: list[str] = []
        checked: set[int] = set()
        by_call = {source.call_id: source for source in evidence.sources}
        for claim in claims:
            sources = [evidence.citation(k) for k in cited_keys(claim.text)]
            passages = [f"[{s.key}] {s.title}\n{s.abstract}" for s in sources if s is not None]
            for ref in claim.quote_refs:
                if source := by_call.get(ref.call_id):
                    passages.append(
                        f"[{ref.source}] Exact bound passage: {ref.quote}\n"
                        + _around_quote(source.text, ref.quote)
                    )
            if not passages:
                continue
            checked.add(claim.index)
            body = "\n".join(dict.fromkeys(passages))
            items.append(
                f"## Claim {claim.index}\n{claim.text}\n### Cited sources\n{fence_untrusted(body)}"
            )
        if not items:
            return []
        user = (
            "\n\n".join(items)
            + '\n\nReturn {"verdicts": [{"claim_index": 1, "supported": true, "reason": "..."}]} '
            "with one verdict per claim above."
        )
        data, self.reviewer_model = await ask_json(
            self.client,
            SYSTEM,
            user,
            session_id=self.session_id,
            classification=self.classification,
        )
        findings: list[Finding] = []
        answered: set[int] = set()
        for v in data.get("verdicts") or []:
            if not isinstance(v, dict) or type(v.get("supported")) is not bool:
                continue
            idx = v.get("claim_index")
            if type(idx) is not int or idx not in checked:
                continue
            answered.add(idx)
            if not v["supported"]:
                reason = str(v.get("reason", ""))[:500]
                findings.append(
                    Finding(
                        idx,
                        self.name,
                        Severity.ERROR,
                        reason or "source does not support the claim",
                    )
                )
        findings.extend(
            Finding(
                idx,
                CITATION_SUPPORT_UNAVAILABLE,
                Severity.WARNING,
                "The citation reviewer returned no verdict for this claim.",
            )
            for idx in sorted(checked - answered)
        )
        return findings
