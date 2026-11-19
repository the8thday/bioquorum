"""Offline checks for the research harness rubric and treatment arms."""

import pytest

from bioquorum.core.refs import NamedValue
from bioquorum.review.evidence import DeclaredValue, Evidence, RunEvidence, Source


@pytest.fixture
def harness(monkeypatch):
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).parents[3]))
    from evals import harness_eval

    return harness_eval


def test_correct_numeric_value_and_source_are_graded_separately(harness):
    case = harness.Case(
        id="sample",
        dataset="PlantGrowth",
        split="heldout",
        question="Evaluate the sample.",
        metrics={"Difference": 1.58, "Pairs": 10},
        design={"Result type": r"\bpaired\b"},
        source_url="https://example.com",
        excerpt="The experiment used paired observations.",
    )
    evidence = Evidence(
        runs=[RunEvidence("r1", True, "", "Difference: 1.58")],
        values={"diff": DeclaredValue(NamedValue(name="diff", value=1.58), "r1")},
        sources=[Source("s1", "call", "read_file", case.excerpt)],
    )
    result = harness.grade(
        case,
        "Difference: {{diff}} hours\nPairs: 10\n"
        'Result type: paired [@s1 "The experiment used paired observations."]',
        evidence,
    )
    assert result == {
        "Difference": {"correct": True, "bound": True},
        "Pairs": {"correct": True, "bound": False},
        "Result type": {"correct": True, "bound": True},
    }
    wrong = harness.grade(
        case,
        'Difference: -1.58\nResult type: unpaired [@s1 "The experiment used paired observations."]',
        evidence,
    )
    assert wrong["Difference"] == {"correct": False, "bound": False}
    assert wrong["Result type"] == {"correct": False, "bound": False}


def test_bare_arm_offers_only_kernel_and_has_no_retry(harness):
    tools = harness.registry_for("bare")
    assert [tool.name for tool in tools] == ["python_execute"]
    assert harness.ARMS["bare"] == (False, 0)
    assert harness.ARMS["full"] == (True, 0)
    assert harness.ARMS["full-retry"] == (True, 1)
