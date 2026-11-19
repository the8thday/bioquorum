import json

import pytest
from pydantic_ai import messages as pa
from pydantic_ai.models.function import FunctionModel

from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, ClaimKind, Classification, OutputStream
from bioquorum.core.messages import Message, TextPart, ToolResultPart
from bioquorum.core.refs import NamedValue, QuoteRef, ValueRef
from bioquorum.core.status import VerificationStatus
from bioquorum.models import ModelClient, ModelsConfig, Router
from bioquorum.review import (
    CitationSupports,
    ClaimInput,
    Evidence,
    deterministic_checks,
    extract_claims,
    review,
    review_answer,
)
from bioquorum.review.evidence import CitationEvidence, DeclaredValue, RunEvidence, Source
from bioquorum.storage.rebuild import snapshot

pytestmark = pytest.mark.anyio
CFG = ModelsConfig.from_dict(
    {"providers": {"deepseek": {}}, "roles": {"reviewer": "deepseek:r", "worker": "deepseek:r"}}
)


async def test_empty_claim_extraction_is_reported_as_incomplete(store):
    answer = store.append(
        ev.AssistantMessage(message=Message.assistant("Treatment changed the endpoint.")),
        session_id="S",
        actor=Actor.AGENT,
    )
    result = await review_answer(
        store,
        client(store, reviewer({"claims": []})),
        session_id="S",
        classification=Classification.PUBLIC,
        answer_event_id=answer.id,
    )
    assert not result.claims and any(
        error.startswith("claim_extraction:") for error in result.errors
    )


def reviewer(reply: dict, seen: list | None = None):
    def fn(messages, info):
        if seen is not None:
            seen.append(messages)
        return pa.ModelResponse(parts=[pa.TextPart(content=json.dumps(reply))])

    return FunctionModel(fn)


def client(store, model):
    return ModelClient(Router(CFG), store, factory=lambda route: model)


EVIDENCE = Evidence(
    question="Assess APOE4 in Alzheimer's disease.",
    runs=[RunEvidence("r1", True, "", "odds_ratio 3.68")],
    citations=[
        CitationEvidence("c1", "APOE4 in mice", "In APOE4 knock-in mice, amyloid rose 2-fold.")
    ],
)
CLAIMS = [
    ClaimInput(1, "E4 carriers had an odds ratio of 3.68.", ClaimKind.NUMERIC),
    ClaimInput(2, "E4 carriers had an odds ratio of 6.38.", ClaimKind.NUMERIC),
    ClaimInput(3, "APOE4 doubles amyloid in patients [c1].", ClaimKind.CITATION),
]


async def test_review_events_and_statuses(store):
    supports = CitationSupports(
        client(
            store,
            reviewer(
                {"verdicts": [{"claim_index": 3, "supported": False, "reason": "mouse study"}]}
            ),
        ),
        session_id="S",
        classification=Classification.PUBLIC,
    )
    result = await review(
        store,
        session_id="S",
        target_event_id="E",
        claims=CLAIMS,
        evidence=EVIDENCE,
        checks=[*deterministic_checks(), supports],
    )
    statuses = [c.status for c in result.claims]
    assert statuses == [
        VerificationStatus.UNVERIFIED,  # passed review, but unbound: still an assertion
        VerificationStatus.DISPUTED,
        VerificationStatus.DISPUTED,
    ]
    assert result.status is VerificationStatus.DISPUTED
    types = [e.type for e in store.read("S")]
    assert types[0] == "review_started" and types.count("claim_extracted") == 3
    assert types.count("review_finding") == 2 and types.count("claim_status_changed") == 3
    assert types.index("review_finished") < types.index("claim_status_changed")
    snap = snapshot(store.db)
    assert {r["status"] for r in snap["claims"]} == {"unverified", "disputed"}
    models = {r["check"]: r["reviewer_model"] for r in snap["review_findings"]}
    assert models == {"value_match": "deterministic", "citation_supports": "deepseek:r"}


