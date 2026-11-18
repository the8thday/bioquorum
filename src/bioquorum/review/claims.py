"""Split an answer into checkable claims with the reviewer model (ARCHITECTURE.md §6.5 step 1)."""

from __future__ import annotations

from bioquorum.core.enums import ClaimKind, Classification
from bioquorum.models.client import ModelClient
from bioquorum.review.evidence import ClaimInput, Evidence
from bioquorum.review.modeljson import ask_json

SYSTEM = """You split a research assistant's answer into atomic, checkable claims.
Copy each claim's wording from the answer as closely as possible, keeping numbers, units and
citation markers such as [c1] or [PMID:123] exactly as written. Evidence markers must be
copied character for character and kept with the claim they support: value placeholders like
{{n_genes}} and quote markers like [@s3 "exact words"] or [@PMID:123 "exact words"].
Do not judge the claims.
kind is one of: numeric (reports a number), citation (attributes a finding to a source),
execution (says a computation or query was done), causal (cause-effect statement),
plan (says what was or was not covered). run_ids lists run identifiers the claim relies on,
only if the answer names them."""


async def extract_claims(
    client: ModelClient,
    answer: str,
    evidence: Evidence,
    *,
    session_id: str,
    classification: Classification,
    causation_id: str | None = None,
) -> list[ClaimInput]:
    runs = ", ".join(r.run_id for r in evidence.runs) or "none"
    user = (
        f"Known run ids: {runs}\n\n# Answer\n{answer}\n\n"
        'Return {"claims": [{"text": "...", "kind": "numeric", "run_ids": []}]}'
    )
    data, _model = await ask_json(
        client,
        SYSTEM,
        user,
        session_id=session_id,
        classification=classification,
        causation_id=causation_id,
    )
    out: list[ClaimInput] = []
    for i, c in enumerate(data.get("claims") or [], start=1):
        if not isinstance(c, dict) or not str(c.get("text", "")).strip():
            continue
        try:
            kind = ClaimKind(c.get("kind", "numeric"))
        except ValueError:
            kind = ClaimKind.NUMERIC
        refs = tuple(str(r) for r in c.get("run_ids") or () if isinstance(r, str))
        out.append(ClaimInput(index=i, text=str(c["text"]).strip(), kind=kind, run_refs=refs))
    return out
