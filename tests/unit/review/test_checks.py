"""Each rule on fresh examples, not taken from the corpus."""

import pytest

from bioquorum.core.enums import ClaimKind
from bioquorum.review import (
    CitationResolves,
    ClaimInput,
    Evidence,
    ExecutionEvidence,
    PlanComplete,
    ValueMatch,
)
from bioquorum.review.evidence import CitationEvidence, PlanStep, RunEvidence

pytestmark = pytest.mark.anyio
K = ClaimKind


def ev(**kw):
    return Evidence(question=kw.pop("question", "Characterise APOE in Alzheimer's disease."), **kw)


def claim(text, kind=K.NUMERIC, refs=()):
    return ClaimInput(1, text, kind, run_refs=tuple(refs))


async def flags(check, c, e):
    return [f.evidence for f in await check.run([c], e)]


# ----------------------------------------------------------------- value_match

TABLE = RunEvidence(
    "r1", True, "print(df)", "group  n  mean_age\nE4     310  71.2\nE3     402  74.8\n"
)


async def test_value_present_with_rounding_ok():
    e = ev(runs=[RunEvidence("r1", True, "", "odds_ratio 3.684")])
    assert await flags(ValueMatch(), claim("E4 carriers had an odds ratio of 3.68."), e) == []


async def test_value_absent_flagged():
    e = ev(runs=[RunEvidence("r1", True, "", "odds_ratio 3.68")])
    (msg,) = await flags(ValueMatch(), claim("E4 carriers had an odds ratio of 6.38."), e)
    assert "6.38" in msg


async def test_value_only_in_failed_run_flagged():
    e = ev(runs=[RunEvidence("r1", False, "", "odds_ratio 3.68")])
    assert await flags(ValueMatch(), claim("odds ratio 3.68"), e)


async def test_unit_conflict_flagged():
    e = ev(runs=[RunEvidence("r1", True, "", "delta_mmol_l 1.9")])
    (msg,) = await flags(ValueMatch(), claim("LDL fell by 1.9% in carriers."), e)
    assert "not a percentage" in msg


async def test_derived_total_ok():
    e = ev(runs=[TABLE])
    assert await flags(ValueMatch(), claim("The cohort had 712 participants."), e) == []


async def test_table_label_swap_flagged():
    c = claim("Mean age was 71.2 in E3 carriers and 74.8 in E4 carriers.")
    msgs = await flags(ValueMatch(), c, ev(runs=[TABLE]))
    assert len(msgs) == 2 and "row 'E4'" in msgs[0]


async def test_table_reading_in_right_order_ok():
    c = claim("Mean age was 71.2 in E4 carriers and 74.8 in E3 carriers.")
    assert await flags(ValueMatch(), c, ev(runs=[TABLE])) == []


async def test_thresholds_are_not_table_readings():
    run = RunEvidence("r1", True, "", "E4  0.05\nE3  0.30\n")
    c = claim("E3 and E4 were compared at p < 0.05.")
    assert await flags(ValueMatch(), c, ev(runs=[run])) == []


async def test_small_counts_ignored():
    assert await flags(ValueMatch(), claim("We compared 3 cohorts across 2 sites."), ev()) == []


# ----------------------------------------------------------------- execution_evidence

RUNS = [
    RunEvidence("r1", True, "import pandas as pd\ndf = pd.read_csv('adni.csv')", "n 712"),
    RunEvidence("r2", False, "from bq.tools import gwas\ngwas.lookup('rs429358')", "TimeoutError"),
]


async def test_action_claim_without_any_run():
    c = claim("I fitted a Cox model and found HR 1.9 for dementia.", K.EXECUTION)
    (msg,) = await flags(ExecutionEvidence(), c, ev(runs=RUNS[:1]))
    assert "no run" in msg or "printed none" in msg


async def test_action_claim_on_failed_run():
    c = claim("I looked up rs429358 in the GWAS catalogue via gwas lookup.", K.EXECUTION)
    c = ClaimInput(
        1, "I queried the gwas lookup for rs429358 and found strong signal.", K.EXECUTION
    )
    (msg,) = await flags(ExecutionEvidence(), c, ev(runs=RUNS))
    assert "failed" in msg


async def test_supported_action_ok():
    c = claim("I loaded adni.csv with pandas: n = 712.", K.EXECUTION)
    assert await flags(ExecutionEvidence(), c, ev(runs=RUNS)) == []


async def test_explicit_ref_to_missing_run():
    c = claim("Run r9 shows 712 participants.", refs=["r9"])
    (msg,) = await flags(ExecutionEvidence(), c, ev(runs=RUNS))
    assert "does not exist" in msg


async def test_honest_failure_report_ok():
    c = claim("The GWAS lookup in run r2 failed with a timeout, so no association was obtained.")
    assert await flags(ExecutionEvidence(), c, ev(runs=RUNS)) == []


async def test_interpretation_without_results_ok():
    c = claim("These clusters look like microglia and astrocytes.", K.EXECUTION)
    assert await flags(ExecutionEvidence(), c, ev(runs=RUNS)) == []


# ----------------------------------------------------------------- citation_resolves


async def test_citation_keys():
    e = ev(citations=[CitationEvidence("PMID:12345678", "t", "a")])
    assert await flags(CitationResolves(), claim("Shown before [PMID:12345678]."), e) == []
    assert await flags(CitationResolves(), claim("Shown before [pmid 12345678]."), e) == []
    (msg,) = await flags(CitationResolves(), claim("Also shown [PMID:99999999]."), e)
    assert "99999999" in msg


# ----------------------------------------------------------------- plan_complete

PLAN = [
    PlanStep("Load the ADNI cohort table"),
    PlanStep("Fit a Cox model with lifelines"),
    PlanStep("Summarise prior literature on APOE4 penetrance"),
]


async def test_named_tool_step_must_appear_in_a_run():
    e = ev(runs=RUNS[:1], plan=PLAN, answer="APOE4 penetrance literature ...")
    (msg,) = await flags(PlanComplete(), claim("All planned analyses are complete.", K.PLAN), e)
    assert "lifelines" in msg


async def test_plan_done_when_runs_and_answer_cover_it():
    runs = [*RUNS[:1], RunEvidence("r3", True, "from lifelines import CoxPHFitter", "HR 1.9")]
    e = ev(runs=runs, plan=PLAN, answer="Prior literature on APOE4 penetrance ...")
    assert await flags(PlanComplete(), claim("All planned analyses are complete.", K.PLAN), e) == []


async def test_marked_steps_and_honest_gaps():
    e = ev(plan=[PlanStep("Fit a Cox model with lifelines", done=False)])
    assert (
        await flags(PlanComplete(), claim("The lifelines Cox model was not run.", K.PLAN), e) == []
    )
    e2 = ev(plan=[PlanStep("Fit a Cox model with lifelines", done=True)])
    assert await flags(PlanComplete(), claim("Every step is complete.", K.PLAN), e2) == []