async def test_untrusted_abstract_is_fenced_for_the_reviewer(store):
    seen: list = []
    supports = CitationSupports(
        client(store, reviewer({"verdicts": []}, seen)),
        session_id="S",
        classification=Classification.PUBLIC,
    )
    await supports.run(CLAIMS, EVIDENCE)
    user_text = str(seen[0][-1].parts[-1].content)
    assert "<untrusted-tool-output>" in user_text and "knock-in mice" in user_text


@pytest.mark.parametrize(
    ("verdicts", "status"),
    [
        ([], VerificationStatus.GROUNDED),
        ([{"claim_index": 1, "supported": False}], VerificationStatus.DISPUTED),
    ],
)
async def test_bound_source_alias_goes_to_the_semantic_reviewer(store, verdicts, status):
    seen = []
    quote = "A constitutive strain was tested in mice."
    supports = CitationSupports(
        client(store, reviewer({"verdicts": verdicts}, seen)),
        session_id="S",
        classification=Classification.PUBLIC,
    )
    result = await review(
        store,
        session_id="S",
        target_event_id="answer",
        claims=[
            ClaimInput(1, f'The strain was tested in humans [@s1 "{quote}"].', ClaimKind.CITATION)
        ],
        evidence=Evidence(sources=[Source("s1", "source-call", "original", quote)]),
        checks=[*deterministic_checks(), supports],
    )
    assert quote in str(seen[0]) and "<untrusted-tool-output>" in str(seen[0])
    assert result.claims[0].status is status


async def test_broken_model_check_does_not_hide_deterministic_results(store):
    def boom(messages, info):
        raise RuntimeError("provider down")

    supports = CitationSupports(
        client(store, FunctionModel(boom)), session_id="S", classification=Classification.PUBLIC
    )
    result = await review(
        store,
        session_id="S",
        target_event_id="E",
        claims=CLAIMS,
        evidence=EVIDENCE,
        checks=[*deterministic_checks(), supports],
    )
    assert result.errors and "citation_supports" in result.errors[0]
    assert result.claims[1].status is VerificationStatus.DISPUTED


async def test_extract_claims(store):
    reply = {
        "claims": [
            {"text": "OR was 3.68 in run r1.", "kind": "numeric", "run_ids": ["r1"]},
            {"text": "It is causal.", "kind": "bogus"},
            {"text": "  "},
        ]
    }
    claims = await extract_claims(
        client(store, reviewer(reply)),
        "answer",
        EVIDENCE,
        session_id="S",
        classification=Classification.PUBLIC,
    )
    assert [(c.index, c.kind.value, c.run_refs) for c in claims] == [
        (1, "numeric", ("r1",)),
        (2, "numeric", ()),
    ]


async def test_evidence_from_events(store):
    sid = "S"
    store.append(
        ev.UserMessage(message=Message.user("Assess APOE4")), session_id=sid, actor=Actor.USER
    )
    store.append(
        ev.RunStarted(run_id="R1", kernel_id="k", code="print(3.68)", code_hash="0" * 64),
        session_id=sid,
        actor=Actor.TOOL,
    )
    store.append(
        ev.RunOutput(run_id="R1", stream=OutputStream.STDOUT, text="3.68\n"),
        session_id=sid,
        actor=Actor.TOOL,
    )
    store.append(ev.RunFinished(run_id="R1", run_key="1" * 64), session_id=sid, actor=Actor.TOOL)
    record = "# APOE4 and risk\nPMID: 28776086\nDOI: 10.1007/s00125-017-4342-z\nabstract text"
    store.append(
        ev.ToolCallFinished(
            call_id="C",
            tool="biomcp__get",
            result=ToolResultPart(call_id="C", content=[TextPart(text=record)]),
        ),
        session_id=sid,
        actor=Actor.TOOL,
    )
    e = Evidence.from_events(store.read(sid))
    assert e.question == "Assess APOE4"
    assert e.runs == [RunEvidence("R1", True, "print(3.68)", "3.68\n")]
    cit = e.citation("PMID:28776086")
    assert (
        cit is not None and cit.title == "APOE4 and risk" and cit.doi == "10.1007/s00125-017-4342-z"
    )
    assert e.citation("doi:10.1007/S00125-017-4342-Z") is not None


