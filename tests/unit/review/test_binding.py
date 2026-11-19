"""Evidence binding (ADR-014): {{name}} and [@source "quote"], resolved without a model."""

import json

import pytest

from bioquorum.core import events as ev
from bioquorum.core.enums import Actor
from bioquorum.core.messages import Message, TextPart, ToolResultPart
from bioquorum.core.refs import NamedValue, QuoteRef, ValueRef
from bioquorum.review.binding import (
    find_bindings,
    format_value,
    ground_claim,
    render,
    unresolved,
)
from bioquorum.review.evidence import DeclaredValue, Evidence, Source, source_aliases

ABSTRACT = (
    "# TYK2 inhibition in psoriasis\nPMID: 31234567\n"
    "Deucravacitinib achieved PASI 75 in 58.7% of patients at week 16,\n"
    "compared with 12.7% on placebo."
)
TRIALS_JSON = json.dumps({"studies": [{"nct": "NCT04167462", "phase": "Phase 3", "n": 666}]})


def evidence() -> Evidence:
    return Evidence(
        values={
            "n_de": DeclaredValue(NamedValue(name="n_de", value=412, unit="genes"), "R1"),
            "p_min": DeclaredValue(NamedValue(name="p_min", value=3.21e-9), "R1"),
            "frac": DeclaredValue(NamedValue(name="frac", value=0.123456), "R2"),
            "top": DeclaredValue(NamedValue(name="top", value="IL23R"), "R2"),
        },
        sources=[
            Source("s1", "C1", "biomcp__article_getter", ABSTRACT),
            Source("s2", "C2", "trials__search", TRIALS_JSON),
        ],
    )


# ----------------------------------------------------------------- values


def test_value_binding_resolves_to_its_run():
    (b,) = find_bindings("We found {{n_de}} genes.", evidence())
    assert b.ok and b.kind == "value" and b.rendered == "412"
    assert b.ref == ValueRef(name="n_de", run_id="R1")


@pytest.mark.parametrize(
    ("value", "spec", "shown"),
    [
        (412, None, "412"),
        (0.123456, None, "0.1235"),
        (3.21e-9, None, "3.21e-09"),
        (123456.7, None, "123457"),
        ("IL23R", None, "IL23R"),
        (0.123456, ".1%", "12.3%"),
        (3.21e-9, ".1e", "3.2e-09"),
        (1234567, ",", "1,234,567"),
    ],
)
def test_format_value(value, spec, shown):
    assert format_value(value, spec) == shown


def test_unknown_name_and_bad_spec_fail():
    a, b, c = find_bindings("{{nope}} {{n_de:x}} {{top:.2f}}", evidence())
    assert not a.ok and "nope" in a.problem
    assert not b.ok and "format" in b.problem
    assert not c.ok  # a text value cannot take a float format


# ----------------------------------------------------------------- quotes


def test_quote_from_alias_and_from_pmid():
    text = (
        'PASI 75 [@s1 "achieved PASI 75 in 58.7% of patients at week 16"] and '
        '[@PMID:31234567 "12.7% on placebo"].'
    )
    a, b = find_bindings(text, evidence())
    assert a.ok and a.ref == QuoteRef(
        source="s1", call_id="C1", quote="achieved PASI 75 in 58.7% of patients at week 16"
    )
    assert b.ok and isinstance(b.ref, QuoteRef) and b.ref.call_id == "C1"


def test_quote_whitespace_is_normalised_but_nothing_else():
    ok = find_bindings('[@s1 "at week 16, compared  with 12.7%"]', evidence())[0]
    assert ok.ok  # the source breaks the line after "16,"
    wrong_case = find_bindings('[@s1 "At Week 16, compared with 12.7%"]', evidence())[0]
    assert not wrong_case.ok
    paraphrase = find_bindings('[@s1 "PASI 75 in 59% of patients"]', evidence())[0]
    assert not paraphrase.ok and "not found" in paraphrase.problem


def test_curly_quotes_are_accepted():
    (b,) = find_bindings("[@s1 “58.7% of patients at week 16”]", evidence())
    assert b.ok


def test_quote_matches_raw_text_and_decoded_json_strings():
    escaped = json.dumps({"abstract": 'IL-23 drives "Th17"\nexpansion in skin lesions.'})
    ev_ = Evidence(sources=[Source("s1", "C1", "lit__get", escaped)])
    # the model reads the decoded text, not the JSON escapes
    (decoded,) = find_bindings('[@s1 \u201cIL-23 drives "Th17" expansion in skin\u201d]', ev_)
    assert decoded.ok
    (raw,) = find_bindings('[@s2 \u201c"n": 666\u201d]', evidence())  # numbers stay raw JSON
    assert raw.ok


def test_quote_rules():
    ev_ = evidence()
    (short,) = find_bindings('[@s1 "PASI"]', ev_)
    assert not short.ok and "short" in short.problem
    (missing,) = find_bindings('[@s9 "achieved PASI 75 in 58.7%"]', ev_)
    assert not missing.ok and "s9" in missing.problem
    (wrong_paper,) = find_bindings('[@PMID:99999999 "achieved PASI 75 in 58.7%"]', ev_)
    assert not wrong_paper.ok  # the quote exists, but not in a result about that paper
    (too_short,) = find_bindings('[@s2 "Phase 3"]', ev_)
    assert not too_short.ok  # 7 characters: below the minimum


def test_quote_by_trial_id_in_json():
    (b,) = find_bindings('[@NCT04167462 “"phase": "Phase 3"”]', evidence())
    assert b.ok and isinstance(b.ref, QuoteRef) and b.ref.call_id == "C2"


# ----------------------------------------------------------------- rendering


