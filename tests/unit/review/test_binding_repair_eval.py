"""Offline checks for the binding repair experiment."""

import pytest

from bioquorum.core.messages import Message
from bioquorum.core.refs import NamedValue
from bioquorum.models import ModelsConfig, Router
from bioquorum.review.evidence import DeclaredValue, Evidence, Source


@pytest.fixture
def repair(monkeypatch):
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).parents[3]))
    from evals import binding_repair_eval

    return binding_repair_eval


def test_scoring_requires_correct_record_and_preserved_claim(repair):
    case = repair.Case(
        id="test",
        split="heldout",
        question="Report screened and blind.",
        initial="Screened: {{missing}}",
        values=[NamedValue(name="n", value=36)],
        sources=["Outcome assessors were masked.", "Participants were not masked."],
        targets=[
            repair.Target(label="Screened", value="n"),
            repair.Target(label="Masking", source=1, description="Outcome assessors were masked"),
        ],
    )
    evidence = Evidence(
        values={
            "n": DeclaredValue(NamedValue(name="n", value=36), "run"),
            "other": DeclaredValue(NamedValue(name="other", value=36), "run"),
        },
        sources=[Source(f"s{i}", str(i), "read_fixture", s) for i, s in enumerate(case.sources, 1)],
    )
    good = 'Screened: {{n}}\nMasking: [@s1 "Outcome assessors were masked."]'
    assert repair.score_answer(case, good, evidence) == {
        "Screened": "repaired",
        "Masking": "repaired",
    }
    assert (
        repair.score_answer(case, good.replace("{{n}}", "{{other}}"), evidence)["Screened"]
        == "changed"
    )
    assert (
        repair.score_answer(case, good.replace("Screened: {{n}}", "Screened: 36"), evidence)[
            "Screened"
        ]
        == "unbound"
    )
    negated = good.replace("[@s1", "Outcome assessors were not masked. [@s1")
    assert repair.score_answer(case, negated, evidence)["Masking"] == "changed"
    assert repair.score_answer(case, good.replace("s1", "s2"), evidence)["Masking"] == "unresolved"


@pytest.mark.anyio
async def test_retry_uses_production_feedback_and_preserves_no_retry_baseline(
    repair, monkeypatch, tmp_path
):
    sent = []

    async def scripted_call(self, route, messages, tools):
        sent.append(messages[-1].text())
        answer = "{{unknown}}" if len(sent) == 1 else "{{n}}"
        return Message.assistant(f"Screened: {answer}"), dict.fromkeys(repair.TOKENS, 12)

    monkeypatch.setattr(repair.ModelClient, "_call", scripted_call)
    case = repair.Case(
        id="test",
        split="heldout",
        question="Report Screened:",
        initial="Screened: {{missing}}",
        values=[NamedValue(name="n", value=36)],
        targets=[repair.Target(label="Screened", value="n")],
    )
    router = Router(
        ModelsConfig.from_dict(
            {"providers": {"deepseek": {}}, "roles": {"worker": "deepseek:test"}}
        )
    )
    for cap in (0, 2):
        row = await repair.run_arm(tmp_path / str(cap), case, router, cap, {})
        assert row["usage"]["calls"] == cap
        assert row["retries"] == cap
        assert row["targets"]["Screened"] == ("repaired" if cap else "unresolved")
    assert "missing" in sent[0] and "unknown" in sent[1]
