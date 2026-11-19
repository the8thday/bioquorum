"""Check session retrieval and, when requested, existence in the authoritative registry."""

from __future__ import annotations

import json
from collections.abc import Sequence

from bioquorum.core.enums import Severity
from bioquorum.review.checks.base import Finding
from bioquorum.review.evidence import ClaimInput, Evidence, cited_keys, normalise_key
from bioquorum.tools import ToolContext, invoke
from bioquorum.tools.builtin.citation import CITATION_LOOKUP

CITATION_UNAVAILABLE = "citation_lookup_unavailable"


class CitationResolves:
    name = "citation_resolves"
    needs_model = False

    def __init__(self, online: ToolContext | None = None, *, causation_id: str | None = None):
        self.online = online
        self.causation_id = causation_id

    async def _lookup(self, key: str) -> bool | None:
        assert self.online is not None
        result = await invoke(
            CITATION_LOOKUP,
            {"key": key},
            self.online,
            causation_id=self.causation_id,
        )
        if result.is_error:
            return None
        data = json.loads(result.text())
        exists = data.get("exists")
        return exists if data.get("key") == key and isinstance(exists, bool) else None

    async def run(self, claims: Sequence[ClaimInput], evidence: Evidence) -> list[Finding]:
        findings: list[Finding] = []
        online: dict[str, bool | None] = {}
        for claim in claims:
            for raw in dict.fromkeys(cited_keys(claim.text)):
                key = normalise_key(raw)
                if evidence.citation(key) is None:
                    findings.append(
                        Finding(
                            claim.index,
                            self.name,
                            Severity.ERROR,
                            f"cites [{raw}], which is not among the sources retrieved in this session",
                        )
                    )
                    continue
                if self.online is None or not key.startswith(("PMID:", "DOI:")):
                    continue
                if key not in online:
                    online[key] = await self._lookup(key)
                if online[key] is False:
                    findings.append(
                        Finding(
                            claim.index,
                            self.name,
                            Severity.ERROR,
                            f"the public registry does not resolve [{key}]",
                        )
                    )
                elif online[key] is None:
                    findings.append(
                        Finding(
                            claim.index,
                            CITATION_UNAVAILABLE,
                            Severity.WARNING,
                            f"online resolution could not be completed for [{key}]",
                        )
                    )
        return findings
