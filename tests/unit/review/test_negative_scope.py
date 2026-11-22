import pytest

from bioquorum.review.checks.absence import unsupported_absence

LEDGER = {
    "queries": [
        {"id": "q1", "status": "empty", "tool": "search_papers", "scope": {"query": "gene X"}},
        {"id": "q2", "status": "failed", "tool": "search_papers", "scope": {"query": "gene X"}},
        {
            "id": "q3",
            "status": "empty",
            "tool": "opentargets__search_entities",
            "scope": {"query_strings": ["TYK2", "psoriasis"]},
        },
    ]
}


@pytest.mark.parametrize(
    ("text", "flag"),
    [
        ("There is no evidence for a benefit.", True),
        ("There is no evidence for a benefit [q1].", True),
        ("search_papers returned no records for gene X [q1].", False),
        ("search_papers returned no records for gene X [q2].", True),
        ("opentargets__search_entities returned no records for TYK2 [q3].", False),
        ("opentargets__search_entities returned no records for unrelated [q3].", True),
        ("这个领域尚无临床证据。", True),
        ("在 search_papers 检索 gene X 未发现记录 [q1]。", False),
        ("A reported confidence interval includes no effect.", False),
        (
            "search_papers returned no records for gene X [q1]. There is no evidence of toxicity.",
            True,
        ),
    ],
)
def test_empty_query_does_not_establish_global_absence(text, flag):
    assert bool(unsupported_absence(text, LEDGER)) is flag


@pytest.mark.anyio
async def test_valid_quote_cannot_elevate_an_unsupported_absence_claim(ctx):
    from bioquorum.core.enums import ClaimKind
    from bioquorum.core.status import VerificationStatus
    from bioquorum.review.checks.absence import NegativeSearchScope
    from bioquorum.review.evidence import ClaimInput, Evidence, Source
    from bioquorum.review.service import review

    material = "No evidence in this source."
    evidence = Evidence(sources=[Source("s1", "call", "paper", material)], coverage=LEDGER)
    result = await review(
        ctx.store,
        session_id=ctx.session_id,
        target_event_id="answer",
        claims=[
            ClaimInput(
                1,
                'There is no evidence of benefit. [@s1 "No evidence in this source."]',
                ClaimKind.CITATION,
            )
        ],
        evidence=evidence,
        checks=[NegativeSearchScope()],
    )
    claim = result.claims[0]
    assert claim.grounding.bindings and all(b.ok for b in claim.grounding.bindings)
    assert claim.status is VerificationStatus.UNVERIFIED


@pytest.mark.anyio
async def test_unsupported_absence_does_not_mask_binding_or_review_errors(ctx):
    from bioquorum.core.enums import ClaimKind
    from bioquorum.core.status import VerificationStatus
    from bioquorum.review.checks.absence import NegativeSearchScope
    from bioquorum.review.checks.citations import CitationResolves
    from bioquorum.review.evidence import ClaimInput, Evidence
    from bioquorum.review.service import review

    evidence = Evidence(coverage=LEDGER)
    result = await review(
        ctx.store,
        session_id=ctx.session_id,
        target_event_id="answer",
        claims=[
            ClaimInput(1, "There is no evidence of benefit ({{missing_val}}).", ClaimKind.NUMERIC),
            ClaimInput(2, "There is no evidence of benefit [PMID:99999999].", ClaimKind.CITATION),
        ],
        evidence=evidence,
        checks=[NegativeSearchScope(), CitationResolves()],
    )
    assert [c.status for c in result.claims] == [
        VerificationStatus.DISPUTED,
        VerificationStatus.DISPUTED,
    ]
