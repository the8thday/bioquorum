from bioquorum.review.text import (
    mentions,
    normalise,
    numbers,
    proper_names,
    same_value,
)


def vals(text):
    return [(n.value, n.unit) for n in numbers(normalise(text))]


def test_identifiers_are_not_numbers():
    assert vals("CD14 and IL23R in GSE9100417, rs123 and NCT0999") == []


def test_ranges_units_and_signs():
    assert vals("HR 0.77 (95% CI 0.61–0.96)") == [(0.77, ""), (95.0, "%"), (0.61, ""), (0.96, "")]  # noqa: RUF001
    assert vals("35-39% versus 3-6%") == [(35.0, "%"), (39.0, "%"), (3.0, "%"), (6.0, "%")]
    assert vals("logFC = -1.5") == [(-1.5, "")]
    assert vals("1,248 cells") == [(1248.0, "")]


def test_scientific_notation_spellings_agree():
    a = vals("p = 3.1 × 10^-32")  # noqa: RUF001
    b = vals("p = 3.1 x 10⁻³²")
    c = vals("p_value 3.1e-32")
    assert a == b == c == [(3.1e-32, "")]


def test_units_from_labels():
    assert vals("difference_mg_dl 38.5") == [(38.5, "mg/dl")]
    assert vals("ldl 120 mg/dL") == [(120.0, "mg/dl")]


def test_rounding_tolerance():
    claim, ev = numbers("0.68")[0], numbers("0.6812")[0]
    assert same_value(claim, ev)
    assert not same_value(numbers("0.69")[0], ev)
    assert not same_value(numbers("0.6812")[0], numbers("0.68")[0])  # more precise than evidence


def test_proper_names_and_mentions():
    assert proper_names("Check gnomAD constraint and run Harmony via Open Targets") == [
        "gnomAD",
        "Harmony",
        "Open Targets",
    ]
    assert mentions("Open Targets", "from bq.tools import opentargets")
    assert not mentions("Harmony", "sc.pp.neighbors(adata)")
