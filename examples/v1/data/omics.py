"""Check donor pairing and the prefiltered matrix received from the Python stage."""

import csv
from collections import defaultdict

with open("paired-samples.csv") as stream:
    samples = list(csv.DictReader(stream))
with open("paired-counts.csv") as stream:
    matrix = csv.DictReader(stream)
    assert matrix.fieldnames == ["gene", *(s["sample"] for s in samples)]
    genes = [r["gene"] for r in matrix]
conditions = defaultdict(list)
for sample in samples:
    conditions[sample["donor"]].append(sample["condition"])
assert all(sorted(items) == ["ctrl", "stim"] for items in conditions.values())
assert len(genes) == len(set(genes))
bq.value("donors", len(conditions), unit="donors")  # noqa: F821
bq.value("genes", len(genes), unit="genes")  # noqa: F821