def test_render_exact_gives_checks_the_recorded_value():
    text = "p = {{p_min:.1e}}, fraction {{frac}}"
    assert render(text, evidence()) == "p = 3.2e-09, fraction 0.1235"
    assert render(text, evidence(), exact=True) == "p = 3.21e-09, fraction 0.123456"


def test_render_for_checks_and_marks_failures():
    text = 'We found {{n_de}} genes [@s1 "58.7% of patients at week 16"] and {{nope}}.'
    assert render(text, evidence()) == (
        'We found 412 genes "58.7% of patients at week 16" [s1] and {{nope}}.'
    )
    marked = render(text, evidence(), failed=lambda b: f"<!{b.raw}!>")
    assert marked.endswith("<!{{nope}}!>.")
    assert [b.raw for b in unresolved(text, evidence())] == ["{{nope}}"]


# ----------------------------------------------------------------- claims


@pytest.mark.parametrize(
    ("claim", "state"),
    [
        ("1. {{n_de}} genes were differentially expressed (run R1).", "grounded"),
        ('Deucravacitinib [@s1 "achieved PASI 75 in 58.7% of patients at week 16"].', "grounded"),
        ("{{n_de}} genes, top hit {{top}}, in 3 cell types.", "none"),  # a bare number
        ("{{n_de}} genes [PMID:31234567].", "none"),  # a bare citation
        ("TYK2 is a JAK family kinase.", "none"),  # nothing bound: an assertion
        ("{{n_de}} genes and {{nope}}.", "failed"),
        ("IL23R and CD14 lead the list of {{n_de}}.", "grounded"),  # digits inside names
        # labels are identifiers, not reported quantities (seen in a real run, 2026-11-18)
        ("Outcome 0: {{n_de}} patients; mean {{frac:.2f}}.", "grounded"),
        ("In type 2 diabetes, {{n_de}} genes changed.", "grounded"),
        ("IL-23 and COVID-19 signatures share {{n_de}} genes.", "grounded"),
        ("A phase 3 trial enrolled {{n_de}} patients.", "grounded"),
        ("Cluster 3 had {{n_de}} cells in 3 samples.", "none"),  # "3 samples" is a count
        ("Group 12 had {{n_de}} cells.", "none"),  # only small labels are exempt
    ],
)
def test_ground_claim(claim, state):
    g = ground_claim(claim, evidence())
    assert g.state == state, (g.unbound, [b.problem for b in g.bindings])


def test_ground_claim_reports_what_is_unbound():
    g = ground_claim("{{n_de}} genes in 3 cell types [PMID:31234567]", evidence())
    assert g.unbound == ["[PMID:31234567]", "3"]


# ----------------------------------------------------------------- evidence from events


def test_evidence_collects_values_and_sources_from_events(store):
    sid = "S"

    def add(payload, actor=Actor.TOOL):
        store.append(payload, session_id=sid, actor=actor)

    add(ev.UserMessage(message=Message.user("q")), Actor.USER)
    add(ev.RunStarted(run_id="R1", kernel_id="k", code="x", code_hash="0" * 64))
    add(ev.RunFinished(run_id="R1", run_key="k", values=[NamedValue(name="n", value=1)]))
    add(ev.RunStarted(run_id="R2", kernel_id="k", code="y", code_hash="0" * 64))
    add(ev.RunFinished(run_id="R2", run_key="k", values=[NamedValue(name="n", value=2)]))

    def result(call_id, tool, text, is_error=False):
        part = ToolResultPart(call_id=call_id, content=[TextPart(text=text)], is_error=is_error)
        add(ev.ToolCallFinished(call_id=call_id, tool=tool, result=part))

    result("C1", "python_execute", "run output is not a source")
    result("C2", "biomcp__article_getter", ABSTRACT)
    result("C3", "biomcp__search", "error text", is_error=True)
    result("C4", "trials__search", TRIALS_JSON)

    events = store.read(sid)
    e = Evidence.from_events(events)
    assert e.values["n"] == DeclaredValue(NamedValue(name="n", value=2), "R2")  # latest wins
    assert [(s.alias, s.call_id) for s in e.sources] == [("s1", "C2"), ("s2", "C4")]
    assert source_aliases(events) == {"C2": "s1", "C4": "s2"}


def test_quote_may_contain_straight_quotes():
    """A real run (2026-11-18) quoted Open Targets JSON inside straight quotes."""
    marker = '[@s2 ""phase": "Phase 3", "n": 666"]'
    (b,) = find_bindings(f"It was a phase 3 trial {marker}.", evidence())
    assert b.ok and b.ref == QuoteRef(
        source="s2", call_id="C2", quote='"phase": "Phase 3", "n": 666'
    )


def test_malformed_markers_fail_instead_of_passing_as_text():
    for text in ["[@s1 achieved PASI 75]", '[@s1 "achieved PASI 75 in 58.7%]', '[@ s1 "x"]']:
        (b,) = find_bindings(text, evidence())
        assert not b.ok and "malformed" in b.problem, text


def test_quote_cannot_cross_paper_boundaries():
    records = [
        {"pmid": "12345678", "title": "First trial"},
        {"pmid": "23456789", "title": "Second trial"},
    ]
    ev_ = Evidence(sources=[Source("s1", "C1", "article_search", json.dumps(records))])
    assert not find_bindings('[@PMID:12345678 "Second trial"]', ev_)[0].ok


def test_scoped_records_still_bind_the_correct_article():
    records = [
        {"pmid": "12345678", "title": "First trial"},
        {"pmid": "23456789", "title": "Second trial"},
    ]
    ev_ = Evidence(sources=[Source("s1", "C1", "article_search", json.dumps(records))])
    assert find_bindings('[@PMID:23456789 "Second trial"]', ev_)[0].ok
