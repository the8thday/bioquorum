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

SYSTEM = f"""You check whether cited sources support claims in a biomedical answer.
A claim is supported only if the cited source states it: same population or model system
(mouse is not patients), same endpoint, same direction, and no causal language where the
source is associative. Judge only against the given sources. {UNTRUSTED_NOTICE}"""


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
        for claim in claims:
            sources = [evidence.citation(k) for k in cited_keys(claim.text)]
            found = [s for s in sources if s is not None]
            if not found:
                continue
            checked.add(claim.index)
            body = "\n".join(f"[{s.key}] {s.title}\n{s.abstract}" for s in found)
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
        for v in data.get("verdicts") or []:
            if not isinstance(v, dict) or v.get("supported", True) is not False:
                continue
            idx = v.get("claim_index")
            if isinstance(idx, int) and idx in checked:
                reason = str(v.get("reason", ""))[:500]
                findings.append(
                    Finding(
                        idx,
                        self.name,
                        Severity.ERROR,
                        reason or "source does not support the claim",
                    )
                )
        return findings
