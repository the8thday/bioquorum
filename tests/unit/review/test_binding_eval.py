"""Offline checks for the binding ablation runner."""

import importlib.util
import json
from pathlib import Path

import pytest

from bioquorum.core.enums import MessageRole, ModelRole
from bioquorum.core.ids import new_id
from bioquorum.core.messages import Message, ToolCallPart, ToolResultPart
from bioquorum.core.refs import NamedValue
from bioquorum.models.client import ModelClient
from bioquorum.models.routing import Router
from bioquorum.review.evidence import DeclaredValue, Evidence, RunEvidence, Source

ROOT = Path(__file__).parents[3]
spec = importlib.util.spec_from_file_location("binding_eval", ROOT / "evals" / "binding_eval.py")
assert spec and spec.loader
binding_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(binding_eval)


@pytest.mark.anyio
async def test_injections_distinguish_failure_from_an_unbound_assertion():
    rows = {row["id"]: row for row in await binding_eval.injections()}
    assert rows["missing"]["legacy_flagged"]
    assert not rows["coincident"]["legacy_flagged"]
    assert rows["coincident"]["marker_state"] == "failed"
    assert rows["coincident"]["bare_state"] == "none"
    assert not rows["clean"]["legacy_flagged"]
    assert rows["clean"]["marker_state"] == "grounded"
    assert rows["wrong_record"]["marker_state"] == "grounded"


@pytest.mark.anyio
async def test_model_answer_probes_follow_the_original_marker_and_use_distinct_values():
    evidence = Evidence(
        runs=[RunEvidence("r1", True, "", "total 30\nexcluded 12")],
        values={
            name: DeclaredValue(NamedValue(name=name, value=value), "r1")
            for name, value in [("total", 30), ("excluded", 12)]
        },
    )
    probes = await binding_eval.numeric_probes("Total: {{total}}.", evidence)
    assert [p["id"] for p in probes] == ["clean", "missing", "coincident", "wrong_record"]
    assert [p["legacy_flagged"] for p in probes] == [False, True, False, False]
    assert [p["marker_state"] for p in probes] == ["grounded", "failed", "failed", "grounded"]
    assert await binding_eval.numeric_probes("30 total.", evidence) == []


def test_cost_is_missing_without_a_price_and_counts_cached_input_at_its_own_rate():
    usage = {"input_tokens": 1000, "cache_read_tokens": 200, "output_tokens": 100}
    assert binding_eval.estimate(usage, None) is None
    assert binding_eval.estimate(usage, (1.0, 0.1, 2.0)) == pytest.approx(0.00102)


def test_quote_marker_text_does_not_count_as_a_rubric_answer():
    evidence = Evidence(sources=[Source("s1", "call", "read_fixture", "The method is randomized.")])
    rubric = {"design": r"randomi[sz]ed"}
    quote = '[@s1 "The method is randomized."]'
    assert binding_eval.score_rubric(quote, evidence, rubric) == {"design": False}
    assert binding_eval.score_rubric(f"Randomized study. {quote}", evidence, rubric) == {
        "design": True
    }


@pytest.mark.anyio
async def test_arm_runs_tool_and_reviews_against_the_matching_events(tmp_path, monkeypatch):
    claim = 'Randomized study. [@s1 "The method is randomized."]'
    usage = {
        "input_tokens": 10,
        "output_tokens": 3,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }

    class ScriptedClient(ModelClient):
        async def _call(self, route, messages, tools, on_delta=None):
            if route.role is ModelRole.REVIEWER:
                assert claim in messages[-1].text()
                data = {"claims": [{"text": claim, "kind": "citation"}]}
                return Message.assistant(json.dumps(data)), usage
            assert tools[0]["name"] == "read_fixture"
            if len(messages) == 2:
                call = ToolCallPart(call_id=new_id(), name="read_fixture", args={})
                return Message(role=MessageRole.ASSISTANT, parts=[call]), usage
            result = messages[-1].parts[0]
            assert isinstance(result, ToolResultPart)
            assert "[source s1]" in result.text()
            return Message.assistant(claim), usage

    monkeypatch.setattr(binding_eval, "ModelClient", ScriptedClient)
    router = Router(
        binding_eval.ModelsConfig.from_dict(
            {
                "providers": {"deepseek": {}},
                "roles": {"worker": "deepseek:test"},
            }
        )
    )
    case = binding_eval.Case(
        id="test-case",
        split="heldout",
        question="What was the design?",
        source="The method is randomized.",
        rubric={"design": r"randomi[sz]ed"},
    )
    result = await binding_eval.run_arm(
        tmp_path / "arm", case, router, "default", prices={}, max_steps=4
    )
    assert "error" not in result, result
    assert result["claims"] == {"grounded": 1}
    assert result["markers"] == {"quote_ok": 1}
    assert result["rubric"] == {"design": True}
    assert result["worker_usage"]["calls"] == 2
    assert result["reviewer_usage"]["calls"] == 1
    assert result["worker_usage"]["estimated_usd"] is None
