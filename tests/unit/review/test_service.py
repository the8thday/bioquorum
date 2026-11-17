import json

import pytest
from pydantic_ai import messages as pa
from pydantic_ai.models.function import FunctionModel

from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, ClaimKind, Classification, OutputStream
from bioquorum.core.messages import Message, TextPart, ToolResultPart
from bioquorum.core.status import VerificationStatus
from bioquorum.models import ModelClient, ModelsConfig, Router
from bioquorum.review import (
    CitationSupports,
    ClaimInput,
    Evidence,
    deterministic_checks,
    extract_claims,
    review,
)
from bioquorum.review.evidence import CitationEvidence, RunEvidence
from bioquorum.storage.rebuild import snapshot

pytestmark = pytest.mark.anyio
CFG = ModelsConfig.from_dict(
    {"providers": {"deepseek": {}}, "roles": {"reviewer": "deepseek:r", "worker": "deepseek:r"}}
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
        VerificationStatus.REVIEWED,
        VerificationStatus.DISPUTED,
        VerificationStatus.DISPUTED,
    ]
    assert result.status is VerificationStatus.DISPUTED
    types = [e.type for e in store.read("S")]
    assert types[0] == "review_started" and types.count("claim_extracted") == 3
    assert types.count("review_finding") == 2 and types.count("claim_status_changed") == 3
    assert types.index("review_finished") < types.index("claim_status_changed")
    snap = snapshot(store.db)
    assert {r["status"] for r in snap["claims"]} == {"reviewed", "disputed"}
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
