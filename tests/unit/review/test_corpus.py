"""Regression: deterministic checks on evals/injected-errors/ (ARCHITECTURE.md §6.8).

The expected sets are the measured result. Changing a rule must not lose a caught error
or add a false positive; improving recall means updating CAUGHT on purpose.
"""

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.anyio
ROOT = Path(__file__).parents[3]
spec = importlib.util.spec_from_file_location("review_eval", ROOT / "evals" / "review_eval.py")
assert spec and spec.loader
review_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review_eval)

MISSED = {
    # The value is in the evidence but for the other disease ("CD" means Crohn's): needs
    # domain knowledge -> citation_supports / model checks.
    "value_mismatch": ["ie-004#3"],
    # Key exists but the source is about something else: only a model can tell.
    "fabricated_citation": ["ie-005#4", "ie-017#3"],
}


async def test_corpus_recall_and_false_positives():
    r = await review_eval.score()
    assert r["missed"] == MISSED
    caught = {t: len(v) for t, v in r["caught"].items()}
    assert caught == {
        "value_mismatch": 10,
        "fabricated_citation": 3,
        "execution_unsupported": 5,
        "plan_incomplete": 3,
    }
    assert r["false_positives"] == []
    assert r["clean_flagged"] == []
