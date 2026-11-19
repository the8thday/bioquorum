import numpy as np
from evals.v05_geo_analysis import mitochondrial_qc


def test_mitochondrial_qc_records_unobserved_and_measured_libraries():
    eligible = np.array([True, True, False])
    totals = np.array([100, 100, 100])
    passed, _, report = mitochondrial_qc(np.zeros(3), totals, eligible, 10)
    assert passed.all() and report == {"umi": 0, "positive_cells": 0, "excluded": 0}
    passed, _, report = mitochondrial_qc(np.array([1, 10, 20]), totals, eligible, 10)
    assert list(passed) == [True, False, False]
    assert report == {"umi": 31, "positive_cells": 3, "excluded": 1}
