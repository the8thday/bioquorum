"""citation_resolves: every cited key must exist in the session's evidence.

Live sessions cite literature as ``[PMID:123]`` or ``[DOI:10.x/y]`` and the evidence holds
every identifier that came back from a literature tool. A cited identifier that no tool
ever returned was made up. (Online resolution against PubMed/Crossref is a later step.)
"""

from __future__ import annotations

from collections.abc import Sequence

from bioquorum.core.enums import Severity
from bioquorum.review.checks.base import Finding
from bioquorum.review.evidence import ClaimInput, Evidence, cited_keys


class CitationResolves:
    name = "citation_resolves"
    needs_model = False

    async def run(self, claims: Sequence[ClaimInput], evidence: Evidence) -> list[Finding]:
        findings: list[Finding] = []
        for claim in claims:
            for key in cited_keys(claim.text):
                if evidence.citation(key) is None:
                    findings.append(
                        Finding(
                            claim.index,
                            self.name,
                            Severity.ERROR,
                            f"cites [{key}], which is not among the sources retrieved in this session",
                        )
                    )
        return findings