async def test_full_record_beats_search_list(store):
    sid = "S"
    listing = (
        "# Articles\n| Identifier | Title |\n|PMID 11111111|Paper one|\n|PMID 22222222|Paper two|"
    )
    full = "# Paper two\nPMID: 22222222\nDOI: 10.1000/two\nAbstract: drug X lowers glucose in mice."
    full_json = (
        '{"pmid": "22222222", "title": "Paper two", "sections": "Full text: X inhibits complex I."}'
    )
    for i, text in enumerate([listing, full, full_json]):
        store.append(
            ev.ToolCallFinished(
                call_id=f"C{i}",
                tool="biomcp__get",
                result=ToolResultPart(call_id=f"C{i}", content=[TextPart(text=text)]),
            ),
            session_id=sid,
            actor=Actor.TOOL,
        )
    e = Evidence.from_events(store.read(sid))
    two = e.citation("PMID:22222222")
    assert two is not None and two.title == "Paper two"
    assert "lowers glucose in mice" in two.abstract and "inhibits complex I" in two.abstract
    one = e.citation("PMID:11111111")
    assert one is not None and one.abstract == "|PMID 11111111|Paper one|"  # only its own line


# ----------------------------------------------------------------- grounding (ADR-014)

ABSTRACT = "# APOE4 and risk\nPMID: 28776086\nE4 homozygotes had an odds ratio of 14.9 for AD."
BOUND = Evidence(
    runs=[RunEvidence("R1", True, "bq.value('or_e4', 3.68)", "bq.value or_e4 = 3.68")],
    values={"or_e4": DeclaredValue(NamedValue(name="or_e4", value=3.68), "R1")},
    sources=[Source("s1", "C1", "biomcp__article_getter", ABSTRACT)],
    citations=[CitationEvidence("PMID:28776086", "APOE4 and risk", ABSTRACT, pmid="28776086")],
)


async def test_grounded_claims_climb_to_reviewed_and_failed_bindings_are_disputed(store):
    claims = [
        ClaimInput(1, "E4 carriers had an odds ratio of {{or_e4}}.", ClaimKind.NUMERIC),
        ClaimInput(
            2,
            'Homozygotes fare worse [@PMID:28776086 "E4 homozygotes had an odds ratio of 14.9"].',
            ClaimKind.CITATION,
        ),
        ClaimInput(3, "Carriers had an odds ratio of {{or_e2}}.", ClaimKind.NUMERIC),
        ClaimInput(4, "E4 carriers had an odds ratio of 3.68.", ClaimKind.NUMERIC),
    ]
    result = await review(
        store,
        session_id="S",
        target_event_id="E",
        claims=claims,
        evidence=BOUND,
        checks=deterministic_checks(),
    )
    assert [c.status for c in result.claims] == [
        VerificationStatus.REVIEWED,
        VerificationStatus.REVIEWED,
        VerificationStatus.DISPUTED,
        VerificationStatus.UNVERIFIED,
    ]
    (binding,) = [f for f in result.findings if f.check == "binding"]
    assert binding.claim_index == 3 and "or_e2" in binding.evidence
    events = store.read("S")
    extracted = [e.parsed() for e in events if e.type == "claim_extracted"]
    assert extracted[0].text == claims[0].text  # the marked text is what is recorded
    assert ValueRef(name="or_e4", run_id="R1") in extracted[0].refs
    assert any(isinstance(r, QuoteRef) and r.call_id == "C1" for r in extracted[1].refs)
    changes = [e.parsed() for e in events if e.type == "claim_status_changed"]
    first = [c for c in changes if c.claim_id == result.claims[0].claim_id]
    assert [(c.from_status.value, c.to_status.value) for c in first] == [
        ("unverified", "grounded"),
        ("grounded", "reviewed"),
    ]


