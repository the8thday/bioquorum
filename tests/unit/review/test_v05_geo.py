"""GEO analysis controls avoid positional joins and cell-level pseudoreplication."""

import numpy as np
import pandas as pd
import pytest
from evals.v05_geo_analysis import adjust_bh, align_metadata, author_barcodes, paired_effects


def test_metadata_matches_by_barcode_and_checks_condition():
    metadata = pd.DataFrame({"stim": ["ctrl", "ctrl"], "ind": ["B", "A"]}, index=["two-1", "one-1"])
    ordered = align_metadata(metadata, ["one-1", "two-1"], "ctrl")
    assert ordered["ind"].tolist() == ["A", "B"]
    with pytest.raises(ValueError, match="barcode"):
        align_metadata(metadata, ["one-1", "absent-1"], "ctrl")
    with pytest.raises(ValueError, match="condition"):
        align_metadata(metadata, ["one-1"], "stim")


def test_paired_randomization_uses_the_number_of_donors():
    differences = np.array([[2.0, 1.0], [2.0, -1.0], [2.0, 0.0]])
    effect, p = paired_effects(differences)
    np.testing.assert_allclose(effect, [2.0, 0.0])
    np.testing.assert_allclose(p, [0.25, 1.0])
    np.testing.assert_allclose(adjust_bh(np.array([0.01, 0.06, 0.03])), [0.03, 0.06, 0.045])


def test_kang_author_suffixes_are_resolved_within_library():
    metadata = pd.DataFrame({"stim": ["ctrl", "stim"]}, index=["shared-1", "shared-11"])
    assert author_barcodes(metadata, ["shared-1"], "ctrl") == ["shared-1"]
    assert author_barcodes(metadata, ["shared-1"], "stim") == ["shared-11"]


def test_paired_counts_keep_integer_counts_and_sample_ids(tmp_path):
    from evals.v05_geo_analysis import write_paired_counts

    ctrl = np.array([[0, 1], [2, 3]])
    stim = np.array([[4, 5], [6, 7]])
    write_paired_counts(tmp_path, ["a", "b"], ["g1", "g2"], ctrl, stim)
    counts = pd.read_csv(tmp_path / "paired-counts.csv", index_col=0)
    samples = pd.read_csv(tmp_path / "paired-samples.csv", index_col=0)
    assert list(counts.columns) == list(samples.index)
    assert samples.loc["b_stim"].tolist() == ["b", "stim"]
    assert counts.loc["g2", "b_stim"] == 7 and counts.dtypes.eq("int64").all()