async def test_checks_see_rendered_text(store):
    seen: list[str] = []

    class Spy:
        name, needs_model = "spy", False

        async def run(self, claims, evidence):
            seen.extend(c.text for c in claims)
            return []

    await review(
        store,
        session_id="S",
        target_event_id="E",
        claims=[ClaimInput(1, 'OR {{or_e4}} [@s1 "odds ratio of 14.9 for AD"]', ClaimKind.NUMERIC)],
        evidence=BOUND,
        checks=[Spy()],
    )
    assert seen == ['OR 3.68 "odds ratio of 14.9 for AD" [s1]']


async def test_rounded_display_does_not_trip_value_match(store):
    """A real run (2026-11-18): {{welch_p:.3e}} showed 2.644e-36 while the run printed
    2.64416134953994e-36, and value_match disputed a correctly bound claim."""
    p = 2.64416134953994e-36
    evidence = Evidence(
        runs=[RunEvidence("R1", True, "bq.value('welch_p', p)", f"bq.value welch_p = {p}")],
        values={"welch_p": DeclaredValue(NamedValue(name="welch_p", value=p), "R1")},
    )
    result = await review(
        store,
        session_id="S",
        target_event_id="E",
        claims=[ClaimInput(1, "Welch t-test: p = {{welch_p:.3e}}.", ClaimKind.NUMERIC)],
        evidence=evidence,
        checks=deterministic_checks(),
    )
    assert result.findings == [] and result.claims[0].status is VerificationStatus.REVIEWED


def test_consecutive_display_results_stay_separate():

    payloads = [
        ev.RunStarted(run_id="r1", kernel_id="k", code="pass", code_hash="hash"),
        ev.RunOutput(run_id="r1", stream=OutputStream.DISPLAY, text="bq.value cells = 2700"),
        ev.RunOutput(run_id="r1", stream=OutputStream.DISPLAY, text="bq.value genes = 32738"),
        ev.RunFinished(run_id="r1", run_key="key"),
    ]
    events = [
        ev.Event.build(p, session_id="S", seq=i, actor=Actor.TOOL) for i, p in enumerate(payloads)
    ]
    assert Evidence.from_events(events).runs[0].stdout.splitlines() == [
        "bq.value cells = 2700",
        "bq.value genes = 32738",
    ]


async def test_unavailable_online_registry_does_not_promote_bound_citation(ctx, store):
    from bioquorum.review.checks.citations import CitationResolves

    check = CitationResolves(ctx)
    # The session is internal, so the tool will refuse the public lookup before networking.
    evidence = Evidence(
        sources=[Source("s1", "call", "read_file", "PMID:21475136\nThe assay showed no effect.")],
        citations=[CitationEvidence(key="PMID:21475136", abstract="The assay showed no effect.")],
    )
    claim = ClaimInput(
        1,
        'The assay showed no effect [@PMID:21475136 "The assay showed no effect."]',
        ClaimKind.CITATION,
    )
    result = await review(
        store,
        session_id="S",
        target_event_id="E",
        claims=[claim],
        evidence=evidence,
        checks=[check],
    )
    assert result.claims[0].status is VerificationStatus.GROUNDED
    assert [f.check for f in result.findings] == ["citation_lookup_unavailable"]


async def test_broken_check_does_not_mark_grounded_claim_reviewed(store):
    from bioquorum.core.refs import NamedValue
    from bioquorum.review.evidence import DeclaredValue

    class BrokenCheck:
        name = "broken"
        needs_model = False

        async def run(self, claims, evidence):
            raise RuntimeError("Check unavailable")

    result = await review(
        store,
        session_id="S",
        target_event_id="target",
        claims=[ClaimInput(1, "The estimate was {{estimate}}.", ClaimKind.NUMERIC)],
        evidence=Evidence(
            values={"estimate": DeclaredValue(NamedValue(name="estimate", value=7), "r")}
        ),
        checks=[BrokenCheck()],
    )
    assert result.errors
    assert result.claims[0].status is VerificationStatus.GROUNDED
